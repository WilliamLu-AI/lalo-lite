import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from .settings import ROOT
from .diagnostics import Journal
from .images import validate_image
from async_timeout import timeout
from .audio import Segmenter
from .codex import CodexContext
from .claude import ClaudeContext
from .explanation.backends import BackendBinding, BackendRouter
from .explanation.follow import locate
from .providers import ProviderError
from .streaming import is_backchannel
from .explanation.stream import explain, prepared_answer, Started, Navigation, Sentence
from .voice.factory import create_runtime
from .transport.websocket import WebSocketAudioOutput
from .streaming_asr import StreamingASR

from .explanation.agent import ExplanationAgent, INSTRUCTIONS


class Session:
    def __init__(self, ws, providers, settings, tad=None, *, audio_output_factory=WebSocketAudioOutput):
        self.ws, self.providers, self.settings, self.tad = ws, providers, settings, tad
        self.id = uuid.uuid4().hex
        self.journal = Journal(self.id)
        self.epoch = 0
        self.task = self.endpoint_task = None
        self.follow_task = None
        self.view_revision = 0
        self.followed_sentence = -1
        self.explicit_navigation = False
        self.segmenter = Segmenter()
        self.blocks, self.history = [], []
        self.background = ''
        self.view = {}
        self.pending_advance = None
        self.playback_epoch = None
        self.source = ''
        self.mode = 'cloud'
        self.host = 'codex'
        self.cwd = ROOT
        self.send_lock = asyncio.Lock()
        self.live = True
        self.stage = 'idle'
        self.asr = None
        self.recognition_task = None
        self.latest_partial = ''
        self.pending_prefix = ''
        self.holding = False
        self.sentences = []
        self.heard = -1
        self.resume_note = ''
        self.resuming = False
        self.metrics = {}
        self.paragraph_target = None
        self.image_target = None
        self.explicit_navigation = False
        self.voice_store = None
        self.voice_providers = None
        self.base_providers = providers
        self.runtime_name = settings.get('VOICE_RUNTIME') or 'native'
        self.runtime = None
        self.audio_output_factory = audio_output_factory
        self.explanation_agent = ExplanationAgent()
        self.backend_router = BackendRouter(settings, self.send,
            codex_factory=CodexContext, claude_factory=ClaudeContext,
            audit=lambda kind, **fields: self.journal.record(kind, self.epoch, self.stage, self.mode, **fields))

    async def send(self, kind, *, expected_epoch=None, **data):
        if expected_epoch is not None and expected_epoch != self.epoch:
            return
        if kind not in ('audio', 'pong'):
            self.journal.record(kind, self.epoch, self.stage, self.mode, **data)
        if kind in ('turn_started', 'endpoint', 'audio_end', 'error', 'interrupted'):
            logging.getLogger('uvicorn.error').info(
                'presenter session=%s epoch=%s stage=%s event=%s code=%s',
                self.id, self.epoch, self.stage, kind, data.get('code', '-'))
        if kind == 'audio_end':
            logging.getLogger('uvicorn.error').info('presenter latency session=%s epoch=%s timings=%s',
                self.id, self.epoch, json.dumps(data.get('timings', {})))
        async with self.send_lock:
            if self.live and (expected_epoch is None or expected_epoch == self.epoch):
                await self.ws.send_json({'type': kind, 'session': self.id, 'epoch': self.epoch, **data})

    async def cancel(self, reason):
        self.journal.record('cancel_requested', self.epoch, self.stage, self.mode, reason=reason)
        if self.playback_epoch is not None and self.history and self.history[-1]['role'] == 'assistant':
            self.history[-1]['content'] += '\n[Playback interrupted; heard complete sentences: ' + ''.join(self.sentences[:self.heard + 1]) + ']'
        if self.sentences and (not self.resume_note or self.resuming):
            self.resume_note = json.dumps({'heard': ''.join(self.sentences[:self.heard + 1]),
                'remaining': ''.join(self.sentences[self.heard + 1:]), 'instruction':
                'On resume continue from the first unacknowledged sentence, without claiming the user heard it.'}, ensure_ascii=False)
        self.sentences, self.heard = [], -1
        self.followed_sentence = -1
        self.epoch += 1
        self.pending_advance = self.playback_epoch = None
        if self.runtime:
            await self.runtime.cancel()
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
        if self.follow_task:
            self.follow_task.cancel()
            await asyncio.gather(self.follow_task, return_exceptions=True)
            self.follow_task = None
        await self.backend_router.cancel(reason)
        await self.send('interrupted', reason=reason)

    async def configure(self, data):
        await self.stop_recognition()
        await self.cancel('configuration')
        self.resume_note = ''
        if self.runtime is None:
            self.runtime = create_runtime(self.runtime_name)
        if self.endpoint_task:
            self.endpoint_task.cancel()
            await asyncio.gather(self.endpoint_task, return_exceptions=True)
        self.segmenter = Segmenter()
        blocks = data.get('blocks', [])
        if not isinstance(blocks, list) or len(blocks) > 2000:
            raise ValueError('Document supports at most 2000 blocks')
        normalized = []
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get('id'), str) or not isinstance(block.get('text'), str):
                raise ValueError('Invalid document block')
            normalized.append({'id': block['id'][:100], 'text': block['text'][:12000],
                               'heading': bool(block.get('heading'))})
        image_number=0
        for original, block in zip(blocks, normalized):
            if original.get('imageIndex'):
                image_number+=1
                block.update(imageIndex=image_number, image=validate_image(original.get('image','')))
        if sum(len(b.get('image','')) for b in normalized)>2_000_000:
            raise ValueError('Document images exceed 2 MB limit')
        if sum(len(b['text']) for b in normalized) > 100000:
            raise ValueError('Document exceeds 100000 characters')
        if len({b['id'] for b in normalized}) != len(normalized):
            raise ValueError('Duplicate block IDs')
        self.blocks = normalized
        self.background = str(data.get('background', ''))[:50000]
        self.mode = data.get('mode') if data.get('mode') in ('codex', 'claude', 'tiered') else 'cloud'
        self.source = str(data.get('source', ''))[:100]
        self.host = data.get('host') if data.get('host') in ('claude', 'codex') else 'codex'
        self.codex_model = data.get('codexModel','')
        if (self.mode == 'codex' or (self.mode == 'tiered' and self.host == 'codex')) and self.codex_model not in ('','gpt-6.1-sol','gpt-6-astra'):
            raise ValueError('Unsupported Codex model')
        if self.mode == 'claude' or (self.mode == 'tiered' and self.host == 'claude'):
            try:
                command = json.loads(self.settings.get('CLAUDE_COMMAND'))
                if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
                    raise ValueError
                if not os.path.isfile(command[0]) and not shutil.which(command[0]):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                raise ValueError('CLAUDE_COMMAND 未配置或格式错误：请在 .env 中设为 JSON 数组，如 ["claude 可执行文件路径"]')
        self.cwd = Path(data.get('cwd') or ROOT).resolve()
        if not self.cwd.is_dir():
            raise ValueError('Project directory does not exist')
        if self.voice_store:
            from .model_adapters.voice import VoiceProviders
            profiles = self.voice_store.resolve(data.get('voiceProfiles'))
            replacement = await VoiceProviders(self.base_providers, self.settings, profiles).init()
            previous = self.voice_providers
            self.voice_providers = replacement
            self.providers = replacement
            if previous: await previous.close()
            self.journal.record('voice_configuration', self.epoch, self.stage, self.mode,
                                profiles={k:{'id':p['id'],'provider':p['provider'],'model':p['model']} for k,p in profiles.items()})
        self.history = []
        self.journal.record('binding', self.epoch, self.stage, self.mode, source=self.source, host=self.host)
        await self.send('configured', voiceRuntime=self.runtime_name, mode=self.mode, source=self.source, host=self.host, codexModel=self.codex_model, configId=data.get('configId'), blocks=len(self.blocks),
                        locator='flash' if self.mode == 'tiered' else ('jev' if self.settings.get('FOLLOW_LOCATOR') == 'jev' else 'model'))

    async def start_turn(self, text=None, pcm=None):
        self.journal.record('input_received', self.epoch, self.stage, self.mode, text=text, audioBytes=len(pcm) if pcm else 0)
        await self.cancel('new_turn')
        ticket = self.epoch
        self.task = asyncio.create_task(self.run_turn(ticket, text, pcm))

    async def run_turn(self, ticket, text, pcm):
        turn_send = self.turn_sender(ticket)
        start = time.perf_counter()
        self.stage = 'asr' if pcm is not None else 'model'
        try:
            async with timeout(300):
                await turn_send('turn_started')
                if pcm is not None:
                    await turn_send('status', message='正在识别')
                    text = await self.providers.transcribe(pcm)
                if not text or text.strip() in ('[识别内容为空]', ''):
                    await turn_send('turn_empty')
                    await turn_send('status', message='未识别到内容，继续聆听')
                    return
                await turn_send('transcript', text=text)
                turn = self.explanation_agent.prepare(
                    question=text, blocks=self.blocks, view=self.view, history=self.history,
                    background=self.background, resume_note=self.resume_note,
                    mode=self.mode, host=self.host, follow_locator=self.settings.get('FOLLOW_LOCATOR'))
                self.explicit_navigation = turn.explicit_navigation
                self.paragraph_target, self.image_target = turn.paragraph_target, turn.image_target
                self.resuming = turn.resuming
                sequential, jev_follow, messages = turn.sequential, turn.jev_follow, turn.messages
                if self.image_target:
                    await turn_send('navigate_current', blockId=self.image_target['id'], force=not sequential)
                self.stage = 'model'
                await turn_send('status', message='正在思考')
                backend = await self.backend_router.resolve(
                    BackendBinding(self.mode, self.host, self.source, self.cwd, self.codex_model), self.providers, notify=turn_send)
                self.metrics = {'started': start}
                self.history.append({'role': 'user', 'content': text})
                self.playback_epoch = ticket
                if jev_follow:
                    answer, count = await backend.answer(messages), None
                else:
                    answer, count = await self.stream_answer(backend, messages, ticket, jev_follow)
                normalized = self.explanation_agent.normalize(answer, turn=turn, blocks=self.blocks, view=self.view)
                speech = normalized['speech']
                if jev_follow and self.view.get('follow', True):
                    self.stage = 'locator'
                    decision = await self.providers.locator.locate(
                        self.blocks, text, speech, self.view.get('current'))
                    if ticket != self.epoch:
                        return
                    normalized = self.explanation_agent.normalize(
                        answer, turn=turn, blocks=self.blocks, view=self.view, location=decision)
                    await turn_send('location', **decision)
                if ticket != self.epoch:
                    return
                answer = normalized
                target, advance = answer['blockId'], answer['advanceTo']
                self.pending_advance = advance
                await turn_send('answer', text=speech, blockId=target, force=self.explicit_navigation)
                if count is None:
                    async def record_sentence(event):
                        if ticket != self.epoch:
                            raise asyncio.CancelledError
                        if isinstance(event, Sentence):
                            self.sentences.append(event.text)
                    _, count = await self.runtime.run(
                        prepared_answer(answer), self.synthesize,
                        self.audio_output(ticket), record_sentence)
                self.history.append({'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)})
                self.history = self.history[-12:]
                await turn_send('audio_end', elapsedMs=round((time.perf_counter() - start) * 1000), bytes=count,
                    timings={k: round((v - start) * 1000) for k, v in self.metrics.items() if k != 'started'})
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.pending_advance = self.playback_epoch = None
            await self.discard_backend()
            await turn_send('error', message='请求超时，请检查网络或代理后重试', stage=self.stage, code='timeout')
        except Exception as exc:
            self.stage = getattr(exc, 'stage', None) or self.stage
            self.pending_advance = self.playback_epoch = None
            await self.discard_backend()
            # Deliberately bounded errors; SDK exception strings can contain request data.
            message = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
            await turn_send('error', message=message, stage=self.stage, code=type(exc).__name__)

    @property
    def codex(self):
        # Compatibility for existing diagnostics; lifecycle belongs to the router.
        return self.backend_router.current

    @codex.setter
    def codex(self, value):
        self.backend_router.current = value

    async def discard_backend(self):
        await self.backend_router.close()

    async def stream_answer(self, backend, messages, ticket, jev_follow):
        visible = ''

        async def business(event):
            nonlocal visible
            if ticket != self.epoch:
                raise asyncio.CancelledError
            if isinstance(event, Started):
                self.metrics.setdefault('firstTokenMs', time.perf_counter())
            elif isinstance(event, Navigation) and not jev_follow:
                nav = (self.image_target or {}).get('id') or self.paragraph_target or event.block_id
                if nav in {b['id'] for b in self.blocks}:
                    await self.send('navigate_current', expected_epoch=ticket, blockId=nav, force=self.explicit_navigation)
            elif isinstance(event, Sentence):
                self.sentences.append(event.text)
                visible += event.text
                await self.send('answer_partial', expected_epoch=ticket, text=visible)

        return await self.runtime.run(explain(backend, messages), self.synthesize,
                                      self.audio_output(ticket), business)

    async def synthesize(self, text):
        self.stage = 'tts'
        source = self.providers.speech(text)
        try:
            async for chunk in source:
                yield chunk
        except Exception as error:
            if isinstance(error, ProviderError):
                error.stage = 'tts'
                raise
            raise ProviderError(type(error).__name__, stage='tts') from error
        finally:
            await source.aclose()

    def turn_sender(self, ticket):
        async def emit(kind, **data):
            await self.send(kind, expected_epoch=ticket, **data)
        return emit

    def audio_output(self, ticket):
        emit = self.turn_sender(ticket)

        async def before_audio():
            self.stage = 'tts'
            if 'firstAudioMs' not in self.metrics:
                await emit('status', message='正在播报')
            self.metrics.setdefault('firstAudioMs', time.perf_counter())
        return self.audio_output_factory(emit, lambda: ticket == self.epoch and self.live, before_audio)

    async def stop_recognition(self, preserve=False):
        if preserve:
            if self.recognition_task and not self.recognition_task.done() and self.latest_partial.strip():
                self.pending_prefix = (self.pending_prefix + ' ' + self.latest_partial.strip()).strip()[:4000]
                self.journal.record('recognition_segment_preserved', self.epoch, self.stage, self.mode, text=self.pending_prefix)
        else:
            self.pending_prefix = ''
        self.latest_partial = ''
        if self.recognition_task:
            self.recognition_task.cancel()
            await asyncio.gather(self.recognition_task, return_exceptions=True)
            self.recognition_task = None
        if self.asr:
            await self.asr.close()
            self.asr = None
        self.holding = False

    async def partial(self, text):
        self.latest_partial = text
        await self.send('transcript_partial', text=text)
        if self.holding and text.strip() and not is_backchannel(text):
            self.holding = False
            await self.cancel('speech_start')

    async def recognize(self, pcm, asr):
        try:
            try:
                text = await asr.finish() if asr else await self.providers.transcribe(pcm)
            except Exception:
                await self.send('status', message='流式识别暂不可用，正在用整句识别恢复')
                text = await self.providers.transcribe(pcm)
            text = (self.pending_prefix + ' ' + text).strip()
            self.pending_prefix = ''
            self.latest_partial = ''
            if self.holding and (not text.strip() or is_backchannel(text)):
                self.holding = False
                await self.send('backchannel', text=text)
                await self.send('playback_resume')
            elif text.strip():
                self.holding = False
                await self.start_turn(text=text)
            else:
                await self.send('status', message='未识别到内容，继续聆听')
        except asyncio.CancelledError:
            raise
        except Exception:
            self.holding = False
            await self.send('playback_resume')
            await self.send('error', message='语音识别失败，请重试', stage='asr', code='recognition')
        finally:
            if asr:
                await asr.close()

    async def finish_speech(self):
        pcm = self.segmenter.take()
        self.segmenter = Segmenter()
        asr, self.asr = self.asr, None
        self.recognition_task = asyncio.create_task(self.recognize(pcm, asr))

    async def audio(self, frame):
        action = self.segmenter.feed(frame)
        if action == 'start':
            self.journal.record('speech_detected', self.epoch, self.stage, self.mode)
            await self.stop_recognition(preserve=True)
            self.holding = bool((self.task and not self.task.done()) or self.playback_epoch is not None)
            if self.holding:
                await self.send('playback_hold')
            await self.send('status', message='正在聆听')
            if self.voice_providers:
                self.asr = self.voice_providers.start_asr(self.partial, self.segmenter.frames)
            elif self.settings.get('STREAMING_ASR') != 'off':
                self.asr = StreamingASR(self.settings, self.partial)
                self.asr.start(self.segmenter.frames)
        else:
            if self.asr and self.segmenter.active:
                self.asr.feed(frame)
            if action == 'limit':
                await self.finish_speech()
            elif action == 'endpoint' and (not self.endpoint_task or self.endpoint_task.done()):
                self.endpoint_task = asyncio.create_task(self.endpoint())

    async def endpoint(self):
        segment = self.segmenter
        count = len(segment.frames)
        pcm = b''.join(segment.frames)
        try:
            probability = await asyncio.to_thread(self.tad.probability, pcm) if self.tad else 1.0
        except Exception:
            segment.take()
            await self.stop_recognition()
            await self.send('playback_resume')
            await self.send('error', message='话轮检测失败，请重试或检查本地模型')
            return
        # Speech may resume while the CPU model is running.
        if self.segmenter is not segment or not segment.active or len(segment.frames) < count or segment.silent < 35:
            return
        if probability >= .5 or segment.silent >= 125:
            await self.send('endpoint', probability=round(probability, 3), forced=probability < .5)
            await self.finish_speech()

    async def follow_spoken(self, index):
        if self.mode != 'tiered' or not self.view.get('follow', True) or self.image_target or self.paragraph_target or self.explicit_navigation:
            return
        if not 0 <= index < len(self.sentences) or index <= self.followed_sentence:
            return
        if self.follow_task:
            self.follow_task.cancel()
        self.followed_sentence = index
        ticket, revision = self.epoch, self.view_revision
        speech = self.sentences[index]
        async def resolve():
            try:
                async with timeout(5):
                    target = await locate(self.providers, self.blocks, speech, self.view.get('current'), self.view.get('selection',''))
                if ticket == self.epoch and revision == self.view_revision and self.view.get('follow',True) and target:
                    await self.send('navigate_current', expected_epoch=ticket, blockId=target)
            except asyncio.CancelledError:
                raise
            except Exception:
                if ticket == self.epoch:
                    await self.send('location', status='flash_unavailable', blockId=None)
        self.follow_task = asyncio.create_task(resolve())

    async def message(self, data):
        kind = data.get('type')
        if kind in ('sentence_played', 'playback_done'):
            self.journal.record('client_' + kind, self.epoch, self.stage, self.mode, receivedEpoch=data.get('epoch'), sentence=data.get('sentence'))
        if kind == 'client_event':
            event = data.get('event')
            if event == 'navigation_applied':
                fields={k:data.get(k) for k in ('blockId','follow','force','visible','before','after','targetTop','targetBottom','readerTop','readerBottom','build')}
                self.journal.record(event,self.epoch,self.stage,self.mode,**fields)
            if event == 'output_started':
                await self.follow_spoken(self.heard + 1)
            if event in ('mic_started', 'mic_stopped', 'mic_failed', 'client_failed', 'output_started', 'output_blocked'):
                self.journal.record(event, self.epoch, self.stage, self.mode)
            return
        if kind == 'configure':
            try:
                await self.configure(data)
            except ValueError as exc:
                await self.send('error', message=str(exc), stage='configuration', code='configuration')
        elif kind == 'view':
            if data.get('current') != self.view.get('current') or data.get('selection','') != self.view.get('selection','') or data.get('follow',True) != self.view.get('follow',True):
                self.view_revision += 1
            self.view = {'current': data.get('current'), 'selection': str(data.get('selection', ''))[:3000],
                         'follow': data.get('follow', True)}
        elif kind == 'sentence_played' and data.get('epoch') == self.epoch:
            index = data.get('sentence')
            if type(index) is int and index == self.heard + 1 and index < len(self.sentences):
                self.heard = index
                if self.runtime:
                    self.runtime.sentence_played(index)
                await self.follow_spoken(index + 1)
        elif kind == 'text':
            await self.stop_recognition()
            self.segmenter = Segmenter()
            text = str(data.get('text', '')).strip()
            if text and len(text) <= 4000:
                await self.start_turn(text=text)
        elif kind == 'interrupt':
            await self.stop_recognition()
            self.segmenter = Segmenter()
            await self.cancel('user')
        elif kind == 'playback_done' and data.get('epoch') == self.epoch == self.playback_epoch:
            if self.runtime:
                self.runtime.playback_done()
            if self.pending_advance:
                await self.send('navigate', blockId=self.pending_advance)
            self.pending_advance = self.playback_epoch = None
            self.sentences, self.heard = [], -1
            self.followed_sentence = -1
            if self.resuming:
                self.resume_note = ''
            await self.send('status', message='继续聆听')
        elif kind == 'ping':
            await self.send('pong')

    async def close(self):
        self.live = False
        await self.stop_recognition()
        if self.endpoint_task:
            self.endpoint_task.cancel()
            await asyncio.gather(self.endpoint_task, return_exceptions=True)
        await self.cancel('closed')
        if self.runtime:
            await self.runtime.close()
        if self.voice_providers:
            await self.voice_providers.close()
            self.voice_providers = None

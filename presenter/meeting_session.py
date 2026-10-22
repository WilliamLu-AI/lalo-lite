"""One existing Lalo explanation session for a shared presentation."""
import asyncio
import secrets
import re
from .streaming import is_backchannel

from .session import Session
from .transport.websocket import WebSocketAudioOutput

PRESENT_CURRENT = '\u8bf7\u9010\u6bb5\u8bb2\u89e3\u5f53\u524d\u6bb5\u843d\uff0c\u8bb2\u5b8c\u540e\u524d\u8fdb\u5230\u4e0b\u4e00\u6bb5\u3002'


class MeetingSession:
    def __init__(self, meeting, providers, settings, send, *, audio_output_factory=WebSocketAudioOutput,
                 voice_store=None):
        self.meeting = meeting
        self._send = send
        self._session = Session(self, providers, settings, audio_output_factory=audio_output_factory)
        self._session.voice_store = voice_store
        self._lock = asyncio.Lock()
        self._active_id = None
        self._generated_epoch = None
        self._presentation_checkpoint = None
        self._speaker = None
        self._host_candidate = None
        self._last_answer = ""

    async def _hold_presentation(self, state):
        if not state['active_question'] and (state['phase'] == 'presenting' or self._session.resume_note):
            await self._session.cancel('question')
            self._presentation_checkpoint = (state['current'], self._session.resume_note)
            self._session.resume_note = ''
            self._generated_epoch = None

    async def start(self, identity):
        state = self.meeting.snapshot(identity)
        await self._session.configure({**self.meeting.execution_configuration(), 'blocks': state['blocks']})
        await self._session.message({'type': 'view', 'current': state['current'], 'follow': False})

    async def send_json(self, event):
        if event['type'] in ('configured', 'context_ready'):
            return
        if event['type'] == 'transcript' and self._speaker:
            event = {**event, 'speaker': self._speaker,
                     'speaker_role': self.meeting.snapshot(self._speaker)['role']}
        if event['type'] == 'error':
            self._host_candidate = None
            self.meeting.execution_failed()
            self._active_id = None
            self._generated_epoch = None
        if event['type'] in ('answer', 'answer_partial'):
            self._last_answer = event.get('text', '')
        if event['type'] == 'audio_end':
            self._generated_epoch = event['epoch']
        if event['type'] in ('navigate', 'navigate_current') and event.get('blockId'):
            self.meeting.explanation_position(event['blockId'])
        await self._send(event)

    async def _run_active(self, identity, resume=False):
        state = self.meeting.snapshot(identity)
        question = state['active_question']
        if state['phase'] == 'answering' and question and question['id'] != self._active_id:
            self._active_id = question['id']
            self._speaker = question['speaker']
            self._generated_epoch = None
            if not resume:
                await self._session.cancel('question')
                self._session.resume_note = ''
            text = 'Continue from the first unheard sentence.' if resume and self._session.resume_note else question['text']
            await self._session.start_turn(text=text)

    async def _present(self, identity, resume=False):
        state = self.meeting.snapshot(identity)
        if state['phase'] != 'presenting':
            return
        self._speaker = identity
        if self._presentation_checkpoint is not None:
            current, note = self._presentation_checkpoint
            self._presentation_checkpoint = None
            await self._session.cancel('presentation_resume')
            self._session.resume_note = note
            self.meeting.explanation_position(current)
            state = self.meeting.snapshot(identity)
        self._generated_epoch = None
        await self._session.message({'type': 'view', 'current': state['current'], 'follow': False})
        text = 'Continue from the first unheard sentence.' if resume and self._session.resume_note else PRESENT_CURRENT
        await self._session.start_turn(text=text)

    async def command(self, identity, message):
        async with self._lock:
            state = self.meeting.snapshot(identity)
            kind = message.get('type')
            if kind in ('ask','request_speech','navigate','stop','resume') and self._host_candidate:
                token, epoch = self._host_candidate
                self._host_candidate = None
                await self._session.send('playback_restore', expected_epoch=epoch, candidate=token)
            if kind == 'ask':
                self.meeting.ask(identity, message.get('text'))
                await self._hold_presentation(state)
                await self._run_active(identity)
            elif kind == 'host_speech_start':
                if state['role'] != 'host':
                    raise PermissionError('Only the host can use continuous listening')
                question = state['active_question']
                if question and question.get('kind') == 'speech' and question['speaker'] != identity:
                    return None
                token = message.get('speech_id') or secrets.token_urlsafe(18)
                self._host_candidate = (token, self._session.epoch)
                await self._session.send('playback_duck', candidate=token)
                return token
            elif kind == 'host_speech_cancel':
                if state['role'] != 'host':
                    raise PermissionError('Only the host can cancel continuous input')
                if self._host_candidate and self._host_candidate[0] == message.get('speech_id'):
                    token, epoch = self._host_candidate
                    self._host_candidate = None
                    await self._session.send('playback_restore', expected_epoch=epoch, candidate=token)
            elif kind == 'request_speech':
                self.meeting.request_speech(identity)
                await self._hold_presentation(state)
                if state['phase'] == 'presenting':
                    await self._session.cancel('speaking_request')
                    self._active_id = None
                    self._generated_epoch = None
            elif kind == 'cancel_speech':
                self.meeting.cancel_speech(identity, message.get('speech_id'))
                await self._run_active(identity)
            elif kind == 'navigate':
                self.meeting.navigate(identity, message.get('block_id'))
                self.meeting.stop(identity)
                await self._session.cancel('host_navigation')
                self._session.resume_note = ''
                self._presentation_checkpoint = None
                self._active_id = None
                self._generated_epoch = None
            elif kind == 'stop':
                self.meeting.stop(identity)
                await self._session.cancel('user')
                self._active_id = None
                self._generated_epoch = None
            elif kind == 'resume':
                self.meeting.resume(identity)
                await self._run_active(identity, resume=True)
                await self._present(identity, resume=True)
            elif kind in ('sentence_played', 'playback_done'):
                if state['role'] != 'host':
                    raise PermissionError('Only host playback advances the meeting')
                if message.get('epoch') != self._session.epoch:
                    return
                if kind == 'sentence_played':
                    await self._session.message(message)
                elif (self._generated_epoch == self._session.epoch
                      and self._session.sentences
                      and self._session.heard == len(self._session.sentences) - 1):
                    advance = self._session.pending_advance
                    await self._session.message(message)
                    if state['phase'] == 'answering':
                        self.meeting.answer_finished(self._active_id)
                    self._active_id = None
                    self._generated_epoch = None
                    await self._run_active(identity)
                    if state['phase'] == 'presenting':
                        if advance:
                            await self._present(identity)
                        else:
                            self.meeting.stop(identity)
            else:
                raise ValueError('Unsupported meeting execution command')

    async def aclose(self):
        await self._session.close()

    async def submit_audio(self, identity, request_id, pcm):
        generation = self.prepare_audio(identity, request_id, pcm)
        await self.recognize_audio(identity, request_id, generation, pcm)

    def prepare_audio(self, identity, request_id, pcm):
        if not isinstance(pcm, bytes) or not 3200 <= len(pcm) <= 1920000 or len(pcm) % 2:
            raise ValueError('Speech must be PCM16 mono at 16kHz, between 0.1 and 60 seconds')
        if self.meeting.snapshot(identity)['role'] == 'host' and self._host_candidate and self._host_candidate[0] == request_id:
            return ('candidate', *self._host_candidate)
        return self.meeting.begin_recognition(identity, request_id)

    async def recognize_audio(self, identity, request_id, generation, pcm):
        if isinstance(generation, tuple) and generation[0] == 'candidate':
            return await self._recognize_candidate(identity, request_id, generation[2], pcm)
        try:
            text = await asyncio.wait_for(self._session.providers.transcribe(pcm), 60)
        except Exception:
            self.meeting.finish_recognition(identity, request_id, generation, None)
            raise
        async with self._lock:
            if self.meeting.finish_recognition(identity, request_id, generation, text):
                await self._run_active(identity)

    async def _recognize_candidate(self, identity, token, epoch, pcm):
        try:
            text = await asyncio.wait_for(self._session.providers.transcribe(pcm), 12)
        except BaseException:
            async with self._lock:
                if self._host_candidate == (token, epoch):
                    self._host_candidate = None
                    await self._session.send('playback_restore', expected_epoch=epoch, candidate=token)
            raise
        async with self._lock:
            if self._host_candidate != (token, epoch):
                return
            self._host_candidate = None
            await self._session.send('playback_restore', expected_epoch=epoch, candidate=token)
            text = text.strip()
            normalized = re.sub(r'[\s，。！？,.!?、]', '', text)
            acknowledgement = is_backchannel(text) or bool(re.fullmatch(r'[嗯啊呃唔额哦噢]{1,6}', normalized)) or normalized in {'哎','咳','咳咳'}
            # A short acknowledgement may be an answer to an explicit assistant question.
            expecting_answer = self._last_answer.rstrip().endswith(('?', '？'))
            if not text or (acknowledgement and not expecting_answer):
                return
            state = self.meeting.snapshot(identity)
            if normalized in {'停','暂停','停一下','暂停一下','等一下','等等','停止讲解','别说了'}:
                self.meeting.stop(identity)
                await self._session.cancel('host_stop_instruction')
                self._active_id = None
                self._generated_epoch = None
                await self._session.send('status', message='讲解已暂停，麦克风继续聆听')
                return
            if not self.meeting.host_speech_start(identity):
                return
            await self._hold_presentation(state)
            await self._session.cancel('host_speech_confirmed')
            self._active_id = None
            self._generated_epoch = None
            question = self.meeting.snapshot(identity)['active_question']
            self.meeting.submit_speech(identity, question['id'], text)
            await self._run_active(identity)

    async def disconnected(self, identity):
        async with self._lock:
            state = self.meeting.snapshot(identity)
            self.meeting.disconnect(identity)
            if state['role'] == 'host':
                self._host_candidate = None
                await self._session.cancel('host_disconnected')
                self._active_id = None
                self._generated_epoch = None
            else:
                await self._run_active(identity)

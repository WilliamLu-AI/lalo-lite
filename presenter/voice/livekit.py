"""AgentSession runtime over explicit business-generation and audio IO ports.

Lalo owns endpoint decisions and business history. AgentSession executes each
committed reply; no Room, remote inference, or second business LLM is created.
"""
import asyncio
from collections import deque

from async_timeout import timeout
from livekit import rtc
from livekit.agents import Agent, AgentSession, llm
from livekit.agents.voice import io

from ..contracts import ProviderError, Sentence, Completed, AudioOutput, Synthesize, BusinessObserver


class _BusinessLLM(llm.LLM):
    def chat(self, **kwargs):
        raise RuntimeError("Generation must go through the Lalo business node")


class _Output(io.AudioOutput):
    def __init__(self, target, generated):
        super().__init__(label="lalo-playback", sample_rate=32000,
                         capabilities=io.AudioOutputCapabilities(pause=False))
        self.target, self.generated = target, generated
        self.spans = deque()
        self.registered = self.captured = 0
        self.ends = {}
        self.ended = set()
        self.heard = -1
        self.played = 0
        self.valid = True
        self.failure = None

    def register(self, index, pcm):
        if len(pcm) % 2:
            raise ProviderError("TTS returned invalid PCM16 audio")
        end = self.registered + len(pcm)//2
        self.spans.append((self.registered, end, index))
        self.registered = end

    async def end_sentence(self, index):
        self.ends[index] = self.registered
        await self._send_ends()

    async def _send_ends(self):
        for index, end in self.ends.items():
            if self.valid and end <= self.captured and index not in self.ended:
                self.ended.add(index)
                await self.target.sentence_end(index)

    async def capture_frame(self, frame):
        try:
            await self._capture_frame(frame)
        except Exception as error:
            self.failure = error
            self.generated.set()
            raise

    async def _capture_frame(self, frame):
        if not self.valid:
            return
        await super().capture_frame(frame)
        if frame.sample_rate != 32000 or frame.num_channels != 1:
            raise ProviderError("Playback requires PCM16 mono at 32000 Hz")
        pcm = bytes(frame.data)
        offset = 0
        while offset < len(pcm):
            while self.spans and self.spans[0][1] <= self.captured:
                self.spans.popleft()
            if not self.spans:
                raise ProviderError("Audio has no business sentence identity")
            _, end, index = self.spans[0]
            length = min(len(pcm)-offset, (end-self.captured)*2)
            await self.target.audio(index, pcm[offset:offset+length])
            self.captured += length//2
            offset += length
            await self._send_ends()

    def flush(self):
        super().flush()
        self.generated.set()

    def sentence_played(self, index):
        if not self.valid or index != self.heard+1 or index not in self.ended:
            return
        self.heard = index
        previous = self.played
        self.played = self.ends[index]
        self.on_playback_progressed(started_at=0, offset=previous/32000,
                                   duration=(self.played-previous)/32000)

    def playback_done(self):
        if (self.valid and self.generated.is_set() and self.ends
                and self.heard == max(self.ends) and self._pending_playback_count):
            self.on_playback_finished(playback_position=self.played/32000, interrupted=False)

    def clear_buffer(self):
        super().flush()
        self.valid = False
        if self._pending_playback_count:
            self.on_playback_finished(playback_position=self.played/32000, interrupted=True)
        self.generated.set()


class LiveKitRuntime:
    name = "livekit"

    def __init__(self):
        self.session = self.handle = self.sink = None
        self._owner = None

    async def run(self, events, speech: Synthesize, output: AudioOutput, business: BusinessObserver):
        await self.close()
        self._owner = asyncio.current_task()
        ready = asyncio.Event()
        sink = self.sink = _Output(output, ready)
        queue = asyncio.Queue(maxsize=32)
        answer = None
        failure = None

        def failed(error):
            nonlocal failure
            failure = error
            ready.set()

        class BusinessAgent(Agent):
            def __init__(self):
                super().__init__(instructions="Execute the committed Lalo reply.",
                                 llm=_BusinessLLM())

            async def llm_node(self, chat_ctx, tools, model_settings):
                nonlocal answer
                try:
                    async for event in events:
                        await business(event)
                        if isinstance(event, Sentence):
                            await queue.put(event)
                            yield event.text
                        elif isinstance(event, Completed):
                            answer = event.answer
                    if answer is None:
                        raise ProviderError("Generation ended without a completed answer")
                    await queue.put(None)
                except Exception as error:
                    failed(error)
                    raise
                finally:
                    await events.aclose()

            async def tts_node(self, text, model_settings):
                # SDK may re-chunk text; business sentence identities travel
                # separately. Drain SDK text so its pipeline remains bounded.
                async def drain():
                    async for _ in text:
                        pass
                drain_task = asyncio.create_task(drain())
                try:
                    while (sentence := await queue.get()) is not None:
                        source = speech(sentence.text)
                        size = 0
                        try:
                            async with timeout(45):
                                async for chunk in source:
                                    if not chunk:
                                        continue
                                    sink.register(sentence.index, chunk)
                                    size += len(chunk)
                                    yield rtc.AudioFrame(data=chunk, sample_rate=32000,
                                                         num_channels=1,
                                                         samples_per_channel=len(chunk)//2)
                                if not size:
                                    raise ProviderError("TTS returned no audio")
                                await sink.end_sentence(sentence.index)
                        finally:
                            await source.aclose()
                    await drain_task
                except Exception as error:
                    failed(error)
                    raise
                finally:
                    drain_task.cancel()
                    await asyncio.gather(drain_task, return_exceptions=True)

        self.session = AgentSession(turn_handling={
            "turn_detection": "manual",
            "interruption": {"enabled": True, "resume_false_interruption": False},
            "preemptive_generation": {"enabled": False},
        })
        self.session.output.audio = sink
        try:
            await self.session.start(agent=BusinessAgent(), record=False)
            self.handle = self.session.generate_reply(allow_interruptions=True)
            await ready.wait()
            if sink.failure is not None:
                raise sink.failure
            if failure is not None:
                raise failure
            if answer is None or not sink.captured:
                raise ProviderError("LiveKit reply ended without audio")
            await sink._send_ends()
            return answer, sink.captured*2
        except BaseException:
            await self.close()
            raise
        finally:
            if self._owner is asyncio.current_task():
                self._owner = None

    def sentence_played(self, index):
        if self.sink:
            self.sink.sentence_played(index)

    def playback_done(self):
        if self.sink:
            self.sink.playback_done()

    async def wait_for_playout(self):
        if self.handle:
            await self.handle.wait_for_playout()

    async def cancel(self):
        if self.handle and not self.handle.done():
            self.handle.interrupt()
        if self.sink:
            self.sink.clear_buffer()
        owner = self._owner
        if owner and owner is not asyncio.current_task() and not owner.done():
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)

    async def close(self):
        await self.cancel()
        if self.session:
            await self.session.aclose()
        self.session = self.handle = self.sink = None

import asyncio

from async_timeout import timeout

from ..contracts import ProviderError, Sentence, Completed, AudioOutput, Synthesize, BusinessObserver


class NativeRuntime:
    name = "native"

    def __init__(self):
        self._owner = None

    async def run(self, events, speech: Synthesize, output: AudioOutput, business: BusinessObserver):
        await self.cancel()
        self._owner = asyncio.current_task()
        queue = asyncio.Queue(maxsize=32)
        count = 0

        async def generate():
            answer = None
            try:
                async for event in events:
                    await business(event)
                    if isinstance(event, Sentence):
                        await queue.put(event)
                    elif isinstance(event, Completed):
                        answer = event.answer
                if answer is None:
                    raise ProviderError("Generation ended without a completed answer")
                await queue.put(None)
                return answer
            finally:
                await events.aclose()

        async def synthesize():
            nonlocal count
            while (sentence := await queue.get()) is not None:
                source = speech(sentence.text)
                sentence_bytes = 0
                try:
                    async with timeout(45):
                        async for chunk in source:
                            await output.audio(sentence.index, chunk)
                            sentence_bytes += len(chunk)
                            count += len(chunk)
                    if not sentence_bytes:
                        raise ProviderError("TTS returned no audio")
                    await output.sentence_end(sentence.index)
                finally:
                    await source.aclose()

        producer = asyncio.create_task(generate())
        consumer = asyncio.create_task(synthesize())
        try:
            answer, _ = await asyncio.gather(producer, consumer)
            return answer, count
        finally:
            producer.cancel()
            consumer.cancel()
            await asyncio.gather(producer, consumer, return_exceptions=True)
            if self._owner is asyncio.current_task():
                self._owner = None

    async def cancel(self):
        owner = self._owner
        if owner and owner is not asyncio.current_task() and not owner.done():
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)

    async def close(self):
        await self.cancel()

    def sentence_played(self, index):
        pass

    def playback_done(self):
        pass

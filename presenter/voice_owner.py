import asyncio
from .audio import Segmenter


class VoiceOwner:
    """One listening page per local service, including across browser profiles."""
    def __init__(self):
        self.owner = None
        self.lock = asyncio.Lock()

    async def acquire(self, session):
        async with self.lock:
            previous, self.owner = self.owner, session
            if previous and previous is not session and previous.live:
                await previous.stop_recognition()
                if previous.endpoint_task:
                    previous.endpoint_task.cancel()
                    await asyncio.gather(previous.endpoint_task, return_exceptions=True)
                previous.segmenter = Segmenter()
                await previous.cancel('microphone_taken_over')
                await previous.send('microphone_revoked')

    def release(self, session):
        if self.owner is session:
            self.owner = None

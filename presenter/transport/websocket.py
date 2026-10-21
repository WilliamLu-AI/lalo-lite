import base64
import asyncio


class WebSocketAudioOutput:
    """Existing browser wire format behind a transport-independent audio sink."""

    def __init__(self, send, is_current, before_audio=None):
        self.send = send
        self.is_current = is_current
        self.before_audio = before_audio

    async def audio(self, index, pcm):
        if not self.is_current():
            raise asyncio.CancelledError
        if self.before_audio:
            await self.before_audio()
        if not self.is_current():
            raise asyncio.CancelledError
        await self.send("audio", pcm=base64.b64encode(pcm).decode(),
                        rate=32000, sentence=index)

    async def sentence_end(self, index):
        if not self.is_current():
            raise asyncio.CancelledError
        await self.send("sentence_end", sentence=index)

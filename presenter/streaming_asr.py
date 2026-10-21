"""Standalone adapter for the same duplex protocol used by Unmute captions."""
import asyncio
import json
import uuid
from async_timeout import timeout
from websockets.asyncio.client import connect
from .providers import ProviderError


class StreamingASR:
    def __init__(self, settings, on_partial):
        self.settings = settings
        self.on_partial = on_partial
        self.queue = asyncio.Queue(maxsize=1600)
        self.task = None
        self.finished = False

    def start(self, frames):
        for frame in frames:
            self.feed(frame)
        self.task = asyncio.create_task(self.run())

    def feed(self, frame):
        if not self.finished:
            self.queue.put_nowait(frame)

    async def finish(self):
        self.finished = True
        self.queue.put_nowait(None)
        async with timeout(12):
            return await self.task

    async def close(self):
        self.finished = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self):
        settings = self.settings
        task_id = str(uuid.uuid4())
        key = settings.get('STREAMING_ASR_API_KEY') or settings.get('OMNI_API_KEY')
        url = settings.get('STREAMING_ASR_URL') or 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
        model = settings.get('STREAMING_ASR_MODEL') or 'qwen-audio-3.0-asr-flash-streaming'
        languages = (settings.get('STREAMING_ASR_LANGUAGES') or 'zh,en').split(',')
        if not key:
            raise ProviderError('ASR credential is not configured')
        async with timeout(45):
            async with connect(url, additional_headers={'Authorization': 'Bearer ' + key},
                               open_timeout=5, close_timeout=2, max_size=1024 * 1024) as ws:
                await ws.send(json.dumps({'header': {'action': 'run-task', 'task_id': task_id, 'streaming': 'duplex'},
                    'payload': {'task_group': 'audio', 'task': 'asr', 'function': 'recognition',
                        'model': model, 'input': {}, 'parameters': {'format': 'pcm', 'sample_rate': 16000,
                        'semantic_punctuation_enabled': False, 'heartbeat': True,
                        'language_hints': [s.strip() for s in languages if s.strip()][:4]}}}))
                async with timeout(8):
                    event = json.loads(await ws.recv())
                if event.get('header', {}).get('event') != 'task-started':
                    raise ProviderError('Streaming ASR start failed')

                async def transmit():
                    while True:
                        frame = await self.queue.get()
                        if frame is None:
                            await ws.send(json.dumps({'header': {'action': 'finish-task', 'task_id': task_id,
                                'streaming': 'duplex'}, 'payload': {'input': {}}}))
                            return
                        await ws.send(frame)

                sender = asyncio.create_task(transmit())
                sentences = {}
                try:
                    async for raw in ws:
                        if sender.done() and sender.exception():
                            raise ProviderError('Streaming ASR send failed')
                        event = json.loads(raw)
                        header = event.get('header', {})
                        if header.get('task_id') != task_id:
                            continue
                        kind = header.get('event')
                        if kind == 'task-failed':
                            raise ProviderError('Streaming ASR task failed')
                        if kind == 'task-finished':
                            return ''.join(sentences[k] for k in sorted(sentences)).strip()
                        sentence = event.get('payload', {}).get('output', {}).get('sentence', {})
                        if kind == 'result-generated' and not sentence.get('heartbeat'):
                            index, text = sentence.get('sentence_id'), sentence.get('text')
                            if isinstance(index, int) and isinstance(text, str):
                                sentences[index] = text
                                await self.on_partial(''.join(sentences[k] for k in sorted(sentences)))
                    raise ProviderError('Streaming ASR disconnected')
                finally:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)

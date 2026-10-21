import asyncio
import base64
import io
import json
import wave
import httpx
from websockets.asyncio.client import connect
from .locator import JevLocator


from .contracts import ProviderError


def wav_bytes(pcm, rate=16000):
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class Providers:
    def __init__(self, settings):
        self.s = settings
        self.usage_records = []
        self.http = httpx.AsyncClient(timeout=60)
        self.locator = JevLocator(settings, self.http)

    async def close(self):
        await self.http.aclose()

    def text_options(self):
        if self.s.get('OPENAI_MODEL').startswith('deepseek-'):
            return {'thinking': {'type': 'disabled'}}
        return {}

    async def completion(self, prefix, payload):
        if prefix == 'OPENAI':
            payload = {**payload, **self.text_options()}
        key = self.s.get(prefix + '_API_KEY')
        if not key:
            raise ProviderError(prefix + ' API Key is not configured', code='configuration')
        url = self.s.get(prefix + '_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        response = await self.http.post(url.rstrip('/') + '/chat/completions',
            headers={'Authorization': 'Bearer ' + key}, json=payload)
        if response.status_code >= 400:
            # Never put a provider body, request audio or credential in app logs.
            raise ProviderError(f'{prefix} HTTP {response.status_code}')
        result = response.json()
        if prefix == 'OPENAI':
            self.usage_records.append({'model':payload.get('model'),'usage':result.get('usage')})
            del self.usage_records[:-256]
        return result

    async def transcribe(self, pcm):
        audio = base64.b64encode(wav_bytes(pcm)).decode()
        if self.s.get('OMNI_PROTOCOL') == 'chat-audio':
            result = await self.completion('OMNI', {
                'model': self.s.get('OMNI_MODEL'), 'stream': False,
                'messages': [{'role': 'user', 'content': [
                    {'type': 'input_audio', 'input_audio': {'data': audio, 'format': 'wav'}},
                    {'type': 'text', 'text': 'Transcribe the audio verbatim. Return only the transcript.'}]}]})
            return result['choices'][0]['message']['content'].strip()
        result = await self.completion('OMNI', {
            'model': self.s.get('OMNI_MODEL', 'qwen3-asr-flash'), 'stream': False,
            'messages': [{'role': 'user', 'content': [{'type': 'input_audio',
                'input_audio': {'data': 'data:audio/wav;base64,' + audio}}]}],
            'asr_options': {'enable_itn': False}})
        return result['choices'][0]['message']['content'].strip()

    async def answer(self, messages):
        result = await self.completion('OPENAI', {
            'model': self.s.get('OPENAI_MODEL', 'qwen-flash'),
            'messages': messages, 'stream': False, 'max_tokens': 900,
            'response_format': {'type': 'json_object'},
        })
        content = result['choices'][0]['message']['content']
        return json.loads(content)

    async def answer_stream(self, messages):
        key = self.s.get('OPENAI_API_KEY')
        if not key:
            raise ProviderError('OPENAI credential is not configured')
        url = self.s.get('OPENAI_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        async with self.http.stream('POST', url.rstrip('/') + '/chat/completions',
                headers={'Authorization': 'Bearer ' + key}, json={
                    'model': self.s.get('OPENAI_MODEL', 'qwen-flash'), 'messages': messages,
                    'stream': True, 'max_tokens': 900, 'response_format': {'type': 'json_object'},
                    **self.text_options()}) as response:
            if response.status_code >= 400:
                raise ProviderError(f'OPENAI HTTP {response.status_code}')
            async for line in response.aiter_lines():
                if line.startswith('data:'):
                    data = line[5:].strip()
                    if data == '[DONE]':
                        return
                    item = json.loads(data)
                    if item.get('error'):
                        raise ProviderError('Model stream failed')
                    for choice in item.get('choices', []):
                        delta = choice.get('delta', {}).get('content')
                        if delta:
                            yield delta

    async def speech(self, text):
        key = self.s.get('MINIMAX_API_KEY')
        if not key:
            raise ProviderError('MINIMAX credential is not configured')
        url = self.s.get('MINIMAX_WS_URL', 'wss://api.minimaxi.com/ws/v1/t2a_v2')
        async with connect(url, additional_headers={'Authorization': 'Bearer ' + key},
                           open_timeout=15, max_size=4 * 1024 * 1024) as ws:
            async def event():
                result = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if result.get('base_resp', {}).get('status_code', 0) != 0:
                    raise ProviderError('TTS provider rejected the request')
                return result
            if (await event()).get('event') != 'connected_success':
                raise ProviderError('TTS connection handshake failed')
            await ws.send(json.dumps({'event': 'task_start',
                'model': self.s.get('MINIMAX_MODEL', 'speech-2.6-hd'),
                'voice_setting': {'voice_id': self.s.get('MINIMAX_VOICE_ID', 'male-qn-qingse'),
                                  'speed': 1, 'vol': 1, 'pitch': 0},
                'audio_setting': {'sample_rate': 32000, 'format': 'pcm', 'channel': 1}}))
            if (await event()).get('event') != 'task_started':
                raise ProviderError('TTS task handshake failed')
            await ws.send(json.dumps({'event': 'task_continue', 'text': text}))
            while True:
                item = await event()
                audio = item.get('data', {}).get('audio')
                if audio:
                    yield bytes.fromhex(audio)
                if item.get('is_final'):
                    break
                if item.get('event') in ('task_failed', 'error'):
                    raise ProviderError('TTS task failed')
            await ws.send(json.dumps({'event': 'task_finish'}))

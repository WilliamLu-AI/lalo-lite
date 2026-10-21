"""DashScope Qwen TTS using LiveKit's TTS interface, without a Room or job."""
import asyncio
import base64
import binascii
import json
import uuid
from urllib.parse import urlsplit, urlunsplit, urlencode

import aiohttp
from async_timeout import timeout
from livekit.agents import APIError, DEFAULT_API_CONNECT_OPTIONS, tts, utils


LANGUAGES = {
    "zh": "Chinese", "en": "English", "de": "German", "it": "Italian",
    "pt": "Portuguese", "es": "Spanish", "ja": "Japanese", "ko": "Korean",
    "fr": "French", "ru": "Russian",
}


class QwenTTS(tts.TTS):
    def __init__(self, *, api_key, model, voice, language, base_url, http_session):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=24000, num_channels=1)
        self.api_key = api_key
        self.model_id = model
        self.voice = voice
        self.language = LANGUAGES.get(language, language or "Auto")
        self.base_url = (base_url or "https://dashscope.aliyuncs.com").rstrip("/")
        self.http_session = http_session

    @property
    def model(self):
        return self.model_id

    @property
    def provider(self):
        return "DashScope Qwen"

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return QwenChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class QwenChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter):
        model = self._tts
        output_emitter.initialize(request_id=utils.shortuuid(), sample_rate=24000,
                                  num_channels=1, mime_type="audio/pcm")
        try:
            async with timeout(90):
                if "-realtime" in model.model_id:
                    size = await self._realtime(output_emitter, model)
                else:
                    size = await self._http(output_emitter, model)
                if not size:
                    raise APIError("Qwen TTS returned no audio", retryable=False)
            output_emitter.flush()
        except APIError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError):
            raise APIError("Qwen TTS connection failed or timed out", retryable=False) from None
        except (ValueError, KeyError, TypeError, binascii.Error):
            raise APIError("Qwen TTS returned invalid audio or events", retryable=False) from None

    @staticmethod
    def _push(emitter, data):
        if data:
            pcm = base64.b64decode(data, validate=True)
            if len(pcm) % 2:
                raise APIError("Qwen TTS returned invalid PCM16 audio", retryable=False)
            emitter.push(pcm)
            return len(pcm)
        return 0

    async def _http(self, emitter, model):
        url = model.base_url + "/api/v1/services/aigc/multimodal-generation/generation"
        async with model.http_session.post(
            url,
            headers={"Authorization": "Bearer " + model.api_key, "X-DashScope-SSE": "enable"},
            json={"model": model.model_id, "input": {
                "text": self.input_text, "voice": model.voice, "language_type": model.language,
            }},
            timeout=aiohttp.ClientTimeout(total=90, sock_read=30),
            read_bufsize=4 * 1024 * 1024,
        ) as response:
            if response.status != 200:
                raise APIError(f"Qwen TTS HTTP {response.status}; check key, region and model", retryable=False)
            if response.content_type != "text/event-stream":
                raise APIError("Qwen TTS expected an SSE audio stream", retryable=False)
            data_lines = []
            size = 0
            async for raw in response.content:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
                elif not line and data_lines:
                    event = json.loads("\n".join(data_lines))
                    data_lines.clear()
                    if event.get("code") or event.get("status_code", 200) != 200:
                        raise APIError("Qwen TTS request rejected; check model, voice and quota", retryable=False)
                    output = event.get("output") or {}
                    size += self._push(emitter, (output.get("audio") or {}).get("data"))
                    if output.get("finish_reason") == "stop":
                        return size
            raise APIError("Qwen TTS audio stream ended before completion", retryable=False)

    async def _realtime(self, emitter, model):
        address = urlsplit(model.base_url)
        url = urlunsplit(("wss" if address.scheme == "https" else "ws", address.netloc,
                          "/api-ws/v1/realtime", urlencode({"model": model.model_id}), ""))
        async with model.http_session.ws_connect(
            url, headers={"Authorization": "Bearer " + model.api_key},
            timeout=aiohttp.ClientWSTimeout(ws_receive=30, ws_close=3),
            max_msg_size=4 * 1024 * 1024,
        ) as ws:
            async def send(kind, **data):
                await ws.send_json({"event_id": uuid.uuid4().hex, "type": kind, **data})

            await send("session.update", session={
                "mode": "commit", "voice": model.voice, "response_format": "pcm",
                "sample_rate": 24000, "language_type": model.language,
            })
            submitted = complete = False
            size = 0
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise APIError("Qwen TTS realtime connection ended unexpectedly", retryable=False)
                event = json.loads(message.data)
                kind = event.get("type")
                if kind == "error":
                    raise APIError("Qwen TTS realtime request rejected; check model, voice and quota", retryable=False)
                if kind == "session.updated" and not submitted:
                    await send("input_text_buffer.append", text=self.input_text)
                    await send("input_text_buffer.commit")
                    submitted = True
                elif kind == "response.audio.delta":
                    size += self._push(emitter, event["delta"])
                elif kind == "response.done":
                    if event.get("response", {}).get("status") != "completed":
                        raise APIError("Qwen TTS realtime synthesis did not complete", retryable=False)
                    complete = True
                    await send("session.finish")
                elif kind == "session.finished" and complete:
                    return size
            raise APIError("Qwen TTS realtime stream ended before completion", retryable=False)

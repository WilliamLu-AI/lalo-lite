"""Session-scoped native/LiveKit bridge. No LiveKit room or cloud required."""
import asyncio
from urllib.parse import urlsplit
from types import SimpleNamespace
from async_timeout import timeout
from ..providers import Providers
from ..contracts import ProviderError
from ..streaming_asr import StreamingASR


class LiveRecognition:
    def __init__(self, model, on_partial):
        from livekit.agents.stt import SpeechEventType
        self.events = SpeechEventType
        self.stream = model.stream()
        self.on_partial = on_partial
        self.task = None

    def start(self, frames):
        self.task = asyncio.create_task(self.consume())
        for frame in frames: self.feed(frame)

    def feed(self, pcm):
        from livekit import rtc
        self.stream.push_frame(rtc.AudioFrame(data=pcm, sample_rate=16000, num_channels=1, samples_per_channel=len(pcm)//2))

    async def consume(self):
        final = []
        async for event in self.stream:
            if not event.alternatives: continue
            text = event.alternatives[0].text
            if event.type == self.events.INTERIM_TRANSCRIPT: await self.on_partial(text)
            elif event.type == self.events.FINAL_TRANSCRIPT:
                final.append(text)
        return ' '.join(final).strip()

    async def finish(self):
        self.stream.end_input()
        async with timeout(20): return await self.task

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.stream.aclose()


class VoiceProviders:
    def __init__(self, base, settings, profiles):
        self.base, self.profiles = base, profiles
        values = dict(settings.values)
        for kind, prefix in [('asr','OMNI'),('tts','MINIMAX'),('llm','OPENAI')]:
            p = profiles[kind]
            values[prefix+'_API_KEY'] = p['apiKey'] or ('local-no-key' if p['provider']=='compatible' else '')
            if p['baseUrl']: values[prefix+'_BASE_URL'] = p['baseUrl']
            values[prefix+'_MODEL'] = p['model']
        # Native Qwen streaming credentials must use the selected connection as well.
        values['STREAMING_ASR_API_KEY'] = profiles['asr']['apiKey']
        if profiles['asr']['provider']=='qwen' and profiles['asr']['mode']=='stream':
            values['STREAMING_ASR_MODEL']=profiles['asr']['model']
            values['OMNI_MODEL']='qwen3-asr-flash'
        values['MINIMAX_VOICE_ID'] = profiles['tts']['voice']
        asr_url=profiles['asr']['baseUrl']
        if profiles['asr']['provider']=='qwen' and asr_url and asr_url!=settings.get('OMNI_BASE_URL','https://dashscope.aliyuncs.com/compatible-mode/v1'):
            url=urlsplit(asr_url)
            values['STREAMING_ASR_URL']=('wss' if url.scheme=='https' else 'ws')+'://'+url.netloc+'/api-ws/v1/inference'
        tts_url=profiles['tts']['baseUrl']
        if profiles['tts']['provider']=='minimax-native' and tts_url:
            values['MINIMAX_WS_URL']=tts_url.replace('https://','wss://').replace('http://','ws://').rstrip('/')+'/ws/v1/t2a_v2'
        self.settings = SimpleNamespace(values=values, get=lambda k, default='': values.get(k) or default)
        self.native = Providers(self.settings)
        self.asr_model = self.tts_model = None
        self.initialized = set()
        self.http_session = None

    def __getattr__(self, name):
        return getattr(self.native, name)

    async def init(self):
        # Explicit aiohttp session avoids a dependency on LiveKit's job context.
        import aiohttp
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90), trust_env=True)
        try:
            self.model('asr')
            self.model('tts')
            return self
        except BaseException:
            await self.close()
            raise

    def make_model(self, kind):
        p = self.profiles[kind]
        provider = p['provider']
        if provider in ('qwen','minimax-native'): return None
        if provider == 'qwen-tts' and kind == 'tts':
            from .qwen_tts import QwenTTS
            if not p['apiKey']: raise ValueError('Qwen TTS requires an API Key')
            return QwenTTS(api_key=p['apiKey'], model=p['model'], voice=p['voice'],
                           language=p['language'], base_url=p['baseUrl'], http_session=self.http_session)
        try:
            from livekit.plugins import openai, elevenlabs, minimax
        except ImportError:
            raise ValueError('请安装 requirements-voice.txt 中的 LiveKit 插件') from None
        key = p['apiKey'] or ('local-no-key' if provider=='compatible' else '')
        if not key: raise ValueError('所选语音连接尚未配置 API Key')
        if provider in ('openai','compatible'):
            options = dict(api_key=key, model=p['model'])
            if p['baseUrl']: options['base_url'] = p['baseUrl']
            if kind=='asr': return openai.STT(**options, language=p['language'] or 'zh', use_realtime=False)
            return openai.TTS(**options, voice=p['voice'], response_format='pcm')
        if provider=='elevenlabs':
            options = dict(api_key=key, model=p['model'], http_session=self.http_session)
            if p['baseUrl']: options['base_url'] = p['baseUrl']
            if kind=='asr': return elevenlabs.STT(**options, language_code=p['language'] or 'zh', use_realtime=True)
            return elevenlabs.TTS(**options, voice_id=p['voice'])
        if provider=='minimax' and kind=='tts':
            options = dict(api_key=key, model=p['model'], voice=p['voice'], http_session=self.http_session,
                           sample_rate=32000)
            if p['baseUrl']: options['base_url']=p['baseUrl']
            return minimax.TTS(**options)
        raise ValueError('不支持的语音组合')

    def model(self, kind):
        if kind not in self.initialized:
            setattr(self, kind+'_model', self.make_model(kind))
            self.initialized.add(kind)
        return getattr(self, kind+'_model')

    def start_asr(self, on_partial, frames):
        if self.profiles['asr']['mode'] != 'stream': return None
        self.model('asr')
        stream = LiveRecognition(self.asr_model, on_partial) if self.asr_model else StreamingASR(self.settings, on_partial)
        stream.start(frames)
        return stream

    async def transcribe(self, pcm):
        self.model('asr')
        if not self.asr_model: return await self.native.transcribe(pcm)
        from livekit import rtc
        if self.asr_model.capabilities.streaming:
            async def ignore(text): pass
            stream = LiveRecognition(self.asr_model, ignore)
            stream.start([pcm])
            try: return await stream.finish()
            finally: await stream.close()
        frame=rtc.AudioFrame(data=pcm,sample_rate=16000,num_channels=1,samples_per_channel=len(pcm)//2)
        result=await self.asr_model.recognize(frame)
        return result.alternatives[0].text if result.alternatives else ''

    async def speech(self, text):
        self.model('tts')
        if not self.tts_model:
            async for chunk in self.native.speech(text): yield chunk
            return
        from livekit import rtc
        from livekit.agents import APIError
        # Existing playback contract is PCM16 mono at 32 kHz.
        resampler = None
        stream = self.tts_model.synthesize(text)
        try:
            async for event in stream:
                frame = event.frame
                if frame.num_channels != 1: raise ProviderError('TTS must produce mono audio')
                if frame.sample_rate == 32000:
                    yield bytes(frame.data)
                else:
                    if resampler is None: resampler=rtc.AudioResampler(input_rate=frame.sample_rate,output_rate=32000,num_channels=1)
                    for converted in resampler.push(frame): yield bytes(converted.data)
            if resampler:
                for frame in resampler.flush(): yield bytes(frame.data)
        except APIError as error:
            if self.profiles['tts']['provider'] == 'qwen-tts':
                raise ProviderError(str(error), stage='tts') from None
            raise
        finally:
            await stream.aclose()

    async def close(self):
        closers = [model.aclose() for model in (self.asr_model, self.tts_model) if model]
        if self.http_session: closers.append(self.http_session.close())
        closers.append(self.native.close())
        await asyncio.gather(*closers, return_exceptions=True)

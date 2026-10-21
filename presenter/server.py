import asyncio
from contextlib import asynccontextmanager
import hmac
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .settings import ROOT, Settings
from .providers import Providers
from .audio import TurnDetector
from .session import Session
from .launch import read_launch
from .voice_owner import VoiceOwner
from .paths import MODEL_PATH, DATA_ROOT
from .voice_config import VoiceConfig
from .cli import VERSION

settings = Settings()


@asynccontextmanager
async def lifespan(app):
    app.state.providers = Providers(settings)
    app.state.voice_store = VoiceConfig(settings.get("VOICE_CONFIG_DIR", str(DATA_ROOT / "voice-config")), settings)
    model = MODEL_PATH
    app.state.tad = await asyncio.to_thread(TurnDetector, model) if model.exists() else None
    yield
    await app.state.providers.close()


app = FastAPI(lifespan=lifespan)
voice_owner = VoiceOwner()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])
app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')


def voice_store():
    store = getattr(app.state, 'voice_store', None)
    if store is None:
        raise HTTPException(503, 'Voice configuration unavailable')
    return store


def capabilities():
    result=settings.capabilities()
    store=getattr(app.state,'voice_store',None)
    if store:
        profiles=store.resolve()
        for kind,flag in [('asr','asr'),('tts','tts'),('llm','chat')]:
            p=profiles[kind]
            result[flag]=bool(p['apiKey'] or (p['provider']=='compatible' and p['baseUrl']))
        result['tiered']=bool((result['codex'] or result['claude']) and result['chat'])
    return result


def same_origin(request):
    # Local settings can contain credentials: reject browser cross-origin writes.
    if request.headers.get('origin') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Same-origin request required')


@app.get('/api/voice')
async def get_voice():
    return JSONResponse(voice_store().public(), headers={'Cache-Control':'no-store'})


@app.put('/api/voice')
async def put_voice(request: Request):
    same_origin(request)
    raw = await request.body()
    if len(raw)>100000: raise HTTPException(413, 'Configuration too large')
    try:
        import json
        data=json.loads(raw)
        if not isinstance(data,dict): raise ValueError('Invalid configuration')
        result=voice_store().save(data)
        return JSONResponse(result, headers={'Cache-Control':'no-store'})
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.post('/api/voice/test')
async def test_voice(request: Request):
    same_origin(request)
    import base64
    import json
    import time
    from .model_adapters.voice import VoiceProviders
    raw=await request.body()
    if len(raw)>1_000_000: raise HTTPException(413,'Test audio too large')
    runtime=None
    try:
        data=json.loads(raw)
        profiles=voice_store().resolve(data.get('active'))
        kind=data.get('kind')
        if kind not in ('asr','tts','llm'): raise ValueError('Unknown test type')
        runtime=await VoiceProviders(app.state.providers,settings,profiles).init()
        started=time.monotonic()
        async with __import__('async_timeout').timeout(60):
            if kind=='asr':
                pcm=base64.b64decode(data.get('pcm',''),validate=True)
                if not pcm or len(pcm)%2 or len(pcm)>640000: raise ValueError('Provide PCM16 mono 16 kHz, up to 20 seconds')
                result={'text':await runtime.transcribe(pcm)}
            elif kind=='tts':
                text=str(data.get('text') or '你好，这是 Lalo 的语音试听。')[:200]
                chunks=[]
                async for chunk in runtime.speech(text):
                    chunks.append(chunk)
                    if sum(map(len,chunks))>2_000_000: raise ValueError('Audio response too large')
                if not chunks: raise ValueError('No audio returned')
                result={'pcm':base64.b64encode(b''.join(chunks)).decode(),'rate':32000}
            else:
                answer=await runtime.answer([{'role':'user','content':'Return JSON {"speech":"连接成功"}'}])
                if not isinstance(answer,dict): raise ValueError('Invalid model response')
                result={'text':str(answer.get('speech',''))[:300]}
        return JSONResponse({**result,'elapsedMs':round((time.monotonic()-started)*1000)},headers={'Cache-Control':'no-store'})
    except Exception as exc:
        # SDK exceptions can contain URLs/headers; never return their raw text.
        raise HTTPException(400,'连接测试失败（'+type(exc).__name__+'），请检查模型、地址、凭据和网络') from None
    finally:
        if runtime: await runtime.close()


def authorized(connection):
    expected = os.environ.get('LALO_SESSION_TOKEN', '')
    if not expected:
        # Source-only test service. The desktop entry point always generates a token.
        return True
    supplied = connection.headers.get('authorization', '').removeprefix('Bearer ')
    supplied = supplied or connection.cookies.get('lalo_session', '')
    return supplied.isascii() and hmac.compare_digest(supplied, expected)


@app.middleware('http')
async def protect_private_routes(request: Request, call_next):
    if request.url.path in ('/health', '/session', '/shutdown') or request.url.path.startswith(('/launch/', '/api/voice')):
        if not authorized(request):
            return JSONResponse({'detail': 'Open Lalo Lite from the installed application'}, status_code=401)
    response = await call_next(request)
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if request.url.path in ('/session', '/health'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.post('/session')
async def browser_session(request: Request):
    origin = request.headers.get('origin')
    if origin and origin != 'http://' + request.headers.get('host', ''):
        raise HTTPException(status_code=403, detail='Invalid origin')
    response = JSONResponse({'ok': True})
    response.set_cookie('lalo_session', os.environ.get('LALO_SESSION_TOKEN', ''),
        httponly=True, samesite='strict', path='/')
    return response


@app.post('/shutdown')
async def shutdown():
    callback = getattr(app.state, 'shutdown', None)
    if callback is None:
        raise HTTPException(status_code=409, detail='Not a managed desktop instance')
    callback()
    return {'stopped': True}


@app.get('/')
async def index():
    return FileResponse(ROOT / 'web/index.html')


@app.get('/health')
async def health():
    return {'ok': True, 'app': 'lalo-lite', 'launchProtocol': 2, 'version': VERSION, 'capabilities': capabilities()}


@app.get('/launch/{token}')
async def launch(token: str):
    try:
        data = read_launch(token)
    except (ValueError, FileNotFoundError, KeyError):
        raise HTTPException(status_code=404, detail='Launch not found or expired; run the skill again')
    return JSONResponse(data, headers={'Cache-Control': 'no-store'})


@app.websocket('/ws')
async def websocket(ws: WebSocket):
    origin = ws.headers.get('origin')
    if origin != 'http://' + ws.headers.get('host', '') or not authorized(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    session = Session(ws, app.state.providers, settings, app.state.tad)
    session.voice_store = getattr(app.state, "voice_store", None)
    try:
        await session.send('ready', capabilities=capabilities())
        while True:
            msg = await asyncio.wait_for(ws.receive(), 45)
            if msg['type'] == 'websocket.disconnect':
                break
            if msg.get('bytes') is not None:
                if voice_owner.owner is session:
                    await session.audio(msg['bytes'])
            elif msg.get('text') is not None:
                if len(msg['text']) > 4_000_000:
                    raise ValueError('Message too large')
                import json
                data = json.loads(msg['text'])
                if not isinstance(data, dict):
                    raise ValueError('Expected object')
                if data.get('type') == 'client_event':
                    if data.get('event') == 'mic_started':
                        await voice_owner.acquire(session)
                    elif data.get('event') in ('mic_stopped', 'mic_failed'):
                        voice_owner.release(session)
                await session.message(data)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except ValueError:
        await session.send('error', message='输入格式或大小不符合要求')
        await ws.close(code=1008)
    finally:
        voice_owner.release(session)
        await session.close()

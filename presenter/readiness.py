"""One local preflight for CLI and HTTP/WS. Never probes paid model endpoints."""
import importlib.util
import shutil
from pathlib import Path
from urllib.parse import urlparse

from .paths import DATA_ROOT, RESOURCE_ROOT, MODEL_PATH
from .voice_config import VoiceConfig, CATALOG


def load_voice_store(settings):
    return VoiceConfig(settings.get('VOICE_CONFIG_DIR', str(DATA_ROOT / 'voice-config')), settings)


def dependency_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def command_available(value):
    try:
        from .commands import claude_command
        claude_command(value)
        return True
    except (ValueError, TypeError):
        return False


def model_readiness(profile, kind):
    missing = []
    provider = profile['provider']
    url = profile['baseUrl'] or CATALOG[provider]['url']
    if not profile['model']: missing.append('模型 ID')
    key = profile.get('streamApiKey') or profile['apiKey'] if kind=='asr' and provider=='qwen' and profile['mode']=='stream' else profile['apiKey']
    host = urlparse(url).hostname or ''
    authenticated_cloud = host=='api.openai.com' or host.endswith('.aliyuncs.com')
    if not key and (provider not in ('compatible','chat-audio') or authenticated_cloud): missing.append('API Key')
    if provider in ('compatible','chat-audio') and not url: missing.append('服务地址')
    if kind=='tts' and not profile['voice']: missing.append('音色 ID')
    if kind=='asr' and provider=='qwen' and profile['mode']=='stream' and not profile.get('streamUrl'): missing.append('实时识别 WS/WSS 地址')
    plugin = {'openai':'openai','compatible':'openai','elevenlabs':'elevenlabs','minimax':'minimax'}.get(provider) if kind!='llm' else None
    dependency = 'livekit.plugins.' + plugin if plugin else ('livekit.agents' if provider=='qwen-tts' else '')
    installed = not dependency or dependency_available(dependency)
    state = 'missing_configuration' if missing else 'unavailable' if not installed else 'configured'
    message = ('缺少：' + '、'.join(missing)) if missing else ('请安装 requirements-voice.txt 中的语音依赖' if not installed else '配置完整，尚未验证连接；请在语音模型设置中测试')
    return dict(state=state, ready=state=='configured', configured=not missing,
                connectionTested=False, provider=provider, model=profile['model'], message=message)


def report(settings, store=None):
    store = store if store is not None else load_voice_store(settings)
    profiles = store.resolve()
    readiness = {kind:model_readiness(profile,kind) for kind,profile in profiles.items()}
    for name in ('codex','claude'):
        value = settings.get('CODEX_BINARY' if name=='codex' else 'CLAUDE_COMMAND')
        available = bool(value) and (command_available(value) if name=='claude' else bool(shutil.which(value) or Path(value).is_file()))
        readiness[name] = dict(state='configured' if available else 'unavailable' if value else 'missing_configuration',
                               ready=available, configured=bool(value), connectionTested=False,
                               message='本机命令可用，尚未验证登录' if available else '请配置可执行的 '+('CLAUDE_COMMAND' if name=='claude' else 'CODEX_BINARY'))
    frontend = (RESOURCE_ROOT / 'web/app.bundle.js').is_file()
    readiness['frontend'] = dict(state='ready' if frontend else 'unavailable', ready=frontend,
                                 message='前端已构建' if frontend else '前端未构建，请在源码目录运行 npm ci 和 npm run build')
    tad = MODEL_PATH.is_file()
    readiness['tad'] = dict(state='ready' if tad else 'optional_missing', ready=tad,
                           message='话轮检测模型已安装' if tad else '未安装可选话轮模型；使用静音分段，不影响云端识别')
    runtime = settings.get('VOICE_RUNTIME', 'native')
    required = ['fastapi','uvicorn','httpx','aiohttp','async_timeout','numpy','webrtcvad','websockets']
    if runtime=='livekit': required.append('livekit.agents')
    missing_dependencies = [name for name in required if not dependency_available(name)]
    runtime_ok = runtime in ('native','livekit') and not missing_dependencies
    readiness['runtime'] = dict(state='ready' if runtime_ok else 'unavailable', ready=runtime_ok,
                               message='语音运行时：'+runtime if runtime_ok else ('缺少依赖：'+', '.join(missing_dependencies)+'；请安装 requirements-lock.txt' if missing_dependencies else 'VOICE_RUNTIME 只支持 native 或 livekit'))
    capabilities = {flag:readiness[kind]['ready'] for kind,flag in [('asr','asr'),('tts','tts'),('llm','chat'),('codex','codex'),('claude','claude'),('tad','tad')]}
    capabilities['tiered'] = (capabilities['codex'] or capabilities['claude']) and capabilities['chat']
    return dict(capabilities=capabilities, readiness=readiness, frontendReady=frontend)

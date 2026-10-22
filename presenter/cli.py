"""Stable desktop entry point; application data never lives in the install folder."""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

VERSION = '0.1.0-preview'
MODEL_HASH = '2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f'
MODEL_URL = 'https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx'


def source_id(mode, explicit=None, environ=None):
    import re
    env = os.environ if environ is None else environ
    if mode == 'cloud':
        return '', ''
    if mode == 'claude':
        key, host = 'CLAUDE_CODE_SESSION_ID', 'claude'
    elif mode == 'codex':
        key, host = 'CODEX_THREAD_ID', 'codex'
    elif explicit and explicit != env.get('CLAUDE_CODE_SESSION_ID'):
        key, host = 'CODEX_THREAD_ID', 'codex'  # tiered: host follows the bound session type
    elif env.get('CLAUDE_CODE_SESSION_ID'):
        key, host = 'CLAUDE_CODE_SESSION_ID', 'claude'
    elif env.get('CODEX_THREAD_ID'):
        key, host = 'CODEX_THREAD_ID', 'codex'
    else:
        raise ValueError('Current session identity unavailable; pass --session or set CLAUDE_CODE_SESSION_ID / CODEX_THREAD_ID')
    value = explicit or env.get(key)
    if not value or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', value):
        raise ValueError(f'Current session identity unavailable; pass --session or set {key}')
    return value, host


def prepare(root, project, document, mode, session, host='codex'):
    import re
    project = Path(project).expanduser().resolve(strict=True)
    document = Path(document).expanduser().resolve(strict=True)
    if not project.is_dir() or not document.is_file():
        raise ValueError('Expected an existing project directory and document file')
    if document.suffix.lower() not in ('.md', '.markdown', '.html', '.htm', '.txt'):
        raise ValueError('Use Markdown, static HTML or text')
    if document.stat().st_size > 500000:
        raise ValueError('Document exceeds 500000 bytes')
    text = document.read_text(encoding='utf-8-sig')
    data = {'mode': mode, 'source': session, 'host': host, 'cwd': str(project),
            'document': {'name': document.name, 'text': text,
                         'html': document.suffix.lower() in ('.html', '.htm')}}
    key = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    directory = Path(root) / 'tmp/launches'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in directory.glob('*.json'):
        if not re.fullmatch(r'[a-f0-9]{64}', path.stem):
            continue
        try:
            old = json.loads(path.read_text(encoding='utf-8'))
            if old.get('key') == key and old.get('expiresAt', 0) > time.time():
                return path.stem
        except (ValueError, OSError):
            continue
    token = secrets.token_hex(32)
    data.update(key=key, expiresAt=time.time() + 86400)
    atomic_json(directory / (token + '.json'), data)
    return token


def atomic_json(path, data):
    staged = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    try:
        with staged.open('x', encoding='utf-8') as stream:
            os.chmod(staged, 0o600)
            json.dump(data, stream, ensure_ascii=True)
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def local_request(info, path, method='GET'):
    port = info.get('port')
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('Invalid local server port')
    scheme = info.get('scheme', 'http')
    if scheme not in ('http', 'https'):
        raise ValueError('Invalid local server scheme')
    handlers = [urllib.request.ProxyHandler({})]
    if scheme == 'https':
        try:
            context = ssl.create_default_context(cafile=info.get('tls_ca') or None)
        except OSError as error:
            raise ValueError('Cannot load the local HTTPS certificate authority; check LALO_MEETING_TLS_CA') from error
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(f'{scheme}://127.0.0.1:{port}' + path, method=method,
        headers={'Authorization': 'Bearer ' + info['token']})
    with opener.open(request, timeout=2) as response:
        return json.load(response)


def running_server():
    from .paths import DATA_ROOT
    try:
        info = json.loads((DATA_ROOT / 'run/server.json').read_text())
        value = local_request(info, '/health')
        if value.get('app') == 'lalo-lite' and value.get('launchProtocol') == 2 and value.get('version') == VERSION:
            return info
        raise ValueError('A different Lalo Lite version is running. Stop it before starting this version.')
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLError):
            raise ValueError('Cannot verify the running HTTPS service certificate; check LALO_MEETING_TLS_CA and certificate names') from error
        return None
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def child_command():
    compiled = globals().get('__compiled__')
    if compiled is not None:
        # Nuitka 4.3 exposes the runnable binary separately from its Python runtime.
        executable = getattr(compiled, 'process_exe', None) or sys.argv[0]
        return [str(Path(executable).resolve()), 'serve']
    return [sys.executable, str(Path(__file__).resolve().parents[1] / 'lalo.py'), 'serve']


def ensure_server():
    from .paths import DATA_ROOT, ensure_data
    check_frontend()
    ensure_data()
    current = running_server()
    if current:
        return current
    lock = DATA_ROOT / 'run/start.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        for _ in range(120):
            current = running_server()
            if current:
                return current
            if not lock.exists():
                return ensure_server()
            time.sleep(0.25)
        raise ValueError(f'Startup is already in progress. Check the log; if no startup process exists, remove {lock}')
    os.close(descriptor)
    try:
        # Recheck under the startup lock in case another launcher just finished.
        current = running_server()
        if current:
            return current
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        env = {**os.environ, 'LALO_HOME': str(DATA_ROOT)}
        with (DATA_ROOT / 'run/server.log').open('ab') as log:
            process = subprocess.Popen(child_command(), cwd=DATA_ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)
        for _ in range(160):
            current = running_server()
            if current:
                return current
            if process.poll() is not None:
                raise ValueError(f'Application startup failed; see {DATA_ROOT / "run/server.log"}')
            time.sleep(0.25)
        process.terminate()
        process.wait(timeout=10)
        raise ValueError(f'Application startup timed out; see {DATA_ROOT / "run/server.log"}')
    finally:
        lock.unlink(missing_ok=True)


def initialize(model_file=None, download=False):
    from .paths import DATA_ROOT, MODEL_PATH, RESOURCE_ROOT, ensure_data
    ensure_data()
    configuration = DATA_ROOT / '.env'
    if not configuration.exists():
        with configuration.open('x', encoding='utf-8') as output:
            os.chmod(configuration, 0o600)
            output.write((RESOURCE_ROOT / '.env.example').read_text(encoding='utf-8-sig'))
    if model_file or download:
        staged = MODEL_PATH.with_suffix('.' + secrets.token_hex(4) + '.download')
        try:
            if model_file:
                shutil.copyfile(Path(model_file).expanduser().resolve(strict=True), staged)
            else:
                with urllib.request.urlopen(MODEL_URL, timeout=60) as response, staged.open('wb') as output:
                    shutil.copyfileobj(response, output)
            if hashlib.sha256(staged.read_bytes()).hexdigest() != MODEL_HASH:
                raise ValueError('Smart Turn model SHA-256 mismatch')
            staged.replace(MODEL_PATH)
        finally:
            staged.unlink(missing_ok=True)
    return {'config': str(configuration), 'modelInstalled': MODEL_PATH.exists()}


def serve(meeting=False):
    from .paths import DATA_ROOT, ensure_data
    check_frontend()
    ensure_data()
    options = None
    if meeting:
        from .settings import Settings
        from .meeting_launch import listener_options
        options = listener_options(Settings())
        if running_server():
            raise ValueError('A Lalo service is already running; stop it before starting meeting mode')
    token = secrets.token_hex(32)
    os.environ['LALO_SESSION_TOKEN'] = token
    import uvicorn
    from .server import app
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    bind_host = '0.0.0.0' if meeting else '127.0.0.1'
    listener.bind((bind_host, options['port'] if options else 0))
    listener.listen(128)
    info = {'port': listener.getsockname()[1], 'token': token, 'pid': os.getpid(), 'version': VERSION}
    if options:
        info.update(scheme='https', tls_ca=options['ca'], meeting_public_url=options['public_url'])
    file = DATA_ROOT / 'run/server.json'
    tls = {'ssl_certfile': options['certfile'], 'ssl_keyfile': options['keyfile']} if options else {}
    server = uvicorn.Server(uvicorn.Config(app, host=bind_host, port=info['port'],
        loop='asyncio', http='h11', ws='websockets', lifespan='on',
        ws_max_size=4 * 1024 * 1024, access_log=False, timeout_graceful_shutdown=3,
        proxy_headers=False, **tls))
    app.state.shutdown = lambda: setattr(server, 'should_exit', True)
    atomic_json(file, info)
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
        try:
            if json.loads(file.read_text()).get('token') == token:
                file.unlink()
        except (OSError, ValueError):
            pass


def check_frontend():
    from .paths import RESOURCE_ROOT
    if not (RESOURCE_ROOT / 'web/app.bundle.js').is_file():
        raise ValueError('Frontend is not built. In the source checkout run: npm ci; npm run build')


def main():
    parser = argparse.ArgumentParser(prog='lalo', description='Lalo Lite local document reader')
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('--home', help='Override the private user data directory')
    commands = parser.add_subparsers(dest='command')
    opened = commands.add_parser('open', help='Open the reader or a bound document')
    opened.add_argument('--document')
    opened.add_argument('--project', default=os.getcwd())
    opened.add_argument('--mode', choices=['cloud', 'codex', 'claude', 'tiered'], default='cloud')
    opened.add_argument('--session')
    opened.add_argument('--no-open', action='store_true')
    init = commands.add_parser('init', help='Create config and optionally install the voice model')
    models = init.add_mutually_exclusive_group()
    models.add_argument('--model-file')
    models.add_argument('--download-model', action='store_true')
    served = commands.add_parser('serve', help='Run the service in the foreground')
    served.add_argument('--meeting', action='store_true', help='Enable the configured HTTPS LAN meeting listener')
    commands.add_parser('doctor', help='Report configuration and capabilities without secrets')
    commands.add_parser('stop', help='Stop this user instance')
    args = parser.parse_args()
    if args.home:
        os.environ['LALO_HOME'] = str(Path(args.home).expanduser().resolve())
    try:
        from .paths import DATA_ROOT, RESOURCE_ROOT
        if args.command == 'serve':
            serve(meeting=args.meeting)
            return
        if args.command == 'init':
            result = initialize(args.model_file, args.download_model)
        elif args.command == 'doctor':
            from .settings import Settings
            from .readiness import report
            result = {'version': VERSION, 'dataDirectory': str(DATA_ROOT),
                'configExists': (DATA_ROOT / '.env').exists(),
                **report(Settings()), 'running': bool(running_server())}
        elif args.command == 'stop':
            current = running_server()
            result = local_request(current, '/shutdown', 'POST') if current else {'stopped': False, 'reason': 'not_running'}
        else:
            initialize()
            document = getattr(args, 'document', None)
            mode = getattr(args, 'mode', 'cloud')
            session, host = source_id(mode, getattr(args, 'session', None))
            if mode=='claude' or (mode=='tiered' and host=='claude'):
                from .settings import Settings
                from .commands import claude_command
                claude_command(Settings().get('CLAUDE_COMMAND'))
            launch = prepare(DATA_ROOT, args.project, document, mode, session, host) if document else None
            info = ensure_server()
            url = f'{info.get("scheme", "http")}://127.0.0.1:{info["port"]}/#access={info["token"]}'
            if launch:
                url += '&launch=' + launch
            result = {'url': url, 'mode': mode, 'source': session, 'host': host,
                'contextMode': 'fork' if mode != 'cloud' else 'standalone'}
            if not getattr(args, 'no_open', False):
                webbrowser.open(url)
        print(json.dumps(result, ensure_ascii=True))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)

"""Launch Lalo from a source checkout or an installed application."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def source_id(mode, explicit=None, environ=None):
    env = os.environ if environ is None else environ
    key = 'CLAUDE_CODE_SESSION_ID'
    if mode == 'cloud':
        return '', ''
    if mode == 'claude':
        value = explicit or env.get('CLAUDE_SESSION_ID') or env.get('CLAUDE_CODE_SESSION_ID')
        host = 'claude'
    elif mode == 'codex':
        value = explicit or env.get('CODEX_THREAD_ID')
        key, host = 'CODEX_THREAD_ID', 'codex'
    else:  # tiered: the deep backend follows whichever host session is available
        if explicit and explicit != env.get('CLAUDE_SESSION_ID') and explicit != env.get('CLAUDE_CODE_SESSION_ID'):
            value, host = explicit, 'codex'
        elif env.get('CLAUDE_SESSION_ID') or env.get('CLAUDE_CODE_SESSION_ID'):
            value, host = env.get('CLAUDE_SESSION_ID') or env.get('CLAUDE_CODE_SESSION_ID'), 'claude'
        elif env.get('CODEX_THREAD_ID'):
            value, host, key = env.get('CODEX_THREAD_ID'), 'codex', 'CODEX_THREAD_ID'
        else:
            value = None
            host = 'codex'
    if not value or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', value):
        raise ValueError(f'Current session identity unavailable. Supply --session or {key}; do not guess.')
    return value, host


def binary(explicit=None):
    skill = Path(__file__).resolve().parents[1]
    bundled = skill.parents[1] / 'app/windows-x64/lalo.exe'
    pointer = skill / 'app-path.txt'
    value = (explicit or os.environ.get('LALO_BINARY')
             or (pointer.read_text(encoding='utf-8-sig').strip() if pointer.is_file() else None)
             or (str(bundled) if bundled.is_file() else None) or shutil.which('lalo'))
    if not value:
        raise ValueError('Install Lalo Lite and set LALO_BINARY to its executable, or pass --app.')
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('Expected the Lalo Lite executable path')
    return str(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--document', required=True)
    parser.add_argument('--project', default=os.getcwd())
    parser.add_argument('--mode', required=True, choices=['codex', 'claude', 'tiered'])
    parser.add_argument('--session')
    parser.add_argument('--app', help='Installed Lalo Lite executable')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    try:
        session, _ = source_id(args.mode, args.session)
        command = application_command(args.app) + ['open', '--document', str(Path(args.document).resolve(strict=True)),
            '--project', str(Path(args.project).resolve(strict=True)), '--mode', args.mode,
            '--session', session]
        if args.no_open:
            command.append('--no-open')
        result = subprocess.run(command, shell=False)
        raise SystemExit(result.returncode)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


def application_command(explicit=None):
    if explicit or os.environ.get('LALO_BINARY'):
        return [binary(explicit)]
    skill = Path(__file__).resolve().parents[1]
    pointer = skill / 'source-root.txt'
    configured = os.environ.get('LALO_SOURCE') or (pointer.read_text(encoding='utf-8-sig').strip() if pointer.is_file() else None)
    root = Path(configured).expanduser().resolve() if configured else skill.parents[1]
    if (root / 'lalo.py').is_file():
        python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not python.is_file():
            raise ValueError('Create the Lalo checkout .venv and install its requirements first; see README.md.')
        return [str(python.resolve()), str((root / 'lalo.py').resolve())]
    if configured:
        raise ValueError('Expected LALO_SOURCE/source-root.txt to point to a Lalo checkout containing lalo.py')
    return [binary()]


if __name__ == '__main__':
    main()

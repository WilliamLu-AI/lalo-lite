"""Apache-2.0 adapter for the separately distributed Lalo Lite application."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def source_id(mode, explicit=None, environ=None):
    env = os.environ if environ is None else environ
    key = 'CODEX_THREAD_ID' if mode == 'codex' else 'CLAUDE_CODE_SESSION_ID'
    value = explicit or (env.get('CLAUDE_SESSION_ID') if mode == 'claude' else None) or env.get(key)
    if not value or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', value):
        raise ValueError(f'Current session identity unavailable. Supply --session or {key}; do not guess.')
    return value


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
    parser.add_argument('--mode', required=True, choices=['codex', 'claude'])
    parser.add_argument('--session')
    parser.add_argument('--app', help='Installed Lalo Lite executable')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    try:
        command = [binary(args.app), 'open', '--document', str(Path(args.document).resolve(strict=True)),
            '--project', str(Path(args.project).resolve(strict=True)), '--mode', args.mode,
            '--session', source_id(args.mode, args.session)]
        if args.no_open:
            command.append('--no-open')
        result = subprocess.run(command, shell=False)
        raise SystemExit(result.returncode)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()

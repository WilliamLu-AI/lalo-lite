"""Read-only application resources and writable per-user data."""
import os
import sys
from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parents[1]


def data_root():
    if os.environ.get('LALO_HOME'):
        return Path(os.environ['LALO_HOME']).expanduser().resolve()
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'LaloLite'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/LaloLite'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'lalo-lite'


DATA_ROOT = data_root()
MODEL_PATH = DATA_ROOT / 'models/smart-turn.onnx'


def ensure_data():
    DATA_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('run', 'tmp/launches', 'tmp/conversations', 'models'):
        (DATA_ROOT / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    return DATA_ROOT

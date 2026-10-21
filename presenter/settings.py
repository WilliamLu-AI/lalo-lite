import os
from pathlib import Path
from dotenv import dotenv_values

from .paths import RESOURCE_ROOT, DATA_ROOT, MODEL_PATH

ROOT = RESOURCE_ROOT


class Settings:
    def __init__(self):
        self.values = {**dotenv_values(DATA_ROOT / '.env'), **os.environ}

    def get(self, name, default=''):
        return self.values.get(name) or default

    def capabilities(self):
        return {
            'asr': bool(self.get('OMNI_API_KEY')),
            'tts': bool(self.get('MINIMAX_API_KEY')),
            'chat': bool(self.get('OPENAI_API_KEY')),
            'tad': MODEL_PATH.is_file(),
            'codex': bool(self.get('CODEX_BINARY')),
            'tiered': bool((self.get('CODEX_BINARY') or self.get('CLAUDE_COMMAND')) and self.get('OPENAI_API_KEY')),
            'claude': bool(self.get('CLAUDE_COMMAND')),
        }

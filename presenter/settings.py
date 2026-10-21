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
        from .readiness import report
        return report(self)['capabilities']

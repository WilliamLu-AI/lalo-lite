import json
import re
import time
from pathlib import Path
from .paths import DATA_ROOT as ROOT


def read_launch(token, directory=None):
    if not re.fullmatch(r'[a-f0-9]{64}', token):
        raise ValueError('Invalid launch token')
    file = (directory or ROOT / 'tmp/launches') / (token + '.json')
    data = json.loads(file.read_text(encoding='utf-8'))
    if data['expiresAt'] < time.time():
        raise ValueError('Launch expired; run the skill again')
    return data

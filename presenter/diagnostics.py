"""Local, bounded conversation/event journal. Never stores credentials or audio."""
import json
import logging
from datetime import datetime, timezone
from .paths import DATA_ROOT as ROOT

class Journal:
    def __init__(self, session):
        self.path = ROOT / 'tmp' / 'conversations' / (session + '.jsonl')
        self.session = session
        self.failed = False

    def record(self, event, epoch, stage, mode, /, **data):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > 10_000_000:
                return
            row = dict(time=datetime.now(timezone.utc).isoformat(), session=self.session,
                       epoch=epoch, stage=stage, mode=mode, event=event, data=data)
            with self.path.open('a', encoding='utf-8-sig') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            if not self.failed:
                logging.getLogger('uvicorn.error').error('Conversation journal unavailable session=%s', self.session)
                self.failed = True

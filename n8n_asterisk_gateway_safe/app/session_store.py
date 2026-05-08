import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


class SessionStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, session_id: str) -> Path:
        safe = ''.join(ch for ch in session_id if ch.isalnum() or ch in ('-', '_'))
        return self.base_dir / safe / 'session.json'

    def create(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        session_dir = self.base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        session = {
            **data,
            'session_id': session_id,
            'status': 'created',
            'turn_index': 0,
            'turns': [],
            'finalized': False,
            'created_at': now,
            'updated_at': now,
        }
        self.save(session)
        return session

    def get(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.exists():
            raise KeyError(f'session not found: {session_id}')
        with self._lock:
            return json.loads(path.read_text(encoding='utf-8'))

    def save(self, session: dict[str, Any]) -> None:
        with self._lock:
            session['updated_at'] = self._now()
            path = self._path(session['session_id'])
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(path)

    def update(self, session_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            session = self.get(session_id)
            session.update(changes)
            self.save(session)
            return session

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


store = SessionStore(settings.sessions_dir)

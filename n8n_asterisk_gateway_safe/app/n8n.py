import json
from pathlib import Path
from typing import Any

import httpx

from .config import settings


class N8nError(RuntimeError):
    pass


async def send_turn_to_n8n(*, webhook_url: str, session: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    recording_path = Path(turn['recording_file'])
    if not recording_path.exists():
        raise N8nError(f'recording file not found: {recording_path}')

    data = {
        'event_type': 'turn_result',
        'session_id': session['session_id'],
        'phone': session['phone'],
        'turn_index': str(turn['turn_index']),
        'status': turn.get('status', 'recorded'),
        'uniqueid': turn.get('uniqueid') or '',
        'channel': turn.get('channel') or '',
        'metadata': json.dumps(session.get('metadata') or {}, ensure_ascii=False),
    }
    files = {
        'recording': (
            recording_path.name,
            recording_path.open('rb'),
            'audio/wav',
        )
    }
    try:
        async with httpx.AsyncClient(timeout=settings.N8N_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.post(webhook_url, data=data, files=files)
            response.raise_for_status()
            if not response.content:
                return {'action': 'hangup', 'reason': 'n8n_empty_response'}
            try:
                payload = response.json()
            except ValueError as exc:
                raise N8nError(f'n8n response is not JSON: {response.text[:500]}') from exc
            if not isinstance(payload, dict):
                raise N8nError('n8n response JSON must be an object')
            return payload
    finally:
        files['recording'][1].close()


async def send_finalize_to_n8n(*, webhook_url: str, session: dict[str, Any], status: str, reason: str | None = None) -> None:
    payload = {
        'event_type': 'finalize',
        'session_id': session['session_id'],
        'phone': session['phone'],
        'status': status,
        'reason': reason,
        'turn_count': len(session.get('turns') or []),
        'metadata': session.get('metadata') or {},
    }
    async with httpx.AsyncClient(timeout=settings.N8N_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.post(webhook_url, json=payload)
        response.raise_for_status()

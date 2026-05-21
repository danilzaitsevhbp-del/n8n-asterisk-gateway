#!/usr/bin/env python3
"""Minimal FastAGI server for the n8n Asterisk gateway.

Asterisk dialplan example:
  AGI(agi://127.0.0.1:4573/n8n_gateway?session_id=${SESSION_ID})
  AGI(agi://127.0.0.1:4573/n8n_finalize?session_id=${SESSION_ID}&reason=hangup)
"""
from __future__ import annotations

import json
import logging
import socketserver
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.config import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('fastagi')


class AgiSession:
    def __init__(self, request):
        self.request = request
        self.rfile = request.makefile('rb')
        self.wfile = request.makefile('wb')
        self.env: dict[str, str] = {}

    def read_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            line = raw.decode('utf-8', errors='replace').strip('\r\n')
            if line == '':
                break
            if ':' in line:
                key, value = line.split(':', 1)
                env[key.strip()] = value.strip()
        self.env = env
        log.info('FastAGI env request=%s channel=%s uniqueid=%s', env.get('agi_request'), env.get('agi_channel'), env.get('agi_uniqueid'))
        return env

    def command(self, command: str) -> str:
        log.info('AGI => %s', command)
        self.wfile.write((command + '\n').encode('utf-8'))
        self.wfile.flush()
        raw = self.rfile.readline()
        if not raw:
            raise RuntimeError('Asterisk closed AGI connection')
        response = raw.decode('utf-8', errors='replace').strip('\r\n')
        log.info('AGI <= %s', response)
        return response

    def get_variable(self, name: str) -> str | None:
        response = self.command(f'GET VARIABLE {name}')
        # Example: 200 result=1 (value)
        if 'result=1' not in response:
            return None
        start = response.find('(')
        end = response.rfind(')')
        if start != -1 and end != -1 and end > start:
            return response[start + 1:end]
        return None

    def hangup(self) -> None:
        try:
            self.command('HANGUP')
        except Exception:
            log.exception('HANGUP command failed')


def parse_query(env: dict[str, str]) -> dict[str, str]:
    source = env.get('agi_network_script') or env.get('agi_request') or ''
    parsed = urllib.parse.urlparse(source)
    query = urllib.parse.parse_qs(parsed.query)
    return {k: v[-1] for k, v in query.items() if v}


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 120) -> dict[str, Any]:
    data = None
    headers = {'Content-Type': 'application/json'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if not body:
            return {}
        return json.loads(body.decode('utf-8'))


def stream_file(agi: AgiSession, playback_file: str) -> None:
    # STREAM FILE expects a filename without extension. Absolute paths are allowed by many Asterisk builds.
    agi.command(f'STREAM FILE {playback_file} ""')


def record_file(agi: AgiSession, record_base: Path, max_seconds: int, silence_seconds: int, beep: bool) -> str:
record_base.parent.mkdir(parents=True, exist_ok=True)
record_base.parent.chmod(0o777)
timeout_ms = max_seconds * 1000
    beep_part = ' BEEP' if beep else ''
    agi.command(f'RECORD FILE {record_base} wav "" {timeout_ms} 0{beep_part} s={silence_seconds}')
    return str(record_base) + '.wav'


def finalize(gateway_url: str, session_id: str, status: str, reason: str | None, env: dict[str, str]) -> None:
    if not session_id:
        return
    payload = {
        'session_id': session_id,
        'status': status,
        'reason': reason,
        'uniqueid': env.get('agi_uniqueid'),
        'channel': env.get('agi_channel'),
    }
    try:
        http_json('POST', f'{gateway_url}/asterisk/finalize', payload, timeout=10)
    except Exception as exc:
        log.warning('Finalize failed for session=%s: %s', session_id, exc)


def resolve_session_id(agi: AgiSession, env: dict[str, str]) -> str:
    q = parse_query(env)
    for value in (
        q.get('session_id'),
        env.get('agi_arg_1'),
        agi.get_variable('N8N_SESSION_ID'),
        agi.get_variable('SESSION_ID'),
    ):
        if value:
            return value
    return ''


def handle_gateway_call(agi: AgiSession) -> None:
    env = agi.env
    session_id = resolve_session_id(agi, env)
    gateway_url = (agi.get_variable('N8N_GATEWAY_BASE_URL') or settings.GATEWAY_BASE_URL).rstrip('/')

    if not session_id:
        log.error('No session_id found in FastAGI request/env/channel variables')
        agi.hangup()
        return

    status = 'completed'
    reason: str | None = None

    try:
        current = http_json('GET', f'{gateway_url}/asterisk/session/{session_id}/current-audio', timeout=10)
        playback_file = current['playback_file']
        turn_index = int(current.get('turn_index') or 0)
        max_turns = int(current.get('max_turns') or settings.MAX_TURNS)
        max_seconds = int(current.get('record_max_seconds') or settings.RECORD_MAX_SECONDS)
        silence_seconds = int(current.get('record_silence_seconds') or settings.RECORD_SILENCE_SECONDS)
        beep = bool(current.get('record_beep'))
        recordings_dir = settings.ASTERISK_RECORDINGS_DIR / session_id
        recordings_dir.mkdir(parents=True, exist_ok=True)

        for _ in range(max_turns):
            stream_file(agi, playback_file)

            record_base = recordings_dir / f'reply_{turn_index:03d}_{int(time.time())}'
            recording_file = record_file(agi, record_base, max_seconds, silence_seconds, beep)

            payload = {
                'session_id': session_id,
                'turn_index': turn_index,
                'recording_file': recording_file,
                'uniqueid': env.get('agi_uniqueid'),
                'channel': env.get('agi_channel'),
                'status': 'recorded',
            }
            response = http_json('POST', f'{gateway_url}/asterisk/turn-result', payload, timeout=180)
            action = str(response.get('action') or '').lower()
            if action != 'continue':
                reason = response.get('reason') or 'gateway requested hangup'
                break

            playback_file = response['playback_file']
            turn_index = int(response.get('turn_index') or (turn_index + 1))
        else:
            status = 'max_turns_reached'
            reason = f'max turns reached: {max_turns}'

        finalize(gateway_url, session_id, status, reason, env)
        agi.hangup()
    except Exception as exc:
        status = 'fastagi_error'
        reason = str(exc)
        log.error('FastAGI failed for session=%s: %s\n%s', session_id, exc, traceback.format_exc())
        finalize(gateway_url, session_id, status, reason, env)
        agi.hangup()


def handle_finalize(agi: AgiSession) -> None:
    env = agi.env
    q = parse_query(env)
    session_id = q.get('session_id') or env.get('agi_arg_1') or agi.get_variable('N8N_SESSION_ID') or agi.get_variable('SESSION_ID') or ''
    reason = q.get('reason') or 'hangup'
    gateway_url = (agi.get_variable('N8N_GATEWAY_BASE_URL') or settings.GATEWAY_BASE_URL).rstrip('/')
    finalize(gateway_url, session_id, 'hangup', reason, env)


class FastAgiHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        agi = AgiSession(self.request)
        env = agi.read_env()
        request = env.get('agi_network_script') or env.get('agi_request') or ''
        log.info('FastAGI connection from %s request=%s', self.client_address, request)
        if 'n8n_finalize' in request:
            handle_finalize(agi)
        else:
            handle_gateway_call(agi)


class ThreadingFastAgiServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    bind = (settings.FASTAGI_HOST, int(settings.FASTAGI_PORT))
    log.info('FastAGI listening on %s:%s', bind[0], bind[1])
    with ThreadingFastAgiServer(bind, FastAgiHandler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info('FastAGI stopped')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

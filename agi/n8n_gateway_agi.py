#!/usr/bin/env python3
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_GATEWAY_URL = 'http://127.0.0.1:8088'
DEFAULT_RECORDINGS_DIR = '/var/spool/asterisk/monitor/n8n-gateway'


def log(message: str) -> None:
    sys.stderr.write(f'n8n_gateway_agi: {message}\n')
    sys.stderr.flush()


def read_agi_env() -> dict[str, str]:
    env = {}
    while True:
        line = sys.stdin.readline()
        if line in ('', '\n', '\r\n'):
            break
        if ':' in line:
            key, value = line.strip().split(':', 1)
            env[key.strip()] = value.strip()
    return env


def agi_command(command: str) -> str:
    sys.stdout.write(command + '\n')
    sys.stdout.flush()
    response = sys.stdin.readline().strip()
    log(f'AGI <= {response} for command: {command}')
    return response


def http_json(method: str, url: str, payload: dict | None = None, timeout: int = 120) -> dict:
    data = None
    headers = {'Content-Type': 'application/json'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        if not body:
            return {}
        return json.loads(body.decode('utf-8'))


def stream_file(playback_file: str) -> None:
    # Asterisk AGI STREAM FILE expects filename without extension.
    agi_command(f'STREAM FILE {playback_file} ""')


def record_file(record_base: str, max_seconds: int, silence_seconds: int, beep: bool) -> str:
    timeout_ms = max_seconds * 1000
    beep_part = ' BEEP' if beep else ''
    # RECORD FILE filename format escape_digits timeout [offset_samples] [BEEP] [s=silence]
    agi_command(f'RECORD FILE {record_base} wav "" {timeout_ms} 0{beep_part} s={silence_seconds}')
    return record_base + '.wav'


def finalize(gateway_url: str, session_id: str, status: str, reason: str | None, agi_env: dict[str, str]) -> None:
    if not session_id:
        return
    payload = {
        'session_id': session_id,
        'status': status,
        'reason': reason,
        'uniqueid': agi_env.get('agi_uniqueid'),
        'channel': agi_env.get('agi_channel'),
    }
    try:
        http_json('POST', f'{gateway_url}/asterisk/finalize', payload, timeout=10)
    except Exception as exc:
        log(f'finalize failed: {exc}')


def main() -> int:
    agi_env = read_agi_env()
    session_id = os.environ.get('N8N_SESSION_ID') or agi_env.get('agi_arg_1') or ''
    gateway_url = os.environ.get('N8N_GATEWAY_BASE_URL') or agi_env.get('agi_arg_2') or DEFAULT_GATEWAY_URL
    gateway_url = gateway_url.rstrip('/')

    max_seconds = int(os.environ.get('N8N_RECORD_MAX_SECONDS') or '10')
    silence_seconds = int(os.environ.get('N8N_RECORD_SILENCE_SECONDS') or '2')
    beep = (os.environ.get('N8N_RECORD_BEEP') or 'false').lower() == 'true'
    recordings_dir = Path(os.environ.get('N8N_RECORDINGS_DIR') or DEFAULT_RECORDINGS_DIR) / session_id
    recordings_dir.mkdir(parents=True, exist_ok=True)

    if not session_id:
        log('N8N_SESSION_ID is empty')
        agi_command('HANGUP')
        return 1

    status = 'completed'
    reason = None

    try:
        current = http_json('GET', f'{gateway_url}/asterisk/session/{session_id}/current-audio', timeout=10)
        playback_file = current['playback_file']
        turn_index = int(current.get('turn_index') or 0)
        max_turns = int(current.get('max_turns') or 20)
        max_seconds = int(current.get('record_max_seconds') or max_seconds)
        silence_seconds = int(current.get('record_silence_seconds') or silence_seconds)
        beep = bool(current.get('record_beep'))

        for _ in range(max_turns):
            stream_file(playback_file)

            record_base = recordings_dir / f'reply_{turn_index:03d}_{int(time.time())}'
            recording_file = record_file(str(record_base), max_seconds, silence_seconds, beep)

            payload = {
                'session_id': session_id,
                'turn_index': turn_index,
                'recording_file': recording_file,
                'uniqueid': agi_env.get('agi_uniqueid'),
                'channel': agi_env.get('agi_channel'),
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

        finalize(gateway_url, session_id, status, reason, agi_env)
        agi_command('HANGUP')
        return 0
    except Exception as exc:
        status = 'agi_error'
        reason = str(exc)
        log(traceback.format_exc())
        finalize(gateway_url, session_id, status, reason, agi_env)
        agi_command('HANGUP')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

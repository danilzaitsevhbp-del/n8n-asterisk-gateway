#!/usr/bin/env python3
import json
import os
import sys
import urllib.request

DEFAULT_GATEWAY_URL = 'http://127.0.0.1:8088'


def log(message: str) -> None:
    sys.stderr.write(f'n8n_gateway_finalize: {message}\n')
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


def post_json(url: str, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=data, method='POST', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def main() -> int:
    agi_env = read_agi_env()
    session_id = os.environ.get('N8N_SESSION_ID') or agi_env.get('agi_arg_1') or ''
    gateway_url = (os.environ.get('N8N_GATEWAY_BASE_URL') or agi_env.get('agi_arg_2') or DEFAULT_GATEWAY_URL).rstrip('/')
    if not session_id:
        return 0
    payload = {
        'session_id': session_id,
        'status': 'hangup',
        'reason': 'asterisk h extension',
        'uniqueid': agi_env.get('agi_uniqueid'),
        'channel': agi_env.get('agi_channel'),
    }
    try:
        post_json(f'{gateway_url}/asterisk/finalize', payload)
    except Exception as exc:
        log(f'finalize post failed: {exc}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

import logging
import queue
import socket
import threading
import time
import uuid
from typing import Callable, Any

from .config import settings

log = logging.getLogger(__name__)

AmiPacket = dict[str, str]
EventHandler = Callable[[AmiPacket], None]


class AmiError(RuntimeError):
    pass


class AmiClient:
    def __init__(self):
        self._sock: socket.socket | None = None
        self._file = None
        self._lock = threading.RLock()
        self._reader: threading.Thread | None = None
        self._running = False
        self._responses: dict[str, queue.Queue[AmiPacket]] = {}
        self._event_handler: EventHandler | None = None

    def set_event_handler(self, handler: EventHandler) -> None:
        self._event_handler = handler

    def connect(self) -> None:
        with self._lock:
            if self._sock:
                return
            log.info('Connecting to AMI %s:%s', settings.AMI_HOST, settings.AMI_PORT)
            sock = socket.create_connection(
                (settings.AMI_HOST, settings.AMI_PORT),
                timeout=settings.AMI_CONNECT_TIMEOUT,
            )
            sock.settimeout(None)
            self._sock = sock
            self._file = sock.makefile('rb')
            self._running = True
            self._reader = threading.Thread(target=self._read_loop, name='ami-reader', daemon=True)
            self._reader.start()

        response = self.action({
            'Action': 'Login',
            'Username': settings.AMI_USERNAME,
            'Secret': settings.AMI_SECRET,
            'Events': 'on',
        })
        if response.get('Response', '').lower() != 'success':
            self.close()
            raise AmiError(f'AMI login failed: {response}')
        log.info('AMI connected and logged in')

    def close(self) -> None:
        with self._lock:
            self._running = False
            try:
                if self._sock:
                    self._sock.close()
            finally:
                self._sock = None
                self._file = None

    def action(self, fields: dict[str, Any], timeout: float = 10.0) -> AmiPacket:
        action_id = str(fields.get('ActionID') or uuid.uuid4())
        fields = {**fields, 'ActionID': action_id}
        q: queue.Queue[AmiPacket] = queue.Queue(maxsize=1)
        self._responses[action_id] = q
        try:
            self._send(fields)
            try:
                return q.get(timeout=timeout)
            except queue.Empty as exc:
                raise AmiError(f'AMI action timeout: {fields.get("Action")} {action_id}') from exc
        finally:
            self._responses.pop(action_id, None)

    def originate_call(self, *, session_id: str, phone: str) -> AmiPacket:
        self.connect()
        action_id = f'originate-{session_id}'
        channel = settings.ASTERISK_CHANNEL_TEMPLATE.format(
            phone=phone,
            trunk=settings.ASTERISK_TRUNK,
            tech=settings.ASTERISK_CHANNEL_TECH,
        )
        variables = {
            'N8N_SESSION_ID': session_id,
            'N8N_GATEWAY_BASE_URL': settings.GATEWAY_BASE_URL.rstrip('/'),
            'N8N_PHONE': phone,
            'N8N_RECORD_MAX_SECONDS': str(settings.RECORD_MAX_SECONDS),
            'N8N_RECORD_SILENCE_SECONDS': str(settings.RECORD_SILENCE_SECONDS),
            'N8N_RECORD_BEEP': 'true' if settings.RECORD_BEEP else 'false',
        }
        variable_header = ','.join(f'{k}={v}' for k, v in variables.items())
        return self.action({
            'Action': 'Originate',
            'ActionID': action_id,
            'Channel': channel,
            'Context': settings.ASTERISK_CONTEXT,
            'Exten': settings.ASTERISK_EXTENSION,
            'Priority': settings.ASTERISK_PRIORITY,
            'CallerID': settings.CALLER_ID,
            'Timeout': settings.ORIGINATE_TIMEOUT_MS,
            'Async': 'true',
            'Variable': variable_header,
        })

    def _send(self, fields: dict[str, Any]) -> None:
        data = ''.join(f'{key}: {value}\r\n' for key, value in fields.items()) + '\r\n'
        with self._lock:
            if not self._sock:
                raise AmiError('AMI socket is not connected')
            self._sock.sendall(data.encode('utf-8'))

    def _read_loop(self) -> None:
        while self._running:
            try:
                packet = self._read_packet()
                if not packet:
                    time.sleep(0.05)
                    continue
                action_id = packet.get('ActionID')
                if 'Response' in packet and action_id in self._responses:
                    self._responses[action_id].put(packet)
                if 'Event' in packet and self._event_handler:
                    try:
                        self._event_handler(packet)
                    except Exception:
                        log.exception('AMI event handler failed')
            except OSError:
                if self._running:
                    log.exception('AMI read loop socket error')
                break
            except Exception:
                log.exception('AMI read loop error')
                time.sleep(1)
        self.close()

    def _read_packet(self) -> AmiPacket:
        if self._file is None:
            return {}
        packet: AmiPacket = {}
        while True:
            line = self._file.readline()
            if not line:
                raise OSError('AMI connection closed')
            line = line.decode('utf-8', errors='replace').strip('\r\n')
            if line == '':
                break
            if ':' in line:
                key, value = line.split(':', 1)
                packet[key.strip()] = value.strip()
        return packet


ami_client = AmiClient()

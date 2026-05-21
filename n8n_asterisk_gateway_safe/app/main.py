import asyncio
import logging
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from .ami import ami_client, AmiError
from .audio import prepare_playback_audio, AudioError
from .config import settings
from .models import (
    FinalizeRequest,
    NextActionResponse,
    StartCallRequest,
    StartCallResponse,
    TurnResultRequest,
)
from .n8n import send_finalize_to_n8n, send_turn_to_n8n, N8nError
from .session_store import store

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger(__name__)

app = FastAPI(title='n8n Asterisk Gateway MVP', version='1.0.0')
_main_loop: asyncio.AbstractEventLoop | None = None


def check_token(x_gateway_token: str | None) -> None:
    if settings.GATEWAY_TOKEN and x_gateway_token != settings.GATEWAY_TOKEN:
        raise HTTPException(status_code=401, detail='invalid gateway token')


def session_public(session: dict[str, Any]) -> dict[str, Any]:
    return {
        'session_id': session['session_id'],
        'phone': session['phone'],
        'status': session['status'],
        'turn_index': session.get('turn_index'),
        'finalized': session.get('finalized', False),
        'created_at': session.get('created_at'),
        'updated_at': session.get('updated_at'),
    }


def ami_event_handler(event: dict[str, str]) -> None:
    event_name = event.get('Event')
    action_id = event.get('ActionID', '')
    if event_name != 'OriginateResponse' or not action_id.startswith('originate-'):
        return

    session_id = action_id.removeprefix('originate-')
    response = event.get('Response', '')
    reason = event.get('Reason') or event.get('Cause') or ''
    log.info('OriginateResponse session=%s response=%s reason=%s', session_id, response, reason)

    if response.lower() == 'success':
        try:
            session = store.get(session_id)
            if session.get('status') not in ('in_call', 'finalized'):
                session['status'] = 'answered'
                session['ami_originate_response'] = event
                store.save(session)
        except Exception:
            log.exception('Failed to update successful originate response for %s', session_id)
        return

    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(
            finalize_session(session_id, status='originate_failed', reason=f'OriginateResponse failure reason={reason}'),
            _main_loop,
        )


@app.on_event('startup')
async def on_startup() -> None:
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    settings.AUDIO_WORK_DIR.mkdir(parents=True, exist_ok=True)
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    settings.ASTERISK_SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    settings.ASTERISK_RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    ami_client.set_event_handler(ami_event_handler)
    try:
        await asyncio.to_thread(ami_client.connect)
    except Exception:
        log.exception('AMI connection failed on startup. /start-call will try again later.')


@app.on_event('shutdown')
async def on_shutdown() -> None:
    ami_client.close()


@app.get('/health')
async def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/start-call', response_model=StartCallResponse)
async def start_call(payload: StartCallRequest, x_gateway_token: str | None = Header(default=None)) -> StartCallResponse:
    check_token(x_gateway_token)
    session_id = uuid.uuid4().hex
    phone = payload.phone
    trunk = payload.trunk or settings.ASTERISK_TRUNK
    channel = settings.ASTERISK_CHANNEL_TEMPLATE.format(
        phone=phone,
        trunk=trunk,
        tech=settings.ASTERISK_CHANNEL_TECH,
    )

    session = store.create(session_id, {
        'phone': phone,
        'channel': channel,
        'n8n_webhook_url': str(payload.n8n_webhook_url),
        'finalize_webhook_url': str(payload.finalize_webhook_url) if payload.finalize_webhook_url else str(payload.n8n_webhook_url),
        'metadata': payload.metadata,
        'trunk': trunk,
        'initial_audio_url': str(payload.audio_url),
    })

    try:
        audio = await prepare_playback_audio(session_id, 0, str(payload.audio_url))
        session['current_audio'] = audio
        session['status'] = 'audio_ready'
        store.save(session)
    except AudioError as exc:
        await finalize_session(session_id, status='audio_prepare_failed', reason=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        response = await asyncio.to_thread(ami_client.originate_call, session_id=session_id, phone=phone, trunk=trunk)
    except AmiError as exc:
        await finalize_session(session_id, status='ami_originate_failed', reason=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if response.get('Response', '').lower() not in ('success', 'follows'):
        await finalize_session(session_id, status='ami_originate_rejected', reason=str(response))
        raise HTTPException(status_code=502, detail=response)

    session['status'] = 'originate_sent'
    session['ami_originate_action_response'] = response
    store.save(session)

    return StartCallResponse(session_id=session_id, status='originate_sent', phone=phone, channel=channel)


@app.get('/session/{session_id}')
async def get_session(session_id: str, x_gateway_token: str | None = Header(default=None)) -> dict[str, Any]:
    check_token(x_gateway_token)
    try:
        return session_public(store.get(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get('/asterisk/session/{session_id}/current-audio')
async def current_audio(session_id: str) -> dict[str, Any]:
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audio = session.get('current_audio')
    if not audio or not audio.get('playback_file'):
        raise HTTPException(status_code=409, detail='no current audio for session')
    return {
        'session_id': session_id,
        'turn_index': session.get('turn_index', 0),
        'playback_file': audio['playback_file'],
        'record_max_seconds': settings.RECORD_MAX_SECONDS,
        'record_silence_seconds': settings.RECORD_SILENCE_SECONDS,
        'record_beep': settings.RECORD_BEEP,
        'max_turns': settings.MAX_TURNS,
    }


@app.post('/asterisk/turn-result', response_model=NextActionResponse)
async def turn_result(payload: TurnResultRequest) -> NextActionResponse:
    try:
        session = store.get(payload.session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if session.get('finalized'):
        return NextActionResponse(
            action='hangup',
            session_id=payload.session_id,
            turn_index=session.get('turn_index'),
            reason='session already finalized',
        )

    turn = payload.model_dump()
    session.setdefault('turns', []).append(turn)
    session['status'] = 'turn_recorded'
    store.save(session)

    if len(session.get('turns') or []) >= settings.MAX_TURNS:
        await finalize_session(payload.session_id, status='max_turns_reached', reason=f'MAX_TURNS={settings.MAX_TURNS}')
        return NextActionResponse(action='hangup', session_id=payload.session_id, reason='max turns reached')

    try:
        n8n_response = await send_turn_to_n8n(
            webhook_url=session['n8n_webhook_url'],
            session=session,
            turn=turn,
        )
    except (N8nError, OSError, httpx.HTTPError) as exc:
        await finalize_session(payload.session_id, status='n8n_turn_failed', reason=str(exc))
        return NextActionResponse(action='hangup', session_id=payload.session_id, reason=f'n8n turn failed: {exc}')

    action = str(n8n_response.get('action') or '').lower()
    next_audio_url = n8n_response.get('audio_url') or n8n_response.get('next_audio_url')

    if action in ('hangup', 'finish', 'stop', 'end'):
        await finalize_session(payload.session_id, status='completed', reason=str(n8n_response.get('reason') or 'n8n requested hangup'))
        return NextActionResponse(action='hangup', session_id=payload.session_id, reason='n8n requested hangup')

    if not next_audio_url:
        await finalize_session(payload.session_id, status='completed', reason='n8n response without next audio_url')
        return NextActionResponse(action='hangup', session_id=payload.session_id, reason='n8n response without audio_url')

    next_turn_index = int(session.get('turn_index') or 0) + 1
    try:
        audio = await prepare_playback_audio(payload.session_id, next_turn_index, str(next_audio_url))
    except AudioError as exc:
        await finalize_session(payload.session_id, status='audio_prepare_failed', reason=str(exc))
        return NextActionResponse(action='hangup', session_id=payload.session_id, reason=str(exc))

    session = store.get(payload.session_id)
    session['turn_index'] = next_turn_index
    session['current_audio'] = audio
    session['last_n8n_response'] = n8n_response
    session['status'] = 'next_audio_ready'
    store.save(session)

    return NextActionResponse(
        action='continue',
        session_id=payload.session_id,
        turn_index=next_turn_index,
        playback_file=audio['playback_file'],
    )



@app.post('/asterisk/finalize')
async def finalize(payload: FinalizeRequest) -> JSONResponse:
    result = await finalize_session(
        payload.session_id,
        status=payload.status,
        reason=payload.reason,
        uniqueid=payload.uniqueid,
        channel=payload.channel,
    )
    return JSONResponse(result)


async def finalize_session(
    session_id: str,
    *,
    status: str,
    reason: str | None = None,
    uniqueid: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    try:
        session = store.get(session_id)
    except KeyError:
        log.warning('Finalize requested for unknown session %s', session_id)
        return {'session_id': session_id, 'status': 'unknown_session'}

    if session.get('finalized'):
        return {'session_id': session_id, 'status': session.get('status'), 'finalized': True, 'already_finalized': True}

    session['finalized'] = True
    session['status'] = status
    session['finalize_reason'] = reason
    if uniqueid:
        session['uniqueid'] = uniqueid
    if channel:
        session['final_channel'] = channel
    store.save(session)

    webhook_url = session.get('finalize_webhook_url') or session.get('n8n_webhook_url')
    if webhook_url:
        try:
            await send_finalize_to_n8n(webhook_url=webhook_url, session=session, status=status, reason=reason)
            session['finalize_webhook_sent'] = True
        except Exception as exc:
            log.exception('Finalize webhook failed for session %s', session_id)
            session['finalize_webhook_sent'] = False
            session['finalize_webhook_error'] = str(exc)
        finally:
            store.save(session)

    return {'session_id': session_id, 'status': status, 'finalized': True}

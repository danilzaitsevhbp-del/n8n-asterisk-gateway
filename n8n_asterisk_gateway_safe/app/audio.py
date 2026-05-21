import asyncio
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import settings


class AudioError(RuntimeError):
    pass


def _ensure_dirs(session_id: str) -> tuple[Path, Path]:
    work_dir = settings.AUDIO_WORK_DIR / 'sessions' / session_id / 'downloads'
    sounds_dir = settings.ASTERISK_SOUNDS_DIR / session_id
    work_dir.mkdir(parents=True, exist_ok=True)
    sounds_dir.mkdir(parents=True, exist_ok=True)
    return work_dir, sounds_dir


def _guess_ext(audio_url: str) -> str:
    path = urlparse(audio_url).path.lower()
    for ext in ('.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac'):
        if path.endswith(ext):
            return ext
    return '.bin'


async def download_audio(audio_url: str, target: Path) -> Path:
    async with httpx.AsyncClient(timeout=settings.DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        async with client.stream('GET', audio_url) as response:
            response.raise_for_status()
            with target.open('wb') as f:
                async for chunk in response.aiter_bytes():
                    if chunk:
                        f.write(chunk)
    if target.stat().st_size == 0:
        raise AudioError(f'downloaded audio is empty: {audio_url}')
    return target


def convert_to_asterisk_wav(source: Path, target_wav: Path) -> Path:
    target_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        'ffmpeg',
        '-y',
        '-hide_banner',
        '-loglevel',
        'error',
        '-i',
        str(source),
        '-ac',
        '1',
        '-ar',
        '8000',
        '-sample_fmt',
        's16',
        str(target_wav),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AudioError('ffmpeg is not installed') from exc
    except subprocess.CalledProcessError as exc:
        raise AudioError(f'ffmpeg failed: {exc.stderr.strip()}') from exc
    if not target_wav.exists() or target_wav.stat().st_size == 0:
        raise AudioError(f'converted wav is empty: {target_wav}')
    return target_wav


async def prepare_playback_audio(session_id: str, turn_index: int, audio_url: str) -> dict[str, str]:
    work_dir, sounds_dir = _ensure_dirs(session_id)
    ext = _guess_ext(audio_url)
    source = work_dir / f'input_{turn_index:03d}{ext}'
    target_base = sounds_dir / f'play_{turn_index:03d}'
    target_wav = target_base.with_suffix('.wav')

    await download_audio(audio_url, source)
    await asyncio.to_thread(convert_to_asterisk_wav, source, target_wav)

    return {
        'audio_url': audio_url,
        'source_file': str(source),
        'playback_file': str(target_base),
        'playback_wav': str(target_wav),
    }

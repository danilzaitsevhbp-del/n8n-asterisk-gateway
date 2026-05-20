import re
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


PHONE_RE = re.compile(r'^7\d{10}$')
TRUNK_RE = re.compile(r'^[A-Za-z0-9_.:-]+$')


class StartCallRequest(BaseModel):
    phone: str = Field(..., examples=['79991234567'])
    audio_url: HttpUrl
    n8n_webhook_url: HttpUrl
    finalize_webhook_url: HttpUrl | None = None

    # Optional Asterisk trunk/endpoint.
    # Example: multifon-79326063650
    # If omitted, gateway will use ASTERISK_TRUNK from .env.
    trunk: str | None = Field(default=None, examples=['multifon-79326063650'])

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not PHONE_RE.match(value):
            raise ValueError('phone must match 7XXXXXXXXXX')
        return value

    @field_validator('trunk')
    @classmethod
    def validate_trunk(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        if not TRUNK_RE.match(value):
            raise ValueError('trunk may contain only letters, digits, underscore, dot, colon and dash')
        return value


class StartCallResponse(BaseModel):
    session_id: str
    status: str
    phone: str
    channel: str


class TurnResultRequest(BaseModel):
    session_id: str
    turn_index: int
    recording_file: str
    uniqueid: str | None = None
    channel: str | None = None
    status: str = 'recorded'
    speech_result: str | None = None
    error: str | None = None


class NextActionResponse(BaseModel):
    action: Literal['continue', 'hangup']
    session_id: str
    turn_index: int | None = None
    playback_file: str | None = None
    reason: str | None = None


class FinalizeRequest(BaseModel):
    session_id: str
    status: str = 'completed'
    reason: str | None = None
    uniqueid: str | None = None
    channel: str | None = None

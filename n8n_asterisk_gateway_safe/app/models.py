import re
from typing import Any, Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator

PHONE_RE = re.compile(r'^7\d{10}$')


class StartCallRequest(BaseModel):
    phone: str = Field(..., examples=['79991234567'])
    audio_url: HttpUrl
    n8n_webhook_url: HttpUrl
    finalize_webhook_url: HttpUrl | None = None
    trunk: str | None = Field(default=None, description='Optional Asterisk trunk override, e.g. multifon-79326063650')
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not PHONE_RE.match(value):
            raise ValueError('phone must match 7XXXXXXXXXX')
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

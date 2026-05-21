from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Runtime configuration.

    Important:
    - Do not hard-code real AMI/SIP/n8n credentials here.
    - Put real values only into a server-side .env file.
    - .env is ignored by git via .gitignore.
    """

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # HTTP gateway
    HOST: str = '127.0.0.1'
    PORT: int = 8088
    GATEWAY_BASE_URL: str = 'http://127.0.0.1:8088'
    GATEWAY_TOKEN: str = ''

    # FastAGI. Asterisk connects here from dialplan via agi://127.0.0.1:4573/...
    FASTAGI_HOST: str = '127.0.0.1'
    FASTAGI_PORT: int = 4573

    # AMI. Real values must be provided in .env on the server.
    AMI_HOST: str = '127.0.0.1'
    AMI_PORT: int = 5038
    AMI_USERNAME: str = ''
    AMI_SECRET: str = ''
    AMI_CONNECT_TIMEOUT: int = 5

    # Asterisk originate
    ASTERISK_CHANNEL_TECH: str = 'PJSIP'
    ASTERISK_TRUNK: str = 'multifon-79326063650'
    ASTERISK_CHANNEL_TEMPLATE: str = 'PJSIP/{phone}@{trunk}'
    ASTERISK_CONTEXT: str = 'n8n-gateway-call'
    ASTERISK_EXTENSION: str = 's'
    ASTERISK_PRIORITY: int = 1
    CALLER_ID: str = 'n8n-gateway <70000000000>'
    ORIGINATE_TIMEOUT_MS: int = 45000

    # Audio paths
    AUDIO_WORK_DIR: Path = Path('/home/danil_z/n8n-asterisk-gateway/data')
    ASTERISK_SOUNDS_DIR: Path = Path('/home/danil_z/n8n-asterisk-gateway/data/sounds')
    ASTERISK_RECORDINGS_DIR: Path = Path('/home/danil_z/n8n-asterisk-gateway/data/recordings')

    # Recording defaults
    RECORD_MAX_SECONDS: int = 10
    RECORD_SILENCE_SECONDS: int = 2
    RECORD_BEEP: bool = False
    MAX_TURNS: int = 20

    # HTTP timeouts
    DOWNLOAD_TIMEOUT_SECONDS: int = 30
    N8N_TIMEOUT_SECONDS: int = 120

    @property
    def sessions_dir(self) -> Path:
        return self.AUDIO_WORK_DIR / 'sessions'


settings = Settings()

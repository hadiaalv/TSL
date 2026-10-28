import os
import shutil
import tempfile
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    data_dir: str = str(ROOT / "data")
    seed_demo: bool = False
    # Set true behind HTTPS (nginx/caddy). Forces secure session cookies.
    auth_cookie_secure: bool = False
    # Public site URL, e.g. https://cos.example.com (no trailing slash)
    public_base_url: str = ""

    # App login (session cookie). Bootstrap first user when DB has none.
    auth_secret: str = "change-me-to-a-long-random-string"
    auth_username: str = "admin"
    auth_password: str = "admin1234"
    auth_session_hours: int = 168
    auth_bypass_local: bool = False

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://127.0.0.1:8000/api/google/callback"
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_redirect_uri: str = "http://localhost:8000/api/slack/callback"
    google_drive_portfolio_folder_id: str = ""
    google_drive_brand_folder_map: dict[str, str] = Field(default_factory=dict)
    google_drive_brand_file_map: dict[str, list[str]] = Field(default_factory=dict)

    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_allowed_phone: str = ""
    whatsapp_username: str = ""
    whatsapp_api_version: str = "v22.0"
    whatsapp_provider: str = "meta"
    d360_api_key: str = ""
    d360_api_base_url: str = "https://waba-sandbox.360dialog.io/v1"
    d360_webhook_auth: str = "token"
    d360_webhook_username: str = ""
    d360_webhook_password: str = ""
    d360_webhook_token: str = ""

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "cos.db"

    @property
    def google_token_path(self) -> Path:
        return Path(self.data_dir) / "google_token.json"

    @property
    def slack_ready(self) -> bool:
        return bool(self.slack_client_id and self.slack_client_secret)

    @property
    def effective_slack_redirect_uri(self) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/") + "/api/slack/callback"
        return self.slack_redirect_uri

    @property
    def google_ready(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def effective_google_redirect_uri(self) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/") + "/api/google/callback"
        return self.google_redirect_uri


settings = Settings()

# Serverless hosts (Vercel) mount the project read-only; only /tmp is writable.
if os.environ.get("VERCEL") and not os.environ.get("DATA_DIR"):
    _src = Path(settings.data_dir)
    _dst = Path(tempfile.gettempdir()) / "tsl-data"
    if not _dst.exists():
        if _src.exists():
            shutil.copytree(_src, _dst, ignore=shutil.ignore_patterns("backups"))
        else:
            _dst.mkdir(parents=True)
    settings.data_dir = str(_dst)

Path(settings.data_dir).mkdir(parents=True, exist_ok=True)

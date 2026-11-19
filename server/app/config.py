from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    session_secret: str = ""
    secure_session_cookie: bool = False
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    github_client_id: str = ""
    github_client_secret: str = ""
    github_api_token: str = ""
    github_oauth_callback_url: str = "http://127.0.0.1:8765/api/auth/github/callback"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_callback_url: str = "http://127.0.0.1:8765/api/auth/google/callback"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    frontend_url: str = "http://127.0.0.1:5173/"
    github_api_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_indexed_files: int = Field(default=1800, ge=100, le=10_000)
    max_source_files: int = Field(default=120, ge=0, le=250)
    max_source_bytes: int = Field(default=96_000, ge=1_000, le=250_000)
    max_source_total_bytes: int = Field(default=8_000_000, ge=100_000, le=20_000_000)

    @property
    def origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def neo4j_configured(self) -> bool:
        return bool(self.neo4j_uri and self.neo4j_password)

    @property
    def github_oauth_configured(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def email_auth_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.smtp_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()

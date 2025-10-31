from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    TELEGRAM_BOT_TOKEN: str = Field(..., description="Telegram bot token from BotFather")
    TIMEZONE: str = Field("Europe/Moscow", description="Default timezone for reminders")
    BASE_CURRENCY: str = Field("RUB", description="Base currency for analytics")
    TINKOFF_API_TOKEN: str | None = Field(default=None, description="Optional Tinkoff Invest API token")
    DATABASE_URL: str = Field("sqlite+aiosqlite:///./finance.db", description="SQLAlchemy database URL")
    TINKOFF_IGNORE_ACCOUNT_IDS: str | None = Field(default=None, description="Comma-separated account IDs to ignore in sync")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


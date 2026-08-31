from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else None


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Переменная {name} не заполнена")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    journal_username: str
    journal_password: str
    allowed_user_id: int | None
    notification_chat_id: int | None
    notification_time: time
    timezone: ZoneInfo
    journal_api_url: str
    request_timeout_seconds: float
    log_dir: Path
    log_level: str
    cache_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        timezone = ZoneInfo(os.getenv("TIMEZONE", "Asia/Yekaterinburg"))
        hour, minute = map(int, os.getenv("NOTIFICATION_TIME", "07:30").split(":"))
        return cls(
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            journal_username=_required("JOURNAL_USERNAME"),
            journal_password=_required("JOURNAL_PASSWORD"),
            allowed_user_id=_optional_int("ALLOWED_TELEGRAM_USER_ID"),
            notification_chat_id=_optional_int("NOTIFICATION_CHAT_ID"),
            notification_time=time(hour, minute, tzinfo=timezone),
            timezone=timezone,
            journal_api_url=os.getenv(
                "JOURNAL_API_URL", "https://msapi.top-academy.ru/api/v2"
            ).rstrip("/"),
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
            log_dir=Path(os.getenv("LOG_DIR", "logs")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            cache_file=Path(os.getenv("CACHE_FILE", "data/schedule_cache.json")),
        )

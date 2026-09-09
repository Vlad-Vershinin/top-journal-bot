from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UserRequestStats:
    user_id: int
    username: str | None
    full_name: str | None
    first_seen: datetime
    last_seen: datetime
    total: int
    commands: dict[str, int]


class RequestStats:
    """Persistent command counters grouped by Telegram user ID."""

    def __init__(self, path: Path, timezone: ZoneInfo) -> None:
        self.path = path
        self.timezone = timezone
        self._lock = threading.RLock()

    def record(
        self,
        user_id: int,
        username: str | None,
        full_name: str | None,
        command: str,
    ) -> None:
        now = datetime.now(self.timezone).isoformat()
        with self._lock:
            data = self._read()
            users = data.setdefault("users", {})
            entry = users.setdefault(
                str(user_id),
                {
                    "username": username,
                    "full_name": full_name,
                    "first_seen": now,
                    "last_seen": now,
                    "total": 0,
                    "commands": {},
                },
            )
            entry["username"] = username
            entry["full_name"] = full_name
            entry["last_seen"] = now
            entry["total"] = int(entry.get("total", 0)) + 1
            commands = entry.setdefault("commands", {})
            commands[command] = int(commands.get(command, 0)) + 1
            self._write(data)

    def get_user(self, user_id: int) -> UserRequestStats | None:
        with self._lock:
            entry = self._read().get("users", {}).get(str(user_id))
        return self._parse_user(user_id, entry)

    def all_users(self) -> list[UserRequestStats]:
        with self._lock:
            users = self._read().get("users", {})
        parsed = [
            stats
            for raw_id, entry in users.items()
            if raw_id.isdigit()
            and (stats := self._parse_user(int(raw_id), entry)) is not None
        ]
        return sorted(parsed, key=lambda item: (-item.total, item.user_id))

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "users": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"version": 1, "users": {}}
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Could not read request statistics %s", self.path)
            return {"version": 1, "users": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _parse_user(user_id: int, entry: Any) -> UserRequestStats | None:
        if not isinstance(entry, dict):
            return None
        try:
            return UserRequestStats(
                user_id=user_id,
                username=entry.get("username"),
                full_name=entry.get("full_name"),
                first_seen=datetime.fromisoformat(entry["first_seen"]),
                last_seen=datetime.fromisoformat(entry["last_seen"]),
                total=int(entry["total"]),
                commands={
                    str(command): int(count)
                    for command, count in entry.get("commands", {}).items()
                },
            )
        except (KeyError, TypeError, ValueError):
            LOGGER.warning("Ignoring damaged request statistics for user_id=%s", user_id)
            return None

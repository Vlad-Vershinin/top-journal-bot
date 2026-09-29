from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .journal import Lesson


LOGGER = logging.getLogger(__name__)
EKATERINBURG = ZoneInfo("Asia/Yekaterinburg")


def watched_day(now: datetime, today_lessons: list[Lesson]) -> date:
    """Watch today until its final lesson ends, then switch to tomorrow."""
    local = now.astimezone(EKATERINBURG)
    lessons = [lesson for lesson in today_lessons if lesson.day == local.date()]
    if not lessons:
        return local.date() + timedelta(days=1)

    ends: list[datetime] = []
    for lesson in lessons:
        try:
            finish = time.fromisoformat(lesson.finishes_at.strip())
        except ValueError:
            LOGGER.warning("Missing or invalid lesson end time for %s", local.date())
            return local.date()
        end = datetime.combine(local.date(), finish)
        if end.tzinfo is None:
            end = end.replace(tzinfo=EKATERINBURG)
        ends.append(end.astimezone(EKATERINBURG))
    return local.date() if local < max(ends) else local.date() + timedelta(days=1)


def schedule_fingerprint(lessons: list[Lesson]) -> str:
    """Ignore API ordering while comparing the visible schedule fields."""
    rows = [
        json.dumps(asdict(lesson), default=str, ensure_ascii=False, sort_keys=True)
        for lesson in lessons
    ]
    ordered = json.dumps(sorted(rows), ensure_ascii=False)
    return hashlib.sha256(ordered.encode("utf-8")).hexdigest()


class NotificationStore:
    """Subscriptions and per-recipient delivery state on persistent storage."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def subscribe(self, user_id: int) -> bool:
        with self._lock:
            data = self._read()
            subscribers = data["subscribers"]
            key = str(user_id)
            if key in subscribers:
                return False
            subscribers[key] = dict(data["observed"])
            self._write(data)
            return True

    def unsubscribe(self, user_id: int) -> bool:
        with self._lock:
            data = self._read()
            if data["subscribers"].pop(str(user_id), None) is None:
                return False
            self._write(data)
            return True

    def is_subscribed(self, user_id: int) -> bool:
        with self._lock:
            return str(user_id) in self._read()["subscribers"]

    def has_subscribers(self) -> bool:
        with self._lock:
            return bool(self._read()["subscribers"])

    def pending(self, day: date, fingerprint: str) -> list[int]:
        key = day.isoformat()
        with self._lock:
            data = self._read()
            observed = data["observed"]
            if key not in observed:
                observed[key] = fingerprint
                for sent in data["subscribers"].values():
                    sent[key] = fingerprint
                cutoff = (day - timedelta(days=14)).isoformat()
                for versions in (observed, *data["subscribers"].values()):
                    for old_key in list(versions):
                        if old_key < cutoff:
                            del versions[old_key]
                self._write(data)
                return []
            if observed[key] != fingerprint:
                observed[key] = fingerprint
                self._write(data)
            return [
                int(user_id)
                for user_id, sent in data["subscribers"].items()
                if sent.get(key) != fingerprint
            ]

    def mark_sent(self, user_id: int, day: date, fingerprint: str) -> None:
        with self._lock:
            data = self._read()
            sent = data["subscribers"].get(str(user_id))
            if sent is not None:
                sent[day.isoformat()] = fingerprint
                self._write(data)

    def _read(self) -> dict[str, Any]:
        empty = {"version": 1, "subscribers": {}, "observed": {}}
        if not self.path.exists():
            return empty
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            if not isinstance(data.get("subscribers"), dict) or not isinstance(
                data.get("observed"), dict
            ):
                raise ValueError("Invalid notification store")
            return data
        except (OSError, ValueError):
            LOGGER.exception("Could not read notification store %s", self.path)
            return empty

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

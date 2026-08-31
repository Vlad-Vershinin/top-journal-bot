from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .journal import Lesson


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CachedSchedule:
    lessons: list[Lesson]
    updated_at: datetime


class ScheduleCache:
    """Small persistent per-day cache with atomic disk writes."""

    def __init__(self, path: Path, timezone: ZoneInfo) -> None:
        self.path = path
        self.timezone = timezone
        self._lock = threading.RLock()

    def store_range(self, start: date, end: date, lessons: list[Lesson]) -> None:
        now = datetime.now(self.timezone)
        grouped: dict[str, list[dict[str, Any]]] = {}
        current = start
        while current <= end:
            grouped[current.isoformat()] = []
            current += timedelta(days=1)
        for lesson in lessons:
            if start <= lesson.day <= end:
                grouped[lesson.day.isoformat()].append(self._lesson_to_dict(lesson))

        with self._lock:
            data = self._read()
            days = data.setdefault("days", {})
            for day_key, day_lessons in grouped.items():
                days[day_key] = {
                    "updated_at": now.isoformat(),
                    "lessons": day_lessons,
                }
            self._write(data)
        LOGGER.info("Schedule cache updated for %s through %s", start, end)

    def load_range(self, start: date, end: date) -> CachedSchedule | None:
        with self._lock:
            days = self._read().get("days", {})
        lessons: list[Lesson] = []
        timestamps: list[datetime] = []
        current = start
        found = False
        while current <= end:
            entry = days.get(current.isoformat())
            if entry:
                found = True
                try:
                    timestamps.append(datetime.fromisoformat(entry["updated_at"]))
                    lessons.extend(
                        Lesson.from_api(item) for item in entry.get("lessons", [])
                    )
                except (KeyError, TypeError, ValueError):
                    LOGGER.warning("Ignoring damaged cache entry for %s", current)
            current += timedelta(days=1)
        if not found or not timestamps:
            return None
        lessons.sort(key=lambda item: (item.day, item.number, item.starts_at))
        return CachedSchedule(lessons=lessons, updated_at=min(timestamps))

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "days": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"version": 1, "days": {}}
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Could not read schedule cache %s", self.path)
            return {"version": 1, "days": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    @staticmethod
    def _lesson_to_dict(lesson: Lesson) -> dict[str, Any]:
        raw = asdict(lesson)
        raw["date"] = raw.pop("day").isoformat()
        raw["lesson"] = raw.pop("number")
        raw["started_at"] = raw.pop("starts_at")
        raw["finished_at"] = raw.pop("finishes_at")
        raw["subject_name"] = raw.pop("subject")
        raw["teacher_name"] = raw.pop("teacher")
        raw["room_name"] = raw.pop("room")
        return raw


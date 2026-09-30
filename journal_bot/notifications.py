from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .journal import Lesson


LOGGER = logging.getLogger(__name__)
EKATERINBURG = ZoneInfo("Asia/Yekaterinburg")


def _service_field(field: str, value: str) -> bool:
    value = value.casefold()
    if field == "room":
        return "дистант" in value or "дистанц" in value
    return field == "teacher" and bool(
        re.search(r"\bпреподаватель\b.*\bпрактик", value)
    )


def schedule_changes(before: list[Lesson], after: list[Lesson]) -> list[str]:
    """Describe changes by lesson slot, omitting service room/teacher edits."""
    changes: list[str] = []
    slots = sorted({(lesson.day, lesson.number) for lesson in before + after})
    fields = (
        ("subject", "Предмет"),
        ("starts_at", "Начало"),
        ("finishes_at", "Окончание"),
        ("teacher", "Преподаватель"),
        ("room", "Аудитория"),
    )
    for day, number in slots:
        old = sorted(
            (lesson for lesson in before if (lesson.day, lesson.number) == (day, number)),
            key=lambda lesson: (lesson.subject, lesson.starts_at, lesson.teacher, lesson.room),
        )
        new = sorted(
            (lesson for lesson in after if (lesson.day, lesson.number) == (day, number)),
            key=lambda lesson: (lesson.subject, lesson.starts_at, lesson.teacher, lesson.room),
        )
        # Match identical entries first when a slot contains several lessons.
        for lesson in old[:]:
            if lesson in new:
                old.remove(lesson)
                new.remove(lesson)
        paired = min(len(old), len(new))
        for previous, current in zip(old[:paired], new[:paired]):
            details = []
            for field, label in fields:
                was, now = getattr(previous, field), getattr(current, field)
                if was != now and not (
                    _service_field(field, was) or _service_field(field, now)
                ):
                    details.append(f"{label}: {was or '—'} → {now or '—'}")
            if details:
                changes.append(f"{number}-я пара ({current.subject}):\n" + "\n".join(details))
        for lesson in old[paired:]:
            changes.append(
                f"Убрана {number}-я пара: {lesson.subject} "
                f"({lesson.starts_at}–{lesson.finishes_at})"
            )
        for lesson in new[paired:]:
            changes.append(
                f"Добавлена {number}-я пара: {lesson.subject} "
                f"({lesson.starts_at}–{lesson.finishes_at})"
            )
    return changes


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
    rows = []
    for lesson in lessons:
        row = asdict(lesson)
        for field in ("room", "teacher"):
            if _service_field(field, row[field]):
                row[field] = ""
        rows.append(json.dumps(row, default=str, ensure_ascii=False, sort_keys=True))
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

    def pending(
        self, day: date, fingerprint: str, lessons: list[Lesson] | None = None
    ) -> list[int]:
        key = day.isoformat()
        with self._lock:
            data = self._read()
            observed = data["observed"]
            snapshots = data.setdefault("snapshots", {})
            versions = snapshots.setdefault(key, {})
            # Old installations have only hashes: establish a baseline once
            # rather than announcing changes without the previous schedule.
            missing_baseline = lessons is not None and observed.get(key) not in versions
            if lessons is not None:
                versions[fingerprint] = [
                    {**asdict(lesson), "day": lesson.day.isoformat()} for lesson in lessons
                ]
            if key not in observed or missing_baseline:
                observed[key] = fingerprint
                for sent in data["subscribers"].values():
                    sent[key] = fingerprint
                cutoff = (day - timedelta(days=14)).isoformat()
                for saved in (observed, snapshots, *data["subscribers"].values()):
                    for old_key in list(saved):
                        if old_key < cutoff:
                            del saved[old_key]
                self._write(data)
                return []
            changed = observed[key] != fingerprint
            if changed:
                observed[key] = fingerprint
            if lessons is not None or changed:
                self._write(data)
            return [
                int(user_id)
                for user_id, sent in data["subscribers"].items()
                if sent.get(key) != fingerprint
            ]

    def changes_for(self, user_id: int, day: date, lessons: list[Lesson]) -> list[str] | None:
        with self._lock:
            data = self._read()
            previous = data["subscribers"].get(str(user_id), {}).get(day.isoformat())
            rows = data.get("snapshots", {}).get(day.isoformat(), {}).get(previous)
        if rows is None:
            return None
        before = [Lesson(**{**row, "day": date.fromisoformat(row["day"])}) for row in rows]
        return schedule_changes(before, lessons)

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

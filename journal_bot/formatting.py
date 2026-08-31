from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape

from .journal import Lesson


WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)


def format_schedule(lessons: list[Lesson], start: date, end: date) -> str:
    if not lessons:
        if start == end:
            return f"📅 <b>{WEEKDAYS[start.weekday()]}, {start:%d.%m.%Y}</b>\n\nЗанятий нет."
        return f"📅 <b>{start:%d.%m.%Y}–{end:%d.%m.%Y}</b>\n\nЗанятий нет."

    grouped: dict[date, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        grouped[lesson.day].append(lesson)

    blocks: list[str] = []
    for day, day_lessons in grouped.items():
        lines = [f"📅 <b>{WEEKDAYS[day.weekday()]}, {day:%d.%m.%Y}</b>"]
        for lesson in day_lessons:
            interval = " – ".join(x for x in (lesson.starts_at, lesson.finishes_at) if x)
            prefix = f"{lesson.number}. " if lesson.number else "• "
            lines.append(f"\n<b>{prefix}{escape(lesson.subject)}</b>")
            if interval:
                lines.append(f"🕒 {escape(interval)}")
            if lesson.room:
                lines.append(f"🚪 {escape(lesson.room)}")
            if lesson.teacher:
                lines.append(f"👤 {escape(lesson.teacher)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


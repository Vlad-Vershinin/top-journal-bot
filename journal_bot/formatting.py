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


def format_change_notification(
    lessons: list[Lesson], day: date, changes: list[str] | None
) -> list[str]:
    """Keep HTML intact when a change description exceeds Telegram's limit."""
    header = f"🔔 <b>Расписание на {day:%d.%m.%Y} изменилось</b>"
    details = (
        "\n\n".join(changes)
        if changes is not None
        else "Предыдущая версия недоступна. Ниже — актуальное расписание."
    )
    schedule = format_schedule(lessons, day, day)
    text = f"{header}\n\n{escape(details)}\n\n{schedule}"
    if len(text.encode("utf-16-le")) // 2 <= 4000:
        return [text]
    # Split plain text before escaping: even emoji consume at most two UTF-16
    # units each, and HTML entities cannot be cut in half.
    messages = [
        f"{header}\n\n{escape(details[offset:offset + 1800])}"
        for offset in range(0, len(details), 1800)
    ]
    messages.append(
        schedule if len(schedule.encode("utf-16-le")) // 2 <= 4000
        else "Актуальное расписание: /today или /tomorrow."
    )
    return messages


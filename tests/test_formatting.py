from datetime import date

from journal_bot.formatting import format_schedule
from journal_bot.journal import Lesson


def test_empty_day() -> None:
    day = date(2026, 9, 1)
    assert "Занятий нет" in format_schedule([], day, day)


def test_lesson_is_escaped() -> None:
    day = date(2026, 9, 1)
    lesson = Lesson(day, 1, "09:00", "10:20", "C++ & Git", "Иванов", "301")
    text = format_schedule([lesson], day, day)
    assert "C++ &amp; Git" in text
    assert "09:00 – 10:20" in text


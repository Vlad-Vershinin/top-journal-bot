import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from telegram.constants import ParseMode

from journal_bot.bot import ScheduleBot
from journal_bot.cache import ScheduleCache
from journal_bot.journal import JournalUnavailable, Lesson
from journal_bot.notifications import (
    NotificationStore,
    schedule_fingerprint,
    watched_day,
)


def test_watched_day_switches_when_last_lesson_ends():
    zone = ZoneInfo("Asia/Yekaterinburg")
    day = date(2026, 9, 29)
    lessons = [
        Lesson(day, 1, "09:00", "10:30", "Математика", "", "101"),
        Lesson(day, 2, "13:00", "14:30", "Физика", "", "102"),
    ]
    assert watched_day(datetime(2026, 9, 29, 14, 29, tzinfo=zone), lessons) == date(
        2026, 9, 29
    )
    assert watched_day(datetime(2026, 9, 29, 14, 30, tzinfo=zone), lessons) == date(
        2026, 9, 30
    )


def test_watched_day_with_no_lessons_or_missing_end_time():
    zone = ZoneInfo("Asia/Yekaterinburg")
    now = datetime(2026, 9, 29, 9, 0, tzinfo=zone)
    assert watched_day(now, []) == date(2026, 9, 30)
    unknown_end = [
        Lesson(date(2026, 9, 29), 1, "09:00", "", "Математика", "", "101")
    ]
    assert watched_day(now, unknown_end) == date(2026, 9, 29)


def test_fingerprint_ignores_lesson_order_but_detects_edits():
    day = date(2026, 9, 29)
    first = Lesson(day, 1, "09:00", "10:30", "Математика", "Иванов", "101")
    second = Lesson(day, 2, "11:00", "12:30", "Физика", "Петров", "102")
    changed = Lesson(day, 2, "11:00", "12:30", "Физика", "Петров", "103")
    assert schedule_fingerprint([first, second]) == schedule_fingerprint([second, first])
    assert schedule_fingerprint([first, second]) != schedule_fingerprint([first, changed])


def test_subscriptions_baseline_changes_and_delivery_survive_restart(tmp_path):
    path = tmp_path / "notifications.json"
    day = date(2026, 9, 29)
    store = NotificationStore(path)
    assert store.subscribe(1)
    assert not store.subscribe(1)
    assert store.pending(day, "original") == []
    assert store.subscribe(2)
    assert store.pending(day, "changed") == [1, 2]
    store.mark_sent(1, day, "changed")

    restored = NotificationStore(path)
    assert restored.pending(day, "changed") == [2]
    assert restored.unsubscribe(2)
    assert not restored.unsubscribe(2)
    assert restored.pending(day, "changed") == []


def test_change_check_sends_once_after_first_observation(tmp_path):
    class FakeJournal:
        lessons_by_day = {}
        unavailable = False

        async def schedule_for_day(self, day):
            if self.unavailable:
                raise JournalUnavailable("Journal недоступен")
            return self.lessons_by_day.get(day, [])

    class FakeTelegram:
        messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    journal = FakeJournal()
    telegram = FakeTelegram()
    bot = object.__new__(ScheduleBot)
    bot.settings = SimpleNamespace(timezone=ZoneInfo("Asia/Yekaterinburg"))
    bot.journal = journal
    bot.cache = ScheduleCache(tmp_path / "cache.json", ZoneInfo("Asia/Yekaterinburg"))
    bot.notifications = NotificationStore(tmp_path / "notifications.json")
    bot.notifications.subscribe(123)
    context = SimpleNamespace(bot=telegram)

    asyncio.run(bot.check_schedule_changes(context))
    assert telegram.messages == []

    journal.unavailable = True
    asyncio.run(bot.check_schedule_changes(context))
    assert telegram.messages == []
    journal.unavailable = False

    day = datetime.now(ZoneInfo("Asia/Yekaterinburg")).date() + timedelta(days=1)
    journal.lessons_by_day[day] = [
        Lesson(day, 1, "09:00", "10:30", "Математика", "", "101")
    ]
    asyncio.run(bot.check_schedule_changes(context))
    asyncio.run(bot.check_schedule_changes(context))
    assert len(telegram.messages) == 1
    assert telegram.messages[0]["chat_id"] == 123
    assert "Расписание на" in telegram.messages[0]["text"]


def test_cancelling_last_lesson_is_reported_before_switching_to_tomorrow(tmp_path):
    zone = ZoneInfo("Asia/Yekaterinburg")
    day = date(2026, 9, 29)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 29, 9, 0, tzinfo=tz)

    class FakeJournal:
        lessons = [Lesson(day, 1, "13:00", "14:30", "Математика", "", "101")]

        async def schedule_for_day(self, requested_day):
            return self.lessons if requested_day == day else []

    class FakeTelegram:
        messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    bot = object.__new__(ScheduleBot)
    bot.settings = SimpleNamespace(timezone=zone)
    bot.journal = FakeJournal()
    bot.cache = ScheduleCache(tmp_path / "cache.json", zone)
    bot.notifications = NotificationStore(tmp_path / "notifications.json")
    bot.notifications.subscribe(123)
    context = SimpleNamespace(bot=FakeTelegram())

    with patch("journal_bot.bot.datetime", FixedDateTime):
        asyncio.run(bot.check_schedule_changes(context))
        bot.journal.lessons = []
        asyncio.run(bot.check_schedule_changes(context))

    assert len(context.bot.messages) == 1
    assert "Занятий нет" in context.bot.messages[0]["text"]


def test_feedback_has_octopus_and_no_link_preview():
    class FakeMessage:
        calls = []

        async def reply_text(self, text, **kwargs):
            self.calls.append((text, kwargs))

    message = FakeMessage()
    asyncio.run(ScheduleBot._reply_notification_feedback(message, "Подписка включена."))
    text, options = message.calls[0]
    assert '🐙 <a href=' in text
    assert 'href="https://github.com/Vlad-Vershinin/top-journal-bot/issues/new"' in text
    assert options["parse_mode"] == ParseMode.HTML
    assert options["link_preview_options"].is_disabled is True

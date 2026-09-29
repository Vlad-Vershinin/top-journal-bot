import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from telegram.constants import ParseMode
from telegram.error import BadRequest

from journal_bot.bot import ScheduleBot
from journal_bot.journal import JournalUnavailable, Lesson
from journal_bot.notifications import (
    NotificationStore,
    schedule_fingerprint,
    watched_day,
)


def test_watched_day_switches_at_ekaterinburg_1600():
    zone = ZoneInfo("Asia/Yekaterinburg")
    assert watched_day(datetime(2026, 9, 29, 15, 59, tzinfo=zone)) == date(
        2026, 9, 29
    )
    assert watched_day(datetime(2026, 9, 29, 16, 0, tzinfo=zone)) == date(
        2026, 9, 30
    )


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
        lessons = []
        unavailable = False

        async def schedule_for_day(self, day):
            if self.unavailable:
                raise JournalUnavailable("Journal недоступен")
            return self.lessons

    class FakeTelegram:
        messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    journal = FakeJournal()
    telegram = FakeTelegram()
    bot = object.__new__(ScheduleBot)
    bot.settings = SimpleNamespace(timezone=ZoneInfo("Asia/Yekaterinburg"))
    bot.journal = journal
    bot.cache = SimpleNamespace(store_range=lambda *args: None)
    bot.notifications = NotificationStore(tmp_path / "notifications.json")
    bot.notifications.subscribe(123)
    context = SimpleNamespace(bot=telegram)

    asyncio.run(bot.check_schedule_changes(context))
    assert telegram.messages == []

    journal.unavailable = True
    asyncio.run(bot.check_schedule_changes(context))
    assert telegram.messages == []
    journal.unavailable = False

    day = watched_day(datetime.now(ZoneInfo("Asia/Yekaterinburg")))
    journal.lessons = [Lesson(day, 1, "09:00", "10:30", "Математика", "", "101")]
    asyncio.run(bot.check_schedule_changes(context))
    asyncio.run(bot.check_schedule_changes(context))
    assert len(telegram.messages) == 1
    assert telegram.messages[0]["chat_id"] == 123
    assert "Расписание на" in telegram.messages[0]["text"]


def test_feedback_has_github_icon_and_no_link_preview():
    class FakeMessage:
        calls = []

        async def reply_text(self, text, **kwargs):
            self.calls.append((text, kwargs))

    message = FakeMessage()
    asyncio.run(ScheduleBot._reply_notification_feedback(message, "Подписка включена."))
    text, options = message.calls[0]
    assert '<tg-emoji emoji-id=' in text
    assert 'href="https://github.com/Vlad-Vershinin/top-journal-bot/issues/new"' in text
    assert options["parse_mode"] == ParseMode.HTML
    assert options["link_preview_options"].is_disabled is True


def test_feedback_falls_back_when_custom_emoji_is_unavailable():
    class FakeMessage:
        calls = []

        async def reply_text(self, text, **kwargs):
            self.calls.append((text, kwargs))
            if len(self.calls) == 1:
                raise BadRequest("custom emoji unavailable")

    message = FakeMessage()
    asyncio.run(ScheduleBot._reply_notification_feedback(message, "Подписка включена."))
    assert len(message.calls) == 2
    assert "<tg-emoji" not in message.calls[1][0]
    assert message.calls[1][1]["link_preview_options"].is_disabled is True

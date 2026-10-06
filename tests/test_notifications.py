import asyncio
from dataclasses import replace
from html import unescape
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from telegram.constants import ParseMode
from telegram.error import TelegramError

from journal_bot.bot import AFTERNOON_CHECK, MORNING_CHECK, ScheduleBot
from journal_bot.cache import ScheduleCache
from journal_bot.formatting import format_change_notification
from journal_bot.journal import JournalUnavailable, Lesson
from journal_bot.notifications import (
    NotificationStore,
    schedule_changes,
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


def test_notification_checks_follow_yekaterinburg_daytime_windows():
    zone = ZoneInfo("Asia/Yekaterinburg")

    def next_check(hour, minute):
        now = datetime(2026, 9, 29, hour, minute, tzinfo=zone)
        return min(
            trigger.get_next_fire_time(None, now)
            for trigger in (MORNING_CHECK, AFTERNOON_CHECK)
        )

    assert next_check(0, 0) == datetime(2026, 9, 29, 6, 0, tzinfo=zone)
    assert next_check(6, 0) == datetime(2026, 9, 29, 6, 0, tzinfo=zone)
    assert next_check(11, 1) == datetime(2026, 9, 29, 11, 15, tzinfo=zone)
    assert next_check(11, 46) == datetime(2026, 9, 29, 12, 0, tzinfo=zone)
    assert next_check(12, 1) == datetime(2026, 9, 29, 12, 30, tzinfo=zone)
    assert next_check(23, 31) == datetime(2026, 9, 30, 6, 0, tzinfo=zone)


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


def test_change_description_lists_fields_and_added_removed_lessons():
    day = date(2026, 9, 29)
    original = Lesson(day, 1, "09:00", "10:30", "Математика", "Иванов", "101")
    removed = replace(original, number=2)
    changed = replace(
        original, starts_at="09:30", finishes_at="11:00", subject="Физика",
        teacher="Петров", room="102",
    )
    added = replace(original, number=3)
    text = "\n".join(schedule_changes([original, removed], [added, changed]))
    for expected in (
        "Предмет: Математика → Физика", "Начало: 09:00 → 09:30",
        "Окончание: 10:30 → 11:00", "Преподаватель: Иванов → Петров",
        "Аудитория: 101 → 102", "Убрана 2-я пара", "Добавлена 3-я пара",
    ):
        assert expected in text
    assert schedule_changes([original, removed], [removed, original]) == []


def test_remote_room_numbers_are_ignored_but_mode_changes_are_reported():
    day = date(2026, 9, 29)
    original = Lesson(
        day, 1, "09:00", "10:30", "Математика", "Преподаватель практика 10", "Дистант 1"
    )
    changed = replace(original, teacher="ПРЕПОДАВАТЕЛЬ практика №20", room="дистант 2")
    assert schedule_fingerprint([original]) == schedule_fingerprint([changed])
    assert schedule_changes([original], [changed]) == []
    real_fields = replace(original, teacher="Иванов", room="101")
    assert schedule_changes([original], [real_fields]) == [
        "1-я пара (Математика):\nАудитория: Дистант → 101"
    ]
    assert schedule_changes([real_fields], [original]) == [
        "1-я пара (Математика):\nАудитория: 101 → Дистант"
    ]
    assert schedule_fingerprint([original]) != schedule_fingerprint([real_fields])
    changed = replace(changed, starts_at="09:30")
    assert schedule_changes([original], [changed]) == [
        "1-я пара (Математика):\nНачало: 09:00 → 09:30"
    ]


def test_self_study_teacher_labels_are_service_fields():
    original = Lesson(date(2026, 10, 5), 1, "07:30", "08:30", "Иностранный язык",
                      "Самостоятельная работа3", "Дистант 4")
    for teacher in ("Самостоятельная работа4", "Самостоятельная работа № 5",
                    "САМОСТОЯТЕЛЬНАЯ РАБОТА N6", "Самостоятельная работа No7",
                    "Преподаватель практика8"):
        changed = replace(original, teacher=teacher)
        assert schedule_fingerprint([original]) == schedule_fingerprint([changed])
        assert schedule_changes([original], [changed]) == []
    changed = replace(original, starts_at="08:00")
    assert schedule_changes([original], [changed]) == [
        "1-я пара (Иностранный язык):\nНачало: 07:30 → 08:00"
    ]
    assert schedule_changes([original], []) == [
        "Убрана 1-я пара: Иностранный язык (07:30–08:30)"
    ]
    actual_teacher = replace(original, teacher="Иванов")
    assert schedule_changes([actual_teacher], [replace(actual_teacher, teacher="Петров")])


def test_notification_diff_survives_cache_updates_restart_and_send_failure(tmp_path):
    day = date(2026, 9, 29)
    original = Lesson(day, 1, "09:00", "10:30", "Математика", "Иванов", "101")
    changed = replace(original, subject="Физика <теория>", room="102")
    bot = object.__new__(ScheduleBot)
    bot.cache = ScheduleCache(tmp_path / "cache.json", ZoneInfo("Asia/Yekaterinburg"))
    path = tmp_path / "notifications.json"
    bot.notifications = NotificationStore(path)
    bot.notifications.subscribe(123)
    bot.notifications.subscribe(456)
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(bot._notify_schedule_change(context, day, [original]))
    context.bot.send_message.assert_not_awaited()
    # A manual /today request must not erase the notification baseline.
    bot.cache.store_range(day, day, [changed])
    context.bot.send_message.side_effect = [TelegramError("temporary"), None]
    asyncio.run(bot._notify_schedule_change(context, day, [changed]))
    assert "Предмет: Математика → Физика &lt;теория&gt;" in (
        context.bot.send_message.await_args_list[0].kwargs["text"]
    )
    bot.notifications = NotificationStore(path)
    context.bot.send_message.reset_mock()
    context.bot.send_message.side_effect = None
    asyncio.run(bot._notify_schedule_change(context, day, [changed]))
    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.await_args.kwargs["chat_id"] == 123
    assert "Аудитория: 101 → 102" in context.bot.send_message.await_args.kwargs["text"]
    context.bot.send_message.reset_mock()
    asyncio.run(bot._notify_schedule_change(context, day, [changed]))
    context.bot.send_message.assert_not_awaited()


def test_legacy_baseline_migrates_and_service_only_changes_do_not_notify(tmp_path):
    day = date(2026, 9, 29)
    original = Lesson(day, 1, "09:00", "10:30", "Математика", "Иванов", "101")
    path = tmp_path / "notifications.json"
    store = NotificationStore(path)
    store.subscribe(123)
    store.pending(day, "legacy-fingerprint")
    bot = object.__new__(ScheduleBot)
    bot.notifications = store
    bot.cache = ScheduleCache(tmp_path / "cache.json", ZoneInfo("Asia/Yekaterinburg"))
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(bot._notify_schedule_change(context, day, [original]))
    context.bot.send_message.assert_not_awaited()
    service = replace(original, teacher="Преподаватель практика №7")
    asyncio.run(bot._notify_schedule_change(context, day, [service]))
    context.bot.send_message.assert_not_awaited()
    changed = replace(service, finishes_at="11:00")
    asyncio.run(bot._notify_schedule_change(context, day, [changed]))
    context.bot.send_message.assert_awaited_once()
    assert "Окончание: 10:30 → 11:00" in context.bot.send_message.await_args.kwargs["text"]


def test_long_change_notifications_preserve_text_and_fit_telegram_limit():
    day = date(2026, 9, 29)
    details = "<Время> 😀 & " * 700
    messages = format_change_notification([], day, [details])
    assert len(messages) > 1
    parts = [unescape(message.split("\n\n", 1)[1]) for message in messages[:-1]]
    assert "".join(parts) == details
    for message in messages:
        plain = unescape(re.sub(r"<[^>]+>", "", message))
        assert len(plain.encode("utf-16-le")) // 2 <= 4096


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


def test_feedback_has_octopus_and_no_link_preview(tmp_path):
    class FakeMessage:
        calls = []

        async def reply_text(self, text, **kwargs):
            self.calls.append((text, kwargs))

    message = FakeMessage()
    bot = object.__new__(ScheduleBot)
    bot.notifications = NotificationStore(tmp_path / "notifications.json")
    asyncio.run(bot._reply_notification_feedback(message, "Подписка включена."))
    text, options = message.calls[0]
    assert '🐙 <a href=' in text
    assert 'href="https://github.com/Vlad-Vershinin/top-journal-bot/issues/new"' in text
    assert options["parse_mode"] == ParseMode.HTML
    assert options["link_preview_options"].is_disabled is True

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

from journal_bot.bot import SCHEDULE_KEYBOARD, ScheduleBot
from journal_bot.stats import RequestStats


def test_start_shows_persistent_schedule_keyboard():
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)
    bot = object.__new__(ScheduleBot)

    asyncio.run(bot.start(update, SimpleNamespace()))

    reply = message.reply_text.await_args
    keyboard = reply.kwargs["reply_markup"]
    assert keyboard is SCHEDULE_KEYBOARD
    assert keyboard.is_persistent is True
    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        ["📅 Сегодня", "📆 Завтра"],
        ["🗓 Неделя"],
    ]


def test_schedule_buttons_use_existing_handlers_and_count_as_commands(tmp_path):
    bot = object.__new__(ScheduleBot)
    path = tmp_path / "stats.json"
    bot.stats = RequestStats(path, ZoneInfo("UTC"))
    bot.today = AsyncMock()
    bot.tomorrow = AsyncMock()
    bot.week = AsyncMock()
    message = SimpleNamespace(text="📅 Сегодня")
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=123, username="test", full_name="Test User"),
        effective_chat=SimpleNamespace(id=123),
    )
    context = SimpleNamespace()

    for label in ("📅 Сегодня", "📆 Завтра", "🗓 Неделя"):
        message.text = label
        asyncio.run(bot.schedule_button(update, context))

    bot.today.assert_awaited_once_with(update, context)
    bot.tomorrow.assert_awaited_once_with(update, context)
    bot.week.assert_awaited_once_with(update, context)
    restored = RequestStats(path, ZoneInfo("UTC")).get_user(123)
    assert restored.total == 3
    assert restored.commands == {"/today": 1, "/tomorrow": 1, "/week": 1}
    assert restored.sources == {"keyboard": 3}
    message.text = "/today"
    asyncio.run(bot.log_command(update, context))
    restored = RequestStats(path, ZoneInfo("UTC")).get_user(123)
    assert restored.total == 4
    assert restored.commands["/today"] == 2
    assert restored.sources == {"keyboard": 3, "command": 1}


def test_schedule_response_keeps_keyboard_visible():
    class FakeJournal:
        async def schedule_for_day(self, day):
            return []

    bot = object.__new__(ScheduleBot)
    bot.journal = FakeJournal()
    bot.cache = SimpleNamespace(store_range=Mock())
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)

    asyncio.run(bot._send_day(update, date(2026, 9, 29)))

    assert message.reply_text.await_args.kwargs["reply_markup"] is SCHEDULE_KEYBOARD

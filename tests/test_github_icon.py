import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.constants import MessageEntityType
from telegram.error import BadRequest

from journal_bot.bot import ScheduleBot
from journal_bot.notifications import NotificationStore


def setup_icon_bot(tmp_path, user_id=123):
    bot = object.__new__(ScheduleBot)
    bot.settings = SimpleNamespace(admin_user_id=123)
    bot.notifications = NotificationStore(tmp_path / "notifications.json")
    message = SimpleNamespace(
        entities=[SimpleNamespace(type=MessageEntityType.CUSTOM_EMOJI, custom_emoji_id="456")],
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message, effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type="private"),
    )
    context = SimpleNamespace(
        args=["🐙"], bot=SimpleNamespace(get_custom_emoji_stickers=AsyncMock(
            return_value=[SimpleNamespace(custom_emoji_id="456", emoji="🐙")]
        )),
    )
    return bot, update, context


def test_admin_sets_icon_from_message_and_it_survives_restart(tmp_path):
    bot, update, context = setup_icon_bot(tmp_path)
    bot.notifications.subscribe(999)
    asyncio.run(bot.set_github_icon(update, context))
    context.bot.get_custom_emoji_stickers.assert_awaited_once_with(["456"])
    restored = NotificationStore(bot.notifications.path)
    assert restored.github_icon() == {"id": "456", "emoji": "🐙"}
    assert restored.is_subscribed(999)
    message = SimpleNamespace(reply_text=AsyncMock())
    bot.notifications = restored
    asyncio.run(bot._reply_notification_feedback(message, "Включено"))
    assert '<tg-emoji emoji-id="456">🐙</tg-emoji> <a href=' in message.reply_text.await_args.args[0]
    assert message.reply_text.await_args.kwargs["link_preview_options"].is_disabled


def test_non_admin_cannot_change_icon(tmp_path):
    bot, update, context = setup_icon_bot(tmp_path, user_id=999)
    asyncio.run(bot.set_github_icon(update, context))
    context.bot.get_custom_emoji_stickers.assert_not_awaited()
    update.effective_message.reply_text.assert_not_awaited()
    assert bot.notifications.github_icon() is None


def test_rejected_preview_keeps_previous_icon(tmp_path):
    bot, update, context = setup_icon_bot(tmp_path)
    original = {"id": "789", "emoji": "🐙"}
    bot.notifications.set_github_icon(original)
    update.effective_message.reply_text.side_effect = [BadRequest("CUSTOM_EMOJI_INVALID"), None]
    asyncio.run(bot.set_github_icon(update, context))
    assert bot.notifications.github_icon() == original


def test_feedback_falls_back_if_custom_emoji_is_rejected(tmp_path):
    bot, update, context = setup_icon_bot(tmp_path)
    bot.notifications.set_github_icon({"id": "456", "emoji": "🐙"})
    update.effective_message.reply_text.side_effect = [BadRequest("CUSTOM_EMOJI_INVALID"), None]
    asyncio.run(bot._reply_notification_feedback(update.effective_message, "Включено"))
    text = update.effective_message.reply_text.await_args.args[0]
    assert "<tg-emoji" not in text
    assert '🐙 <a href=' in text


def test_admin_can_use_numeric_id_and_reset_icon(tmp_path):
    bot, update, context = setup_icon_bot(tmp_path)
    update.effective_message.entities = []
    context.args = ["456"]
    asyncio.run(bot.set_github_icon(update, context))
    assert bot.notifications.github_icon()["id"] == "456"
    context.args = ["off"]
    asyncio.run(bot.set_github_icon(update, context))
    assert bot.notifications.github_icon() is None

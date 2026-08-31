from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .cache import ScheduleCache
from .config import Settings
from .formatting import format_schedule
from .journal import JournalClient, JournalError, JournalUnavailable


LOGGER = logging.getLogger(__name__)


class ScheduleBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.journal = JournalClient(
            settings.journal_api_url,
            settings.journal_username,
            settings.journal_password,
            settings.request_timeout_seconds,
        )
        self.cache = ScheduleCache(settings.cache_file, settings.timezone)

    def build(self) -> Application:
        builder = ApplicationBuilder().token(self.settings.telegram_bot_token)
        if self.settings.telegram_proxy_url:
            # Bot API uses two separate HTTP clients: one for getUpdates and
            # one for all other methods (sendMessage, getMe, deleteWebhook...).
            # Both must use the proxy, otherwise polling may start but replies
            # will still fail on networks where Telegram is blocked.
            builder = builder.proxy(
                self.settings.telegram_proxy_url
            ).get_updates_proxy(self.settings.telegram_proxy_url)
            LOGGER.info(
                "Telegram proxy enabled: %s",
                self._safe_proxy_name(self.settings.telegram_proxy_url),
            )
        application = builder.post_shutdown(self._shutdown).build()
        application.add_handler(
            MessageHandler(filters.COMMAND, self.log_command), group=-1
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("id", self.show_id))
        application.add_handler(CommandHandler("today", self.today))
        application.add_handler(CommandHandler("tomorrow", self.tomorrow))
        application.add_handler(CommandHandler("week", self.week))
        application.add_error_handler(self.on_error)

        if self.settings.notification_chat_id is not None:
            if application.job_queue is None:
                raise RuntimeError("Установите зависимость python-telegram-bot[job-queue]")
            application.job_queue.run_daily(
                self.daily_notification,
                time=self.settings.notification_time,
                name="daily_schedule",
            )
        return application

    async def log_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Write one privacy-conscious audit entry for every bot command."""
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or not message.text:
            return
        raw_command = message.text.split(maxsplit=1)[0]
        command = raw_command.split("@", maxsplit=1)[0].lower()
        LOGGER.info(
            "Command invoked: user_id=%s chat_id=%s command=%s",
            user.id if user else "unknown",
            chat.id if chat else "unknown",
            command,
        )

    @staticmethod
    def _safe_proxy_name(proxy_url: str) -> str:
        """Return a log-safe proxy address without credentials."""
        parsed = urlsplit(proxy_url)
        host = parsed.hostname or "unknown-host"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme or 'proxy'}://{host}{port}"

    def _allowed(self, update: Update) -> bool:
        expected = self.settings.allowed_user_id
        return expected is None or (
            update.effective_user is not None and update.effective_user.id == expected
        )

    async def _deny_if_needed(self, update: Update) -> bool:
        if self._allowed(update):
            return False
        if update.effective_message:
            await update.effective_message.reply_text("Этот бот приватный.")
        return True

    def _today(self) -> date:
        return datetime.now(self.settings.timezone).date()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._deny_if_needed(update) or not update.effective_message:
            return
        await update.effective_message.reply_text(
            "Расписание TOP Academy\n\n"
            "/today — сегодня\n"
            "/tomorrow — завтра\n"
            "/week — текущая неделя\n"
            "/id — ваш Telegram ID"
        )

    async def show_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message and update.effective_user:
            await update.effective_message.reply_text(
                f"Ваш user ID: {update.effective_user.id}\n"
                f"Chat ID: {update.effective_chat.id if update.effective_chat else '—'}"
            )

    async def today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_day(update, self._today())

    async def tomorrow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_day(update, self._today() + timedelta(days=1))

    async def week(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._deny_if_needed(update) or not update.effective_message:
            return
        today = self._today()
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        await self._reply_range(update, start, end)

    async def _send_day(self, update: Update, day: date) -> None:
        if await self._deny_if_needed(update) or not update.effective_message:
            return
        try:
            lessons = await self.journal.schedule_for_day(day)
            self.cache.store_range(day, day, lessons)
            await update.effective_message.reply_text(
                format_schedule(lessons, day, day), parse_mode=ParseMode.HTML
            )
        except JournalUnavailable as exc:
            LOGGER.warning("Journal unavailable for %s: %s", day, exc)
            await update.effective_message.reply_text(
                self._cached_fallback(day, day, str(exc)), parse_mode=ParseMode.HTML
            )
        except JournalError as exc:
            LOGGER.error("Journal request failed for %s: %s", day, exc)
            await update.effective_message.reply_text(f"⚠️ {exc}")

    async def _reply_range(self, update: Update, start: date, end: date) -> None:
        try:
            lessons = await self.journal.schedule_for_range(start, end)
            self.cache.store_range(start, end, lessons)
            text = format_schedule(lessons, start, end)
            await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        except JournalUnavailable as exc:
            LOGGER.warning("Journal unavailable for %s..%s: %s", start, end, exc)
            await update.effective_message.reply_text(
                self._cached_fallback(start, end, str(exc)), parse_mode=ParseMode.HTML
            )
        except JournalError as exc:
            LOGGER.error("Journal request failed for %s..%s: %s", start, end, exc)
            await update.effective_message.reply_text(f"⚠️ {exc}")

    async def daily_notification(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = self.settings.notification_chat_id
        if chat_id is None:
            return
        day = self._today()
        try:
            lessons = await self.journal.schedule_for_day(day)
            self.cache.store_range(day, day, lessons)
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_schedule(lessons, day, day),
                parse_mode=ParseMode.HTML,
            )
        except JournalUnavailable as exc:
            LOGGER.warning("Daily notification failed: %s", exc)
            await context.bot.send_message(
                chat_id=chat_id,
                text=self._cached_fallback(day, day, str(exc)),
                parse_mode=ParseMode.HTML,
            )
        except JournalError as exc:
            LOGGER.error("Daily notification failed: %s", exc)
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ {exc}")

    def _cached_fallback(self, start: date, end: date, reason: str) -> str:
        cached = self.cache.load_range(start, end)
        header = f"⚠️ <b>{reason}</b>"
        if cached is None:
            return f"{header}\n\nСохранённого расписания для этих дат пока нет."
        updated = cached.updated_at.astimezone(self.settings.timezone)
        return (
            f"{header}\n"
            f"Показываю последнее известное расписание "
            f"(обновлено {updated:%d.%m.%Y в %H:%M}).\n\n"
            f"{format_schedule(cached.lessons, start, end)}"
        )

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        LOGGER.exception("Unhandled bot error", exc_info=context.error)

    async def _shutdown(self, application: Application) -> None:
        await self.journal.close()

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from html import escape
from urllib.parse import urlsplit

from telegram import BotCommand, Update
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
from .stats import RequestStats, UserRequestStats


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
        self.stats = RequestStats(settings.stats_file, settings.timezone)

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
        application = (
            builder.post_init(self._post_init)
            .post_shutdown(self._shutdown)
            .build()
        )
        application.add_handler(
            MessageHandler(filters.COMMAND, self.log_command), group=-1
        )
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("id", self.show_id))
        application.add_handler(CommandHandler("today", self.today))
        application.add_handler(CommandHandler("tomorrow", self.tomorrow))
        application.add_handler(CommandHandler("week", self.week))
        application.add_handler(CommandHandler("stats", self.show_stats))
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

    async def _post_init(self, application: Application) -> None:
        """Publish the slash-command menu shown by Telegram clients."""
        commands = [
            BotCommand("start", "Открыть меню бота"),
            BotCommand("today", "Расписание на сегодня"),
            BotCommand("tomorrow", "Расписание на завтра"),
            BotCommand("week", "Расписание на текущую неделю"),
        ]
        await application.bot.set_my_commands(commands)
        LOGGER.info("Telegram command menu published: %s commands", len(commands))

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
        if user is not None:
            try:
                self.stats.record(user.id, user.username, user.full_name, command)
            except OSError:
                LOGGER.exception("Could not persist command statistics")

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

    async def show_stats(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show persistent command statistics only to the configured owner."""
        user = update.effective_user
        message = update.effective_message
        owner_id = self.settings.allowed_user_id
        if message is None or user is None or owner_id is None or user.id != owner_id:
            LOGGER.warning(
                "Stats access denied: user_id=%s", user.id if user else "unknown"
            )
            return

        if context.args:
            try:
                requested_id = int(context.args[0])
            except ValueError:
                await message.reply_text("Использование: /stats [user_id]")
                return
            stats = self.stats.get_user(requested_id)
            if stats is None:
                await message.reply_text(f"Для user ID {requested_id} данных пока нет.")
                return
            await message.reply_text(
                self._format_user_stats(stats), parse_mode=ParseMode.HTML
            )
            return

        users = self.stats.all_users()
        total = sum(item.total for item in users)
        lines = [
            "📊 <b>Статистика команд</b>",
            f"Пользователей: {len(users)}",
            f"Всего запросов: {total}",
            "",
        ]
        for index, stats in enumerate(users[:30], start=1):
            lines.append(
                f"{index}. {self._stats_name(stats)} — {stats.total} "
                f"(<code>{stats.user_id}</code>)"
            )
        if len(users) > 30:
            lines.append(f"…и ещё {len(users) - 30}")
        lines.extend(["", "Подробнее: <code>/stats user_id</code>"])
        await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    def _format_user_stats(self, stats: UserRequestStats) -> str:
        username = f"@{escape(stats.username)}" if stats.username else "—"
        full_name = escape(stats.full_name) if stats.full_name else "—"
        commands = sorted(stats.commands.items(), key=lambda item: (-item[1], item[0]))
        command_lines = [
            f"<code>{escape(command)}</code> — {count}"
            for command, count in commands
        ]
        return "\n".join(
            [
                "📊 <b>Статистика пользователя</b>",
                f"ID: <code>{stats.user_id}</code>",
                f"Username: {username}",
                f"Имя: {full_name}",
                f"Всего запросов: {stats.total}",
                f"Первый: {stats.first_seen.astimezone(self.settings.timezone):%d.%m.%Y %H:%M}",
                f"Последний: {stats.last_seen.astimezone(self.settings.timezone):%d.%m.%Y %H:%M}",
                "",
                "<b>Команды:</b>",
                *(command_lines or ["—"]),
            ]
        )

    @staticmethod
    def _stats_name(stats: UserRequestStats) -> str:
        if stats.username:
            return f"@{escape(stats.username)}"
        return escape(stats.full_name or "без имени")

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
            await update.effective_message.reply_text(
                self._cached_fallback(day, day, str(exc)), parse_mode=ParseMode.HTML
            )

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
            await update.effective_message.reply_text(
                self._cached_fallback(start, end, str(exc)), parse_mode=ParseMode.HTML
            )

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
            await context.bot.send_message(
                chat_id=chat_id,
                text=self._cached_fallback(day, day, str(exc)),
                parse_mode=ParseMode.HTML,
            )

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

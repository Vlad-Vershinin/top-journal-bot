from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
BOT_TOKEN_PATTERN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


class RedactSecretsFilter(logging.Filter):
    """Prevent Telegram bot tokens from reaching console or disk logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = BOT_TOKEN_PATTERN.sub("bot<redacted>", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(log_dir: Path, level_name: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"
    level = getattr(logging, level_name.upper(), logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)
    secret_filter = RedactSecretsFilter()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(secret_filter)
    root.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(secret_filter)
    file_handler.suffix = "%Y-%m-%d"
    root.addHandler(file_handler)

    # HTTPX logs every long-poll request at INFO and includes the Bot API URL.
    # Besides being noisy, that URL contains the Telegram token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _remove_old_logs(log_dir, days=14)
    return log_file


def _remove_old_logs(log_dir: Path, days: int) -> None:
    cutoff = datetime.now().timestamp() - timedelta(days=days).total_seconds()
    for path in log_dir.glob("bot.log.*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            logging.getLogger(__name__).exception("Could not remove old log %s", path)

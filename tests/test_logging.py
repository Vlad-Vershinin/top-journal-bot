import logging

from journal_bot.logging_setup import RedactSecretsFilter


def test_telegram_token_is_redacted() -> None:
    record = logging.LogRecord(
        name="httpx",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="POST https://api.telegram.org/bot123456:ABC_def-123/getMe failed",
        args=(),
        exc_info=None,
    )

    assert RedactSecretsFilter().filter(record)
    assert record.getMessage() == (
        "POST https://api.telegram.org/bot<redacted>/getMe failed"
    )

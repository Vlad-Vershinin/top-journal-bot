from .bot import ScheduleBot
from .config import Settings
from .logging_setup import configure_logging


def main() -> None:
    settings = Settings.from_env()
    log_file = configure_logging(settings.log_dir, settings.log_level)
    import logging

    logging.getLogger(__name__).info("Bot is starting; log file: %s", log_file.resolve())
    ScheduleBot(settings).build().run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()

# 🎓 TOP Journal Bot

> Расписание TOP Academy в Telegram — без ежедневных походов на сайт.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

Небольшой, но живучий Telegram-бот, который авторизуется в Journal TOP Academy,
получает расписание через API и присылает его в удобном виде. Если Journal снова
решит немного полежать, бот предупредит об этом и покажет последнее сохранённое
расписание.

## ✨ Возможности

- `/today` — занятия на сегодня;
- `/tomorrow` — расписание на завтра;
- `/week` — вся текущая неделя;
- автоматическая ежедневная рассылка;
- доступ только для указанного Telegram-пользователя;
- автоматическое обновление токена Journal;
- дисковый кэш последнего успешного расписания;
- резервный ответ из кэша при сетевых ошибках, `403` и `5xx`;
- ежедневная ротация логов с хранением за 14 дней;
- запуск локально или в Docker.

## 🚀 Быстрый запуск

### Windows PowerShell

```powershell
git clone https://github.com/Vlad-Vershinin/top-journal-bot.git
cd top-journal-bot
Copy-Item .env.example .env
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Создайте Telegram-бота через [@BotFather](https://t.me/BotFather), затем заполните
`.env`:

```dotenv
TELEGRAM_BOT_TOKEN=токен_от_BotFather
JOURNAL_USERNAME=логин_от_Journal
JOURNAL_PASSWORD=пароль_от_Journal
```

Запустите проект из корневой папки:

```powershell
python -m journal_bot
```

> Не запускайте `python journal_bot/bot.py`: пакет использует относительные
> импорты и должен стартовать через `python -m journal_bot`.

Напишите боту `/id`, перенесите полученные значения в `.env` и перезапустите его:

```dotenv
ALLOWED_TELEGRAM_USER_ID=ваш_user_id
NOTIFICATION_CHAT_ID=ваш_chat_id
```

## ⚙️ Настройки

| Переменная | Обязательна | По умолчанию | Назначение |
|---|:---:|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Токен от BotFather |
| `JOURNAL_USERNAME` | ✅ | — | Логин Journal |
| `JOURNAL_PASSWORD` | ✅ | — | Пароль Journal |
| `ALLOWED_TELEGRAM_USER_ID` | — | доступ открыт | Разрешённый Telegram user ID |
| `NOTIFICATION_CHAT_ID` | — | рассылка выключена | Чат ежедневной рассылки |
| `NOTIFICATION_TIME` | — | `07:30` | Время рассылки |
| `TIMEZONE` | — | `Asia/Yekaterinburg` | Часовой пояс |
| `REQUEST_TIMEOUT_SECONDS` | — | `20` | Тайм-аут API |
| `LOG_DIR` | — | `logs` | Каталог логов |
| `LOG_LEVEL` | — | `INFO` | Уровень логирования |
| `CACHE_FILE` | — | `data/schedule_cache.json` | Файл кэша |

## 🛟 Когда Journal недоступен

После каждого успешного запроса бот сохраняет расписание по датам в
`data/schedule_cache.json`. При сетевой ошибке, `403` или ответе `5xx` пользователь
получает предупреждение, последнее известное расписание и время обновления кэша.
Если дата ещё не загружалась, бот не выдумывает данные и сообщает, что кэша нет.

## 🧾 Логи

Текущий лог записывается в `logs/bot.log`. В полночь создаётся новый файл, а логи
старше 14 дней удаляются автоматически. Для подробной диагностики установите
`LOG_LEVEL=DEBUG`.

## 🐳 Docker

```powershell
docker build -t top-journal-bot .
docker run -d --restart unless-stopped --env-file .env `
  --name top-journal-bot top-journal-bot
```

## 🧩 Архитектура

```text
Telegram → команды бота → JournalClient → API TOP Academy
                               │
                               ├── успешный ответ → дисковый кэш
                               └── сайт недоступен → последнее расписание
```

- `journal_bot/bot.py` — Telegram-команды и уведомления;
- `journal_bot/journal.py` — авторизация и запросы к Journal;
- `journal_bot/cache.py` — атомарный дисковый кэш;
- `journal_bot/logging_setup.py` — консольные и файловые логи;
- `journal_bot/formatting.py` — человекочитаемый ответ Telegram.

## 🔐 Безопасность

- `.env`, кэш, логи и виртуальное окружение исключены из Git;
- токены и пароли не записываются в исходный код;
- если Telegram-токен утёк, немедленно отзовите его через BotFather.

## ⚠️ Важно

API Journal публично не документирован и может измениться. Проект не является
официальным продуктом TOP Academy и предназначен для личного использования.

## 📄 Лицензия

MIT — пользуйтесь, улучшайте и присылайте pull request'ы. Звезда репозиторию тоже
не повредит: автору будет приятно, а расписанию — стабильнее. Наверное. ⭐

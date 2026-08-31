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
- автоматическое меню команд в интерфейсе Telegram;
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
| `TELEGRAM_PROXY_URL` | — | прямое соединение | HTTP/SOCKS5-прокси для Telegram Bot API |
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

## 🌐 Запуск через прокси

Если хостинг блокирует `api.telegram.org`, укажите обычный HTTP- или
SOCKS5-прокси в `.env`:

```dotenv
TELEGRAM_PROXY_URL=http://user:password@proxy.example.com:3128
```

или:

```dotenv
TELEGRAM_PROXY_URL=socks5://user:password@proxy.example.com:1080
```

Прокси применяется одновременно к long polling (`getUpdates`) и ко всем
остальным методам Bot API, включая отправку сообщений. Учётные данные прокси
не выводятся в лог. Если пароль содержит `@`, `:`, `/`, `#` или другие служебные
символы URL, их необходимо percent-encode.

После изменения `.env` Docker-контейнер нужно пересоздать, поскольку простой
`docker restart` не перечитывает `--env-file`:

```bash
docker rm -f top-journal-bot
docker build --no-cache -t top-journal-bot .
docker run -d --restart unless-stopped --env-file .env \
  --name top-journal-bot top-journal-bot
```

`tg-ws-proxy` использовать здесь нельзя: он реализует локальный MTProto-прокси
для Telegram Desktop, тогда как бот работает через HTTPS Telegram Bot API.

## 🛟 Когда Journal недоступен

После каждого успешного запроса бот сохраняет расписание по датам в
`data/schedule_cache.json`. При сетевой ошибке, `403` или ответе `5xx` пользователь
получает предупреждение, последнее известное расписание и время обновления кэша.
Если дата ещё не загружалась, бот не выдумывает данные и сообщает, что кэша нет.
Кэш используется не только при сетевом сбое, но и при ошибках авторизации или
других ошибках чтения расписания.

## 🧾 Логи

Текущий лог записывается в `logs/bot.log`. В полночь создаётся новый файл, а логи
старше 14 дней удаляются автоматически. Для подробной диагностики установите
`LOG_LEVEL=DEBUG`. Обычные успешные HTTP-запросы Telegram не записываются; для
каждой команды сохраняются только Telegram user ID, chat ID и имя команды. Даже
если сторонняя библиотека включит URL Bot API в ошибку, токен автоматически
заменяется на `bot<redacted>`.

## 🐳 Docker

```powershell
docker build -t top-journal-bot .
docker run -d --restart unless-stopped --env-file .env `
  --name top-journal-bot top-journal-bot
```

Для постоянного хранения кэша и логов удобнее использовать Compose:

```bash
docker compose up -d --build
```

## 🚚 Автоматический деплой

Workflow `.github/workflows/deploy.yml` при каждом push в `main`:

1. компилирует проект и запускает тесты;
2. подключается к серверу по SSH;
3. обновляет репозиторий только через fast-forward;
4. пересобирает и запускает контейнер через Docker Compose;
5. ждёт строку `Application started` и завершает деплой ошибкой, если бот не
   начал polling за 60 секунд.

Первичная подготовка сервера:

```bash
sudo mkdir -p /opt/top-journal-bot
sudo chown "$USER":"$USER" /opt/top-journal-bot
git clone https://github.com/Vlad-Vershinin/top-journal-bot.git /opt/top-journal-bot
cd /opt/top-journal-bot
cp .env.example .env
nano .env
docker compose up -d --build
```

Создайте отдельный ключ для GitHub Actions без парольной фразы:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/top-journal-deploy -C github-actions
cat ~/.ssh/top-journal-deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

В настройках GitHub-репозитория откройте **Settings → Environments**, создайте
окружение `production`, затем добавьте в него secrets:

| Secret | Значение |
|---|---|
| `DEPLOY_HOST` | IP или домен сервера |
| `DEPLOY_USER` | SSH-пользователь с доступом к Docker |
| `DEPLOY_PORT` | SSH-порт, обычно `22` |
| `DEPLOY_PATH` | `/opt/top-journal-bot` |
| `DEPLOY_SSH_PRIVATE_KEY` | содержимое `~/.ssh/top-journal-deploy` |
| `DEPLOY_KNOWN_HOSTS` | результат `ssh-keyscan -H SERVER_IP` |

Сам `.env` хранится только в `/opt/top-journal-bot` на сервере. Workflow его не
копирует и не перезаписывает. Каталоги `data` и `logs` смонтированы с хоста,
поэтому переживают пересборку контейнера. Ручной деплой можно запустить на вкладке
**Actions → Test and deploy → Run workflow**.

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

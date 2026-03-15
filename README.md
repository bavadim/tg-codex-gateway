# TG Agent Gateway

Telegram-бот, который запускает `opencode` на локальной папке проекта. Нужен, чтобы работать с проектом из Telegram: задавать вопросы по коду, запускать агент на своей рабочей директории и, при необходимости, управлять GitHub issues через `gh`.

## Установка

```bash
python -m pip install "git+https://github.com/bavadim/tg-codex-gateway.git"
```

## Что нужно настроить

Нужны:

- `opencode`
- `gh`
- Telegram bot token
- `.env` с переменными

Установка инструментов:

```bash
curl -fsSL https://opencode.ai/install | bash
sudo apt install gh
```

### Telegram bot token

1. Открой `@BotFather` в Telegram.
2. Выполни `/newbot`.
3. Задай имя и username бота.
4. Скопируй выданный token в `TELEGRAM_BOT_TOKEN`.

### Переменные окружения

Создай `.env`:

```bash
cp env.example .env
```

Заполни как минимум:

```bash
TELEGRAM_BOT_TOKEN=123456:your-bot-token
ALLOWED_CHAT_USER_IDS=@your_username
AGENT_BACKEND=opencode
OPENCODE_BIN=opencode
OPENCODE_MODEL=myopenai/gpt-5
OPENAI_API_BASE=https://openai.bavadim.xyz/v1
OPENAI_API_KEY=your-api-key
GH_TOKEN=your-github-token
```

Кратко:

- `TELEGRAM_BOT_TOKEN` — токен бота от `@BotFather`
- `ALLOWED_CHAT_USER_IDS` — кто может пользоваться ботом
- `OPENAI_API_BASE` и `OPENAI_API_KEY` — настройки для `opencode`
- `GH_TOKEN` — токен для `gh`, нужен если хочешь использовать управление GitHub issues

Gateway сам собирает runtime-конфиг для `opencode` из env. Дополнительный `opencode.json` в проекте не нужен.

## Запуск

```bash
set -a
source .env
set +a
tg-agent-gateway --workdir /path/to/your/project
```

## Что важно

- `--workdir` должен указывать на папку проекта
- если в проекте есть `AGENTS.md` и `.opencode/skills`, `opencode` их увидит
- вместе с gateway поставляется skill `gh-pm` для GitHub issue management через `gh`

# TG Agent Gateway

Telegram-бот, который запускает `opencode` на локальной папке проекта и пересылает ответы в Telegram.

## Что нужно

- Python 3.9+
- установленный `opencode`
- Telegram bot token

Проверка установки `opencode`:

```bash
opencode --version
```

## Установка

Сборка и установка пакета:

```bash
python -m build --wheel
pip install dist/tg_agent_gateway-*.whl
```

Для локальной разработки:

```bash
./scripts/dev-install.sh
```

## Настройка

Скопируй пример и заполни `.env`:

```bash
cp env.example .env
```

Переменные:

- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота
- `ALLOWED_CHAT_USER_IDS` — список разрешённых пользователей/чатов
- `AGENT_BACKEND=opencode`
- `OPENCODE_BIN=opencode`
- `OPENCODE_MODEL=myopenai/gpt-5`
- `OPENAI_API_BASE=https://openai.bavadim.xyz/v1`
- `OPENAI_API_KEY` — ключ для OpenAI-compatible API

Gateway сам собирает runtime-конфиг для `opencode` из этих env. Дополнительный `opencode.json` в папке проекта не нужен.

## Запуск

Загрузи переменные из `.env` и запусти бота на папке проекта:

```bash
set -a
source .env
set +a
tg-agent-gateway --workdir /path/to/your/project
```

## Что важно

- `--workdir` должен указывать на папку проекта, где лежит твой код
- если в проекте есть `AGENTS.md` и `.opencode/skills`, `opencode` их увидит
- доступ к боту ограничен через `ALLOWED_CHAT_USER_IDS`

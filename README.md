# Telegram -> Codex Gateway

A gateway bot that connects Telegram chats with Codex, forwarding recent messages to Codex and relaying the response back to Telegram. The bot runs the `codex` CLI locally and uses the repo you specify in `.env` for context.

## Setup

1) Install the package (editable for local dev):
```
pip install -e .
```

2) Install required Codex skills via the Codex skill installer:
```
codex skill install <skill-name-or-repo>
```

3) Create `.env` from example and fill values:
```
cp .env.example .env
```

## Environment variables

- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`: Telegram credentials.
- `REPO`: GitHub repo in `owner/name` format.
- `CODEX_MODEL`: Optional model override for `codex`.
- `ALLOWED_CHAT_USER_IDS`: Comma-separated Telegram user IDs allowed to authorize chats.

## Running the bot

Before запуском нужен файл `.env` с настройками (см. шаги выше). Экспортируй переменные в shell перед стартом. Пример для bash:
```
export TELEGRAM_API_ID="123456"
export TELEGRAM_API_HASH="0123456789abcdef0123456789abcdef"
export TELEGRAM_BOT_TOKEN="123456:bot-token"
export REPO="owner/name"
export CODEX_MODEL="gpt-5"
export ALLOWED_CHAT_USER_IDS="111111111,222222222"
```

Запуск:
```
telegram-codex-gateway --codex-dir /path/to/repo
```

Для локального запуска без установки:
```
python gateway.py --codex-dir /path/to/repo
```

## Codex workspace requirements

The directory passed to `--codex-dir` should be a ready Codex workspace:
- Contains the repo context Codex should read and edit (the target project).
- Includes an `AGENTS.md` with local contributor/agent instructions.
- Skills are installed via the Codex skill installer and available to the CLI (typically under `$CODEX_HOME/skills` or a project-local `.codex/skills`).

## How the gateway works

- The bot keeps a rolling log of the last 30 messages per chat.
- A chat is authorized only after a user from `ALLOWED_CHAT_USER_IDS` posts there.
- In group chats, the bot responds when it is @mentioned or replied to.
- In private chats, the allowed user can mention or reply to the bot to trigger a Codex run.
- The prompt sent to Codex is the chat log in chronological order.

## Notes

- Access is restricted via `ALLOWED_CHAT_USER_IDS`. A chat becomes authorized only after an allowed user posts there.
- The bot expects `codex` CLI on PATH.

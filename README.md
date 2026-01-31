# Telegram -> Codex Gateway

A Pyrogram bot that forwards the last 30 chat messages to Codex and returns the result to Telegram. The bot runs the `codex` CLI locally and uses the repo you specify in `.env` for context.

## Setup

1) Install dependencies:
```
pip install -r requirements.txt
```

2) Install the skills:
```
cp -r ../codex-pm-skill/github-issues ~/.codex/skills/
cp -r ../codex-pm-skill/pm ~/.codex/skills/
```

3) Create `.env` from example and fill values:
```
cp .env.example .env
```

4) Run:
```
python bot.py --codex-dir /path/to/repo
```

## Environment variables

- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`: Telegram credentials.
- `REPO`: GitHub repo in `owner/name` format.
- `CODEX_MODEL`: Optional model override for `codex`.
- `ALLOWED_CHAT_USER_IDS`: Comma-separated Telegram user IDs allowed to authorize chats.

## Notes

- Access is restricted via `ALLOWED_CHAT_USER_IDS`. A chat becomes authorized only after an allowed user posts there.
- The bot expects `codex` CLI on PATH.
- The PM prompt is stored in the `pm` skill inside `codex-pm-skill/pm/PROMPT.md`.

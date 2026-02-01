# Telegram -> Codex Gateway

A gateway bot that connects Telegram chats with Codex, forwarding recent messages to Codex and relaying the response back to Telegram. The bot runs the `codex` CLI locally and uses the repo you pass via `--codex-dir` for context.

## Requirements

- Python 3.9+
- `codex` CLI available on PATH

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
cp env.example .env
```

If you only need runtime dependencies without installing the console script:
```
pip install -r requirements.txt
```

## Configuration

Environment variables:
- `TELEGRAM_BOT_TOKEN`: Telegram bot token.
- `ALLOWED_CHAT_USER_IDS`: Comma-separated allowlist entries. Accepts numeric IDs, usernames (`@user` or `user`), and chat links (`https://t.me/your_group` or `t.me/your_group`).

## Running the bot

You must have a `.env` file with configuration before starting the bot (see Setup above). Export variables into your shell before running.

Example (bash):
```
set -a
source .env
set +a
```

Run:
```
telegram-codex-gateway --codex-dir /path/to/repo
```

Local run without installation:
```
python gateway.py --codex-dir /path/to/repo
```

## Testing

```
cp .env .env.test
pytest
```

## Codex workspace requirements

The directory passed to `--codex-dir` should be a ready Codex workspace:
- Contains the repo context Codex should read and edit (the target project).
- Includes an `AGENTS.md` with local contributor/agent instructions.
- Skills are installed via the Codex skill installer and available to the CLI (typically under `$CODEX_HOME/skills` or a project-local `.codex/skills`).

## How the gateway works

- The bot keeps a rolling log of the last 30 messages per chat.
- A chat is authorized either by being listed in `ALLOWED_CHAT_USER_IDS` or after an allowed user posts there.
- In group chats, the bot responds when it is @mentioned or replied to.
- In private chats, the allowed user can mention or reply to the bot to trigger a Codex run.
- The prompt sent to Codex is the chat log in chronological order.

## Notes

- Access is restricted via `ALLOWED_CHAT_USER_IDS`. User entries authorize chats after they post; chat links authorize the chat immediately.
- The bot expects `codex` CLI on PATH.

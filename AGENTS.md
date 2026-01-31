# Repository Guidelines

## Project Structure & Module Organization
- `gateway.py` is the main Pyrogram bot entry point for local runs.
- `telegram_codex_gateway/cli.py` is the package entry point used by the console script.
- `README.md` documents setup and usage.
- `requirements.txt` lists Python dependencies.
- `.env.example` provides required configuration keys; copy to `.env`.
- There is no separate `tests/` directory at present.

## Build, Test, and Development Commands
- `pip install -r requirements.txt` installs runtime dependencies.
- `python gateway.py --codex-dir /path/to/repo` runs the bot and forwards chat context to the local `codex` CLI working directory.
- `cp .env.example .env` prepares local configuration.

## Coding Style & Naming Conventions
- Python: follow PEP 8 conventions, 4-space indentation.
- Prefer explicit, descriptive names (`CODEX_WORKDIR`, `ALLOWED_USERS`).
- Keep functions short and single-purpose; add small helper functions rather than large handlers.

## Testing Guidelines
- No automated tests are currently configured.
- If you add tests, place them under a new `tests/` folder and prefer `pytest` with filenames like `test_*.py`.
- Include a minimal test command in this section if you add a test runner.

## Commit & Pull Request Guidelines
- Git history does not show a consistent commit message convention yet.
- Use concise, imperative commit messages (e.g., `Add codex-dir flag to bot`).
- Pull requests should include: a short summary, how to run/test changes, and any config updates.
- When a user asks for commits, split changes into logical commits with clear messages, then `git push` after confirming the branch is correct.

## Security & Configuration Tips
- Do not commit `.env` files or real credentials.
- `ALLOWED_CHAT_USER_IDS` controls who can authorize chats; keep it strict.
- Ensure the `codex` CLI is installed and available on `PATH` before running the bot.

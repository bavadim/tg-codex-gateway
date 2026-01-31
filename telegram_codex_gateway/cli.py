import argparse
import os
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError


ENV_FILENAME = ".env"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def parse_allowed_entries(raw: str) -> List[str]:
    entries: List[str] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            entries.append(part)
    return entries


def iter_chat_candidates(raw: str) -> Iterable[str]:
    yield raw
    trimmed = raw.strip()
    if trimmed.startswith("@"):
        yield trimmed[1:]
    cleaned = trimmed
    for prefix in ("https://", "http://"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    if cleaned.startswith("t.me/"):
        tail = cleaned[len("t.me/") :]
        if tail:
            yield tail


def extract_username(raw: str) -> Optional[str]:
    trimmed = raw.strip()
    if trimmed.startswith("@"):
        trimmed = trimmed[1:]
    for prefix in ("https://", "http://"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix) :]
    if trimmed.startswith("t.me/"):
        trimmed = trimmed[len("t.me/") :]
    if not trimmed or trimmed.startswith("+") or "/" in trimmed:
        return None
    return trimmed


def resolve_allowed_entries(
    app: Client, entries: List[str]
) -> Tuple[Set[int], Set[int]]:
    allowed_users: Set[int] = set()
    allowed_chats: Set[int] = set()
    unresolved: List[str] = []

    for entry in entries:
        if entry.lstrip("-").isdigit():
            value = int(entry)
            allowed_users.add(value)
            allowed_chats.add(value)
            continue

        chat = None
        for candidate in iter_chat_candidates(entry):
            try:
                chat = app.get_chat(candidate)
                break
            except Exception:
                continue

        if chat is None:
            username = extract_username(entry)
            if username:
                try:
                    chat = app.get_users(username)
                except Exception:
                    chat = None

        if chat is None:
            unresolved.append(entry)
            continue

        if getattr(chat, "type", None) == "private":
            allowed_users.add(chat.id)
        else:
            allowed_chats.add(chat.id)

    if unresolved:
        joined = ", ".join(unresolved)
        raise SystemExit(
            "Не удалось разрешить записи ALLOWED_CHAT_USER_IDS: "
            f"{joined}"
        )

    return allowed_users, allowed_chats


def message_to_line(msg) -> str:
    user = msg.from_user
    name = "unknown"
    if user:
        name = user.username or user.first_name or user.last_name or str(user.id)
    text = msg.text or msg.caption or "[non-text message]"
    if msg.reply_to_message and msg.reply_to_message.from_user:
        reply_user = msg.reply_to_message.from_user
        reply_name = (
            reply_user.username
            or reply_user.first_name
            or reply_user.last_name
            or str(reply_user.id)
        )
        return f"{name} (reply to {reply_name}): {text}"
    return f"{name}: {text}"


def append_log(chat_logs: Dict[int, List[str]], chat_id: int, line: str) -> None:
    items = chat_logs.setdefault(chat_id, [])
    items.append(line)
    if len(items) > 30:
        del items[:-30]


def build_prompt(chat_logs: Dict[int, List[str]], chat_id: int) -> str:
    log = chat_logs.get(chat_id, [])
    header = "Лог чата (последние 30 сообщений, в порядке отправки):\n"
    lines = "\n".join(f"- {line}" for line in log)
    return header + lines


def run_codex(prompt: str, workdir: Path) -> str:
    cmd = [
        "codex",
        "exec",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "-C",
        str(workdir),
        "-",
    ]
    env = os.environ.copy()

    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or "codex exec failed"
        raise RuntimeError(err)
    return (result.stdout or "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Telegram -> Codex gateway bot.",
    )
    parser.add_argument(
        "--codex-dir",
        default=str(Path.cwd()),
        help="Directory passed to codex via -C (default: current directory).",
    )
    return parser.parse_args()


def main() -> None:
    load_env(Path.cwd() / ENV_FILENAME)

    telegram_api_id = os.environ.get("TELEGRAM_API_ID")
    telegram_api_hash = os.environ.get("TELEGRAM_API_HASH")
    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")

    allowed_entries = parse_allowed_entries(
        os.environ.get("ALLOWED_CHAT_USER_IDS", "")
    )

    if not telegram_api_id or not telegram_api_hash or not telegram_bot_token:
        raise SystemExit("Missing TELEGRAM_API_ID/TELEGRAM_API_HASH/TELEGRAM_BOT_TOKEN")
    if not allowed_entries:
        raise SystemExit("Missing ALLOWED_CHAT_USER_IDS")

    chat_logs: Dict[int, List[str]] = {}
    authorized_chats: Set[int] = set()

    codex_workdir = Path(parse_args().codex_dir).expanduser().resolve()
    if not codex_workdir.exists():
        raise SystemExit(f"codex dir not found: {codex_workdir}")

    app = Client(
        "tg_pm_bot",
        api_id=int(telegram_api_id),
        api_hash=telegram_api_hash,
        bot_token=telegram_bot_token,
    )

    app.start()
    try:
        allowed_users, allowed_chats = resolve_allowed_entries(app, allowed_entries)
    finally:
        app.stop()

    if not allowed_users and not allowed_chats:
        raise SystemExit("Missing ALLOWED_CHAT_USER_IDS")
    authorized_chats.update(allowed_chats)

    def is_allowed_user(message) -> bool:
        if not message.from_user:
            return False
        return message.from_user.id in allowed_users

    def is_authorized_chat(chat_id: int) -> bool:
        return chat_id in authorized_chats

    @app.on_message(filters.all)
    def capture_messages(_, message):
        if is_allowed_user(message):
            authorized_chats.add(message.chat.id)
        if not is_authorized_chat(message.chat.id):
            return
        line = message_to_line(message)
        append_log(chat_logs, message.chat.id, line)

    @app.on_message(filters.command(["help"]))
    def commands(_, message):
        if not is_allowed_user(message):
            message.reply_text("Нет доступа")
            return
        text = message.text or ""
        if text.startswith("/help"):
            reply = (
                "Я PM-бот. Упоминание запускает анализ последних 30 сообщений.\n"
                "Команды: /help"
            )
            message.reply_text(reply)
            return

    @app.on_message(filters.group | filters.private)
    def mention_handler(_, message):
        if not is_allowed_user(message):
            return
        if not is_authorized_chat(message.chat.id):
            message.reply_text("Нет доступа")
            return
        if message.from_user and message.from_user.is_bot:
            return
        if not message.text:
            return

        bot = app.get_me()
        bot_username = bot.username or ""
        bot_id = bot.id

        mentioned = False
        for ent in message.entities or []:
            if ent.type == "mention":
                mention = message.text[ent.offset : ent.offset + ent.length]
                if mention.lstrip("@").lower() == bot_username.lower():
                    mentioned = True
                    break
            if ent.type == "text_mention" and ent.user and ent.user.id == bot_id:
                mentioned = True
                break

        if not mentioned and message.reply_to_message:
            reply_user = message.reply_to_message.from_user
            if reply_user and reply_user.is_bot and reply_user.id == bot_id:
                mentioned = True

        if not mentioned:
            return

        prompt = build_prompt(chat_logs, message.chat.id)
        answer = ""
        try:
        answer = run_codex(prompt, codex_workdir)
            if not answer:
                raise RuntimeError("Empty response from codex")
            message.reply_text(
                answer,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
        except RPCError:
            safe = answer or "Ошибка: некорректный MarkdownV2"
            message.reply_text(safe)
        except Exception as exc:
            message.reply_text(f"Ошибка: {exc}")

    app.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Set

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError


ROOT = Path(__file__).resolve().parent


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


load_env(ROOT / ".env")

TELEGRAM_API_ID = os.environ.get("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

REPO = os.environ.get("REPO")
CODEX_MODEL = os.environ.get("CODEX_MODEL")

ALLOWED_CHAT_USER_IDS = os.environ.get("ALLOWED_CHAT_USER_IDS", "")
ALLOWED_USERS: Set[int] = set()
for part in ALLOWED_CHAT_USER_IDS.split(","):
    part = part.strip()
    if part:
        try:
            ALLOWED_USERS.add(int(part))
        except ValueError:
            pass

if not TELEGRAM_API_ID or not TELEGRAM_API_HASH or not TELEGRAM_BOT_TOKEN:
    raise SystemExit("Missing TELEGRAM_API_ID/TELEGRAM_API_HASH/TELEGRAM_BOT_TOKEN")
if not REPO:
    raise SystemExit("Missing REPO env (owner/name)")
if not ALLOWED_USERS:
    raise SystemExit("Missing ALLOWED_CHAT_USER_IDS")


chat_logs: Dict[int, List[str]] = {}
authorized_chats: Set[int] = set()
CODEX_WORKDIR = ROOT


def message_to_line(msg) -> str:
    user = msg.from_user
    name = "unknown"
    if user:
        name = user.username or user.first_name or user.last_name or str(user.id)
    text = msg.text or msg.caption or "[non-text message]"
    if msg.reply_to_message and msg.reply_to_message.from_user:
        r = msg.reply_to_message.from_user
        rname = r.username or r.first_name or r.last_name or str(r.id)
        return f"{name} (reply to {rname}): {text}"
    return f"{name}: {text}"


def append_log(chat_id: int, line: str) -> None:
    items = chat_logs.setdefault(chat_id, [])
    items.append(line)
    if len(items) > 30:
        del items[:-30]


def build_prompt(chat_id: int) -> str:
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
    if CODEX_MODEL:
        cmd += ["--model", CODEX_MODEL]

    env = os.environ.copy()

    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or "codex exec failed"
        raise RuntimeError(err)
    return (result.stdout or "").strip()


app = Client(
    "tg_pm_bot",
    api_id=int(TELEGRAM_API_ID),
    api_hash=TELEGRAM_API_HASH,
    bot_token=TELEGRAM_BOT_TOKEN,
)


def is_allowed_user(message) -> bool:
    if not message.from_user:
        return False
    return message.from_user.id in ALLOWED_USERS


def is_authorized_chat(chat_id: int) -> bool:
    return chat_id in authorized_chats


@app.on_message(filters.all)
def capture_messages(_, message):
    if is_allowed_user(message):
        authorized_chats.add(message.chat.id)
    if not is_authorized_chat(message.chat.id):
        return
    line = message_to_line(message)
    append_log(message.chat.id, line)


@app.on_message(filters.command(["help", "status", "set_repo"]))
def commands(_, message):
    if not is_allowed_user(message):
        message.reply_text("Нет доступа")
        return
    text = message.text or ""
    if text.startswith("/help"):
        reply = (
            "Я PM-бот. Упоминание запускает анализ последних 30 сообщений.\n"
            "Команды: /status, /set_repo owner/name"
        )
        message.reply_text(reply)
        return

    if text.startswith("/status"):
        reply = f"REPO: {os.environ.get('REPO','')}\nMODEL: {os.environ.get('CODEX_MODEL','')}"
        message.reply_text(reply)
        return

    if text.startswith("/set_repo"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            message.reply_text("Укажи репозиторий: /set_repo owner/name")
            return
        os.environ["REPO"] = parts[1].strip()
        message.reply_text(f"REPO обновлен: {os.environ['REPO']}")


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
            m = message.text[ent.offset: ent.offset + ent.length]
            if m.lstrip("@").lower() == bot_username.lower():
                mentioned = True
                break
        if ent.type == "text_mention" and ent.user and ent.user.id == bot_id:
            mentioned = True
            break

    if not mentioned and message.reply_to_message:
        ru = message.reply_to_message.from_user
        if ru and ru.is_bot and ru.id == bot_id:
            mentioned = True

    if not mentioned:
        return

    prompt = build_prompt(message.chat.id)
    answer = ""
    try:
        answer = run_codex(prompt, CODEX_WORKDIR)
        if not answer:
            raise RuntimeError("Empty response from codex")
        message.reply_text(answer, parse_mode=ParseMode.MARKDOWN_V2, disable_web_page_preview=True)
    except RPCError:
        safe = answer or "Ошибка: некорректный MarkdownV2"
        message.reply_text(safe)
    except Exception as e:
        message.reply_text(f"Ошибка: {e}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Telegram -> Codex gateway bot.",
    )
    parser.add_argument(
        "--codex-dir",
        default=str(ROOT),
        help="Directory passed to codex via -C (default: script directory).",
    )
    return parser.parse_args()


def main():
    global CODEX_WORKDIR
    args = parse_args()
    CODEX_WORKDIR = Path(args.codex_dir).expanduser().resolve()
    if not CODEX_WORKDIR.exists():
        raise SystemExit(f"codex dir not found: {CODEX_WORKDIR}")
    app.run()


if __name__ == "__main__":
    main()

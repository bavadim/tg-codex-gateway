import argparse
import asyncio
import contextlib
import logging
import os
import subprocess
import sys
from pathlib import Path
import json
from typing import Dict, List, Optional, Set, Tuple

from telegram import Update
from telegram.constants import ChatAction, MessageEntityType, ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

SYSTEM_PROMPT = (
    "Системный промпт:\n"
    "Ты — Другалек, уставший, циничный продукт-менеджер. "
    "Даешь оценочные суждения задачам пользователя и формулируешь выводы. "
    "Если видишь явные проблемы — говоришь прямо, без смягчений и без подбора выражений."
)

MAX_PLAIN_MESSAGE_LENGTH = 3900
MAX_MARKDOWN_MESSAGE_LENGTH = 3500


def parse_allowed_entries(raw: str) -> List[str]:
    entries: List[str] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            entries.append(part)
    return entries


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
    entries: List[str],
) -> Tuple[Set[int], Set[int], Set[str], Set[str], List[str]]:
    allowed_users: Set[int] = set()
    allowed_chats: Set[int] = set()
    allowed_usernames: Set[str] = set()
    allowed_chat_usernames: Set[str] = set()
    unresolved: List[str] = []

    for entry in entries:
        if entry.lstrip("-").isdigit():
            value = int(entry)
            allowed_users.add(value)
            allowed_chats.add(value)
            continue
        username = extract_username(entry)
        if username:
            normalized = username.lower()
            allowed_usernames.add(normalized)
            allowed_chat_usernames.add(normalized)
            continue
        unresolved.append(entry)

    return (
        allowed_users,
        allowed_chats,
        allowed_usernames,
        allowed_chat_usernames,
        unresolved,
    )


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


def build_group_prompt(chat_logs: Dict[int, List[str]], chat_id: int) -> str:
    log = chat_logs.get(chat_id, [])
    header = "Лог чата (последние 30 сообщений, в порядке отправки):\n"
    lines = "\n".join(f"- {line}" for line in log)
    return header + lines


def extract_message_text(message) -> str:
    return message.text or message.caption or ""


def split_message(text: str, limit: int) -> List[str]:
    if not text:
        return [""]
    parts: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut == -1 or cut < int(limit * 0.4):
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]
    return parts


async def reply_in_chunks(
    message,
    text: str,
    parse_mode: Optional[str] = None,
    disable_web_page_preview: bool = True,
) -> None:
    if not text:
        return
    use_parse_mode = parse_mode
    limit = MAX_PLAIN_MESSAGE_LENGTH
    if parse_mode:
        if len(text) <= MAX_MARKDOWN_MESSAGE_LENGTH:
            limit = MAX_MARKDOWN_MESSAGE_LENGTH
        else:
            use_parse_mode = None
            limit = MAX_PLAIN_MESSAGE_LENGTH
    for part in split_message(text, limit):
        await message.reply_text(
            part,
            parse_mode=use_parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )


def extract_codex_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            parts.append(extract_codex_text(item))
        return "".join(parts)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "output_text"):
            if key in value:
                return extract_codex_text(value[key])
    return ""


def parse_codex_json_output(raw: str) -> Tuple[Optional[str], Optional[str]]:
    answer = None
    session_id = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if session_id is None:
            if isinstance(payload, dict):
                if "session_id" in payload:
                    session_id = payload.get("session_id")
                else:
                    session = payload.get("session")
                    if isinstance(session, dict):
                        session_id = session.get("id")
                    elif payload.get("type") == "session":
                        session_id = payload.get("id")
                if session_id is None:
                    thread_id = payload.get("thread_id")
                    if thread_id:
                        session_id = thread_id
                    elif payload.get("type") == "thread.started":
                        session_id = payload.get("thread_id") or payload.get("id")
        if not isinstance(payload, dict):
            continue

        candidate = None
        if payload.get("type") in ("message", "assistant_message", "final_message", "agent_message"):
            if payload.get("role") in (None, "assistant"):
                candidate = extract_codex_text(payload)
        elif "message" in payload:
            message = payload.get("message")
            if isinstance(message, dict):
                if message.get("role") in (None, "assistant"):
                    candidate = extract_codex_text(message)
            else:
                candidate = extract_codex_text(message)
        elif payload.get("type") == "item.completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") in (
                "agent_message",
                "assistant_message",
                "message",
                "final_message",
            ):
                candidate = extract_codex_text(item)
        elif "response" in payload:
            response = payload.get("response")
            if isinstance(response, dict):
                candidate = extract_codex_text(response.get("output_text"))

        if candidate:
            answer = candidate
    return answer, session_id


def run_codex_command(
    cmd: List[str],
    prompt: str,
    cwd: Optional[Path],
    env: Dict[str, str],
    session_id: Optional[str],
    allow_failure: bool = False,
) -> Tuple[str, Optional[str], Optional[str]]:
    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        env=env,
        check=False,
        cwd=cwd,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or "codex exec failed"
        if allow_failure:
            return "", session_id, err
        raise RuntimeError(err)
    raw = (result.stdout or "").strip()
    if not raw:
        return "", session_id, None
    answer, new_session_id = parse_codex_json_output(raw)
    if not answer:
        return "", new_session_id or session_id, None
    return answer.strip(), new_session_id or session_id, None


def run_codex(
    prompt: str,
    workdir: Path,
    session_id: Optional[str],
) -> Tuple[str, Optional[str]]:
    env = os.environ.copy()
    base_cmd = [
        "codex",
        "--dangerously-bypass-approvals-and-sandbox",
        "exec",
    ]
    if session_id:
        cmd = base_cmd + ["resume", session_id, "--json", "-"]
        full_prompt = prompt
        cwd = workdir
    else:
        cmd = base_cmd + ["--json", "-C", str(workdir)]
        cmd.append("-")
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        cwd = None

    answer, new_session_id, _ = run_codex_command(
        cmd,
        full_prompt,
        cwd,
        env,
        session_id,
    )
    return answer, new_session_id


async def send_typing_loop(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    stop_event: asyncio.Event,
    interval: float = 4.0,
) -> None:
    while not stop_event.is_set():
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            # Best-effort typing indicator; ignore failures.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


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


def setup_logging() -> logging.Logger:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    return logging.getLogger("telegram_codex_gateway")


def read_settings(codex_dir: str) -> Tuple[str, List[str], Path]:
    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    allowed_entries = parse_allowed_entries(
        os.environ.get("ALLOWED_CHAT_USER_IDS", "")
    )

    if not telegram_bot_token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN")
    if not allowed_entries:
        raise SystemExit("Missing ALLOWED_CHAT_USER_IDS")

    codex_workdir = Path(codex_dir).expanduser().resolve()
    if not codex_workdir.exists():
        raise SystemExit(f"codex dir not found: {codex_workdir}")

    return telegram_bot_token, allowed_entries, codex_workdir


def main() -> None:
    logger = setup_logging()
    args = parse_args()
    telegram_bot_token, allowed_entries, codex_workdir = read_settings(args.codex_dir)

    chat_logs: Dict[int, List[str]] = {}
    chat_sessions: Dict[int, str] = {}
    authorized_chats: Set[int] = set()

    app = ApplicationBuilder().token(telegram_bot_token).build()
    bot_id: Optional[int] = None
    bot_username = ""
    (
        allowed_users,
        allowed_chats,
        allowed_usernames,
        allowed_chat_usernames,
        unresolved_entries,
    ) = resolve_allowed_entries(allowed_entries)

    if (
        not allowed_users
        and not allowed_chats
        and not allowed_usernames
        and not allowed_chat_usernames
    ):
        raise SystemExit("Missing ALLOWED_CHAT_USER_IDS")
    authorized_chats.update(allowed_chats)
    if unresolved_entries:
        joined = ", ".join(unresolved_entries)
        print(
            "Не удалось разобрать записи ALLOWED_CHAT_USER_IDS: "
            f"{joined}. Они будут проигнорированы.",
            file=sys.stderr,
        )
        logger.warning(
            "Ignoring ALLOWED_CHAT_USER_IDS entries: %s", joined
        )

    logger.info(
        "Startup: allowed_users=%s allowed_chats=%s usernames=%s chat_usernames=%s",
        sorted(allowed_users),
        sorted(allowed_chats),
        sorted(allowed_usernames),
        sorted(allowed_chat_usernames),
    )

    def is_allowed_user(message) -> bool:
        if not message or not message.from_user:
            return False
        if message.from_user.id in allowed_users:
            return True
        username = message.from_user.username
        if username and username.lower() in allowed_usernames:
            return True
        return False

    def is_authorized_chat(chat) -> bool:
        if not chat:
            return False
        if chat.id in authorized_chats:
            return True
        username = chat.username
        if username and username.lower() in allowed_chat_usernames:
            return True
        return False

    async def ensure_bot_identity(context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal bot_id, bot_username
        if bot_id is not None:
            return
        info = await context.bot.get_me()
        bot_id = info.id
        bot_username = info.username or ""
        logger.info("Resolved bot identity: id=%s username=%s", bot_id, bot_username)

    def is_mentioned(message) -> bool:
        if not message or not message.text:
            return False
        for ent in message.entities or []:
            if ent.type == MessageEntityType.MENTION:
                mention = message.text[ent.offset : ent.offset + ent.length]
                if mention.lstrip("@").lower() == bot_username.lower():
                    return True
            if ent.type == MessageEntityType.TEXT_MENTION and ent.user:
                if bot_id is not None and ent.user.id == bot_id:
                    return True
        if message.reply_to_message:
            reply_user = message.reply_to_message.from_user
            if reply_user and reply_user.is_bot and reply_user.id == bot_id:
                return True
        return False

    async def capture_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        logger.debug("capture_messages handler; update_id=%s", update.update_id)
        logger.debug(
            "Incoming message chat_id=%s from_user_id=%s from_user_username=%s text=%s",
            chat.id,
            getattr(message.from_user, "id", None),
            getattr(message.from_user, "username", None),
            bool(message.text or message.caption),
        )
        if is_allowed_user(message):
            authorized_chats.add(chat.id)
            logger.debug("Authorized chat_id=%s via allowed user", chat.id)
        if not is_authorized_chat(chat):
            logger.debug(
                "Chat_id=%s not authorized; skipping log append", chat.id
            )
            return
        line = message_to_line(message)
        append_log(chat_logs, chat.id, line)

    async def commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message or not is_allowed_user(message):
            if message:
                await message.reply_text("Нет доступа")
            return
        text = message.text or ""
        if text.startswith("/help"):
            reply = (
                "Я PM-бот. Упоминание запускает анализ последних 30 сообщений.\n"
                "Команды: /help"
            )
            await message.reply_text(reply)

    async def mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        logger.debug("mention_handler handler; update_id=%s", update.update_id)
        if not is_allowed_user(message):
            logger.debug(
                "Message from non-allowed user; chat_id=%s from_user_id=%s from_user_username=%s",
                chat.id,
                getattr(message.from_user, "id", None),
                getattr(message.from_user, "username", None),
            )
            return
        if not is_authorized_chat(chat):
            logger.debug(
                "Chat not authorized; chat_id=%s from_user_id=%s from_user_username=%s",
                chat.id,
                getattr(message.from_user, "id", None),
                getattr(message.from_user, "username", None),
            )
            await message.reply_text("Нет доступа")
            return
        if message.from_user and message.from_user.is_bot:
            logger.debug("Ignoring bot message; chat_id=%s", chat.id)
            return
        await ensure_bot_identity(context)
        if chat.type == "private":
            logger.debug("Private chat; bypass mention check; chat_id=%s", chat.id)
        else:
            if not is_mentioned(message):
                logger.debug("Bot not mentioned; chat_id=%s", chat.id)
                return

        if chat.type == "private":
            prompt = extract_message_text(message)
        else:
            prompt = build_group_prompt(chat_logs, chat.id)
        answer = ""
        session_id = chat_sessions.get(chat.id)
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(
            send_typing_loop(context, chat.id, stop_typing)
        )
        try:
            answer, new_session_id = await asyncio.to_thread(
                run_codex,
                prompt,
                codex_workdir,
                session_id,
            )
            if new_session_id:
                chat_sessions[chat.id] = new_session_id
            elif session_id is None:
                logger.warning(
                    "No session id returned from codex; chat_id=%s", chat.id
                )
            if not answer:
                raise RuntimeError("Empty response from codex")
            await reply_in_chunks(
                message,
                answer,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
            logger.info("Replied to chat_id=%s", chat.id)
        except BadRequest:
            safe = answer or "Ошибка: некорректный MarkdownV2"
            await reply_in_chunks(message, safe)
            logger.warning("Bad markdown response; chat_id=%s", chat.id)
        except Exception as exc:
            await reply_in_chunks(message, f"Ошибка: {exc}")
            logger.exception("Handler error for chat_id=%s", chat.id)
        finally:
            stop_typing.set()
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    app.add_handler(MessageHandler(filters.ALL, capture_messages), group=0)
    app.add_handler(CommandHandler("help", commands), group=1)
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS | filters.ChatType.PRIVATE, mention_handler
        ),
        group=1,
    )

    app.run_polling()


if __name__ == "__main__":
    main()

import argparse
import asyncio
import contextlib
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
import json
import shutil
import tarfile
import uuid
import zipfile
import gzip
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from telegram import Update
from telegram.constants import ChatAction, MessageEntityType, ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

MAX_PLAIN_MESSAGE_LENGTH = 3900
MAX_MARKDOWN_MESSAGE_LENGTH = 3500
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXTRACT_FILES = 2000
SANDBOX_ROOT = Path("/tmp/tg-codex")
SANDBOX_LINK_DIRNAME = ".tg-sandboxes"


@dataclass
class SandboxInfo:
    sandbox_id: str
    path: Path
    link: Path


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


def sanitize_filename(name: str) -> str:
    if not name:
        return "upload"
    base = Path(name).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    return base or "upload"


def ensure_sandbox(
    chat_id: int,
    codex_workdir: Path,
    chat_sandboxes: Dict[int, SandboxInfo],
    sandbox_id: Optional[str] = None,
    force_new: bool = False,
) -> SandboxInfo:
    if not force_new and chat_id in chat_sandboxes:
        return chat_sandboxes[chat_id]

    if not sandbox_id:
        sandbox_id = uuid.uuid4().hex
    sandbox_id = sanitize_filename(sandbox_id)
    path = SANDBOX_ROOT / str(chat_id) / sandbox_id
    uploads = path / "uploads"
    work = path / "work"
    notes = path / "notes"
    uploads.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    notes.mkdir(parents=True, exist_ok=True)

    link_root = codex_workdir / SANDBOX_LINK_DIRNAME / str(chat_id)
    link_root.mkdir(parents=True, exist_ok=True)
    link = link_root / sandbox_id
    if link.exists() or link.is_symlink():
        if link.is_symlink():
            link.unlink()
        else:
            shutil.rmtree(link, ignore_errors=True)
    link.symlink_to(path, target_is_directory=True)

    info = SandboxInfo(sandbox_id=sandbox_id, path=path, link=link)
    chat_sandboxes[chat_id] = info
    return info


def is_within_directory(base: Path, target: Path) -> bool:
    try:
        base_resolved = base.resolve()
        target_resolved = target.resolve()
    except FileNotFoundError:
        return False
    return base_resolved == target_resolved or base_resolved in target_resolved.parents


def extract_zip(archive: Path, dest: Path, max_files: int) -> int:
    count = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if count >= max_files:
                break
            target = dest / info.filename
            if not is_within_directory(dest, target):
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def extract_tar(archive: Path, dest: Path, max_files: int) -> int:
    count = 0
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            if count >= max_files:
                break
            target = dest / member.name
            if not is_within_directory(dest, target):
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as src:
                if src is None:
                    continue
                with target.open("wb") as out:
                    shutil.copyfileobj(src, out)
            count += 1
    return count


def extract_gzip(archive: Path, dest: Path) -> Optional[Path]:
    if archive.suffix != ".gz":
        return None
    name = archive.name
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return None
    target_name = name[:-3] or "archive"
    target = dest / target_name
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive, "rb") as src, target.open("wb") as out:
        shutil.copyfileobj(src, out)
    return target


def handle_archive(file_path: Path, sandbox: SandboxInfo) -> int:
    work_dir = sandbox.path / "work"
    if zipfile.is_zipfile(file_path):
        return extract_zip(file_path, work_dir, MAX_EXTRACT_FILES)
    if tarfile.is_tarfile(file_path):
        return extract_tar(file_path, work_dir, MAX_EXTRACT_FILES)
    extracted = extract_gzip(file_path, work_dir)
    if extracted:
        return 1
    return 0


def build_sandbox_prompt(
    sandbox: SandboxInfo,
    request_text: str,
    uploaded_file: Optional[Path] = None,
) -> str:
    uploads = sandbox.path / "uploads"
    work = sandbox.path / "work"
    files: List[str] = []
    for root in (uploads, work):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                try:
                    rel = path.relative_to(sandbox.path)
                except ValueError:
                    rel = path.name
                files.append(str(rel))
                if len(files) >= 200:
                    break
        if len(files) >= 200:
            break
    if not files:
        return ""
    file_list = "\n".join(f"- {item}" for item in files)
    uploaded_line = (
        f"Загруженный файл: {uploaded_file}\n" if uploaded_file else ""
    )
    return (
        "$log-archive-triage\n"
        f"Запрос: {request_text}\n"
        f"Путь к песочнице: {sandbox.link}\n"
        f"{uploaded_line}"
        "Доступные файлы в песочнице:\n"
        f"{file_list}"
    )


def normalize_markdown(text: str) -> str:
    return text or ""


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
        if not isinstance(payload, dict):
            continue
        if session_id is None:
            session_id = payload.get("session_id")
            if session_id is None:
                session = payload.get("session")
                if isinstance(session, dict):
                    session_id = session.get("id")
                elif payload.get("type") == "session":
                    session_id = payload.get("id")
            if session_id is None:
                session_id = payload.get("thread_id")
                if payload.get("type") == "thread.started":
                    session_id = payload.get("thread_id") or payload.get("id")

        candidate = None
        payload_type = payload.get("type")
        if payload_type in ("message", "assistant_message", "final_message", "agent_message"):
            if payload.get("role") in (None, "assistant"):
                candidate = extract_codex_text(payload)
        elif payload_type == "item.completed":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") in (
                "agent_message",
                "assistant_message",
                "message",
                "final_message",
            ):
                candidate = extract_codex_text(item)
        elif "message" in payload:
            candidate = extract_codex_text(payload.get("message"))
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
        full_prompt = prompt
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


async def run_codex_and_reply(
    message,
    prompt: str,
    codex_workdir: Path,
    chat_id: int,
    chat_sessions: Dict[int, str],
    chat_sandboxes: Dict[int, SandboxInfo],
    logger: logging.Logger,
    context: ContextTypes.DEFAULT_TYPE,
    log_label: str,
) -> None:
    raw_answer = ""
    session_id = chat_sessions.get(chat_id)
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        send_typing_loop(context, chat_id, stop_typing)
    )
    start_time = asyncio.get_event_loop().time()
    try:
        raw_answer, new_session_id = await asyncio.to_thread(
            run_codex,
            prompt,
            codex_workdir,
            session_id,
        )
        if new_session_id:
            if session_id and session_id != new_session_id:
                ensure_sandbox(
                    chat_id,
                    codex_workdir,
                    chat_sandboxes,
                    sandbox_id=new_session_id,
                    force_new=True,
                )
            elif session_id is None and chat_id not in chat_sandboxes:
                ensure_sandbox(
                    chat_id,
                    codex_workdir,
                    chat_sandboxes,
                    sandbox_id=new_session_id,
                )
            chat_sessions[chat_id] = new_session_id
        elif session_id is None:
            logger.warning(
                "No session id returned from codex; chat_id=%s", chat_id
            )
        if not raw_answer:
            raise RuntimeError("Empty response from codex")
        answer = normalize_markdown(raw_answer)
        await reply_in_chunks(
            message,
            answer,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        elapsed = asyncio.get_event_loop().time() - start_time
        logger.info(
            "%s: chat_id=%s elapsed=%.2fs text=%s",
            log_label,
            chat_id,
            elapsed,
            raw_answer.replace("\n", "\\n"),
        )
    except BadRequest:
        safe = raw_answer or "Ошибка: некорректный Markdown"
        await reply_in_chunks(message, safe)
        logger.warning("Bad Markdown response; chat_id=%s", chat_id)
    except Exception as exc:
        await reply_in_chunks(message, f"Ошибка: {exc}")
        logger.exception("Handler error for chat_id=%s", chat_id)
    finally:
        stop_typing.set()
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task


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
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
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
    chat_sandboxes: Dict[int, SandboxInfo] = {}
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

    async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        document = message.document
        if not document:
            return
        request_text = extract_message_text(message).strip()
        if not request_text:
            request_text = "Проверь логи и определи основные ошибки и аномалии."
        if is_allowed_user(message):
            authorized_chats.add(chat.id)
        if not is_authorized_chat(chat):
            await message.reply_text("Нет доступа")
            return
        if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
            await message.reply_text("Файл слишком большой")
            return

        sandbox = ensure_sandbox(chat.id, codex_workdir, chat_sandboxes)
        uploads_dir = sandbox.path / "uploads"
        work_dir = sandbox.path / "work"
        filename = sanitize_filename(document.file_name or document.file_unique_id)
        destination = uploads_dir / filename
        try:
            tg_file = await document.get_file()
            await tg_file.download_to_drive(custom_path=str(destination))
        except Exception:
            logger.exception("Failed to download file; chat_id=%s", chat.id)
            await message.reply_text("Не удалось скачать файл")
            return

        extracted = 0
        try:
            extracted = handle_archive(destination, sandbox)
            if extracted == 0:
                target = work_dir / filename
                if target != destination:
                    shutil.copy2(destination, target)
        except Exception:
            logger.exception("Failed to process file; chat_id=%s", chat.id)
            await message.reply_text("Не удалось обработать файл")
            return

        extra = f", распаковано файлов: {extracted}" if extracted else ""
        logger.info(
            "Document request: chat_id=%s text=%s",
            chat.id,
            request_text.replace("\n", "\\n"),
        )
        sandbox_prompt = build_sandbox_prompt(
            sandbox,
            request_text,
            uploaded_file=sandbox.link / filename,
        )
        if not sandbox_prompt:
            await message.reply_text(
                f"Файл сохранен: {sandbox.link}/{filename}{extra}"
            )
            return
        prompt = sandbox_prompt
        await run_codex_and_reply(
            message=message,
            prompt=prompt,
            codex_workdir=codex_workdir,
            chat_id=chat.id,
            chat_sessions=chat_sessions,
            chat_sandboxes=chat_sandboxes,
            logger=logger,
            context=context,
            log_label="Document response sent",
        )

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
        allowed_user = is_allowed_user(message)
        logger.info(
            "Request: chat_id=%s chat_type=%s from_user_id=%s from_user_username=%s allowed_user=%s authorized_chat=%s",
            chat.id,
            chat.type,
            getattr(message.from_user, "id", None),
            getattr(message.from_user, "username", None),
            allowed_user,
            is_authorized_chat(chat),
        )
        if allowed_user:
            authorized_chats.add(chat.id)
        if not allowed_user and not is_authorized_chat(chat):
            logger.debug(
                "Chat not authorized; chat_id=%s from_user_id=%s from_user_username=%s",
                chat.id,
                getattr(message.from_user, "id", None),
                getattr(message.from_user, "username", None),
            )
            await message.reply_text("Нет доступа")
            logger.info(
                "Request denied: chat_id=%s from_user_id=%s",
                chat.id,
                getattr(message.from_user, "id", None),
            )
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

        request_text = extract_message_text(message)
        if chat.type == "private":
            prompt = request_text
        else:
            prompt = build_group_prompt(chat_logs, chat.id)
        sandbox = chat_sandboxes.get(chat.id)
        if sandbox:
            sandbox_prompt = build_sandbox_prompt(sandbox, request_text)
            if sandbox_prompt:
                prompt = f"{prompt}\n\n{sandbox_prompt}"
        logger.info(
            "Request text: chat_id=%s prompt_type=%s text=%s",
            chat.id,
            "private" if chat.type == "private" else "group_log",
            request_text.replace("\n", "\\n"),
        )
        await run_codex_and_reply(
            message=message,
            prompt=prompt,
            codex_workdir=codex_workdir,
            chat_id=chat.id,
            chat_sessions=chat_sessions,
            chat_sandboxes=chat_sandboxes,
            logger=logger,
            context=context,
            log_label="Response sent",
        )
        logger.info("Replied to chat_id=%s", chat.id)

    app.add_handler(MessageHandler(filters.ALL, capture_messages), group=0)
    app.add_handler(CommandHandler("help", commands), group=1)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document), group=1)
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS | filters.ChatType.PRIVATE, mention_handler
        ),
        group=1,
    )

    app.run_polling()


if __name__ == "__main__":
    main()

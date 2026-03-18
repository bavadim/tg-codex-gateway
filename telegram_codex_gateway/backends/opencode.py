import json
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from telegram_codex_gateway.opencode_runtime import (
    build_opencode_runtime,
    cleanup_opencode_runtime,
)

from .base import AgentRunResult


def _extract_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_extract_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "output_text"):
            if key in value:
                return _extract_text(value[key])
    return ""


def parse_opencode_json_output(raw: str) -> Tuple[Optional[str], Optional[str]]:
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
            session_id = payload.get("session_id") or payload.get("sessionID")
            if session_id is None:
                session_id = payload.get("thread_id")
            if session_id is None and payload.get("type") in (
                "session",
                "session.created",
            ):
                session_id = payload.get("id")
            if session_id is None:
                session = payload.get("session")
                if isinstance(session, dict):
                    session_id = session.get("id") or session.get("sessionID")

        candidate = None
        payload_type = payload.get("type")
        part = payload.get("part")
        if isinstance(part, dict) and payload_type == "text":
            candidate = _extract_text(part.get("text"))
        if payload_type in ("message", "assistant_message", "message.completed", "run.completed"):
            candidate = _extract_text(payload)
        elif payload.get("role") == "assistant":
            candidate = _extract_text(payload)
        elif isinstance(part, dict):
            candidate = _extract_text(part)
        elif "message" in payload:
            candidate = _extract_text(payload.get("message"))
        elif "output" in payload:
            candidate = _extract_text(payload.get("output"))
        elif "response" in payload:
            candidate = _extract_text(payload.get("response"))

        if candidate:
            answer = candidate
    return answer, session_id


class OpenCodeBackend:
    def __init__(
        self,
        binary: str = "opencode",
        server_url: Optional[str] = None,
    ) -> None:
        self.binary = binary
        self.server_url = server_url

    def run(
        self,
        prompt: str,
        workdir: Path,
        session_id: Optional[str],
        chat_id: int,
    ) -> AgentRunResult:
        del chat_id
        runtime = build_opencode_runtime(base_env=os.environ.copy())
        try:
            env = runtime.env
            cmd = [
                self.binary,
                "run",
                "--agent",
                "build",
                "--format",
                "json",
            ]
            if self.server_url:
                cmd.extend(["--attach", self.server_url])
            if session_id:
                cmd.extend(["--session", session_id])

            result = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
                cwd=workdir,
                env=env,
            )
            if result.returncode != 0:
                err = result.stderr.strip() or "opencode run failed"
                raise RuntimeError(err)

            raw = (result.stdout or "").strip()
            if not raw:
                return AgentRunResult(answer="", session_id=session_id)
            answer, new_session_id = parse_opencode_json_output(raw)
            return AgentRunResult(
                answer=(answer or "").strip(),
                session_id=new_session_id or session_id,
            )
        finally:
            cleanup_opencode_runtime(runtime)

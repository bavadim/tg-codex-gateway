import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .base import AgentRunResult


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
        if payload_type in (
            "message",
            "assistant_message",
            "final_message",
            "agent_message",
        ):
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


class CodexBackend:
    def __init__(self, binary: str = "codex") -> None:
        self.binary = binary

    def _run_command(
        self,
        cmd: List[str],
        prompt: str,
        cwd: Optional[Path],
        env: Dict[str, str],
        session_id: Optional[str],
    ) -> Tuple[str, Optional[str]]:
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
            raise RuntimeError(err)
        raw = (result.stdout or "").strip()
        if not raw:
            return "", session_id
        answer, new_session_id = parse_codex_json_output(raw)
        if not answer:
            return "", new_session_id or session_id
        return answer.strip(), new_session_id or session_id

    def run(
        self,
        prompt: str,
        workdir: Path,
        session_id: Optional[str],
        chat_id: int,
    ) -> AgentRunResult:
        del chat_id
        env = os.environ.copy()
        base_cmd = [
            self.binary,
            "--dangerously-bypass-approvals-and-sandbox",
            "exec",
        ]
        if session_id:
            cmd = base_cmd + ["resume", session_id, "--json", "-"]
            cwd = workdir
        else:
            cmd = base_cmd + ["--json", "-C", str(workdir), "-"]
            cwd = None

        answer, new_session_id = self._run_command(
            cmd,
            prompt,
            cwd,
            env,
            session_id,
        )
        return AgentRunResult(answer=answer, session_id=new_session_id)

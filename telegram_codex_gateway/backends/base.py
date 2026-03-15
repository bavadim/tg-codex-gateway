from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Optional


@dataclass
class AgentRunResult:
    answer: str
    session_id: Optional[str]


class AgentBackend(Protocol):
    def run(
        self,
        prompt: str,
        workdir: Path,
        session_id: Optional[str],
        chat_id: int,
    ) -> AgentRunResult:
        ...

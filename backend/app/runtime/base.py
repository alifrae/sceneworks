"""Provider-neutral execution runtime contract (WP14/WP15)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence


class RuntimeErrorBase(RuntimeError):
    """Expected runtime-domain failure safe to surface through MCP."""


class CommandRuntimeError(RuntimeErrorBase):
    """Command failure carrying bounded objective evidence for the ledger."""

    def __init__(self, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.evidence = evidence or {}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ProcessSnapshot:
    process_id: str
    pid: int | None
    command: list[str]
    cwd: str
    started_at: str
    finished_at: str | None
    running: bool
    returncode: int | None
    next_cursor: int
    output: list[dict]


class ExecutionRuntime(Protocol):
    """Machine capabilities independent of any LLM or agent protocol."""

    key: str
    label: str

    async def read_text(
        self,
        root: Path,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        max_chars: int = 120_000,
    ) -> dict: ...

    async def list_files(
        self,
        root: Path,
        path: str = "",
        *,
        recursive: bool = False,
        max_entries: int = 500,
    ) -> dict: ...

    async def search_text(
        self,
        root: Path,
        query: str,
        *,
        path: str = "",
        max_results: int = 100,
    ) -> dict: ...

    async def write_text(
        self,
        root: Path,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
        create_only: bool = False,
    ) -> dict: ...

    async def run_command(
        self,
        root: Path,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str = "",
        timeout: int = 300,
    ) -> CommandResult: ...

    async def start_process(
        self,
        root: Path,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str = "",
    ) -> ProcessSnapshot: ...

    async def process_output(
        self, process_id: str, *, cursor: int = 0, max_events: int = 200
    ) -> ProcessSnapshot: ...

    async def stop_process(self, process_id: str) -> ProcessSnapshot: ...

    async def git_status(self, root: Path) -> dict: ...

    async def git_diff(self, root: Path, *, base_commit: str | None = None) -> dict: ...

    async def git_commit(self, root: Path, message: str) -> dict: ...

    async def shutdown(self) -> None: ...

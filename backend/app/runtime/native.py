"""Native SceneWorks execution runtime (WP14).

This runtime intentionally contains no model or agent logic. It exposes a
worktree-confined filesystem surface plus command/process/Git primitives. Shell
and child processes still run with the SceneWorks OS user's authority; cwd/path
confinement is not an OS sandbox.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from app.git.workspace import GitError, run_git
from app.runtime.base import CommandResult, ProcessSnapshot, RuntimeErrorBase

_MAX_FILE_BYTES = 4_000_000
_MAX_COMMAND_CHARS = 240_000
_MAX_PROCESS_EVENTS = 5000


@dataclass
class _ProcessRecord:
    process_id: str
    command: list[str]
    cwd: Path
    process: asyncio.subprocess.Process
    output: list[dict] = field(default_factory=list)
    readers: list[asyncio.Task] = field(default_factory=list)


class NativeRuntime:
    key = "native"
    label = "SceneWorks Native Runtime"

    def __init__(self) -> None:
        self._processes: dict[str, _ProcessRecord] = {}
        self._lock = asyncio.Lock()

    # --------------------------------------------------------------- paths/files

    def _root(self, root: Path) -> Path:
        resolved = root.resolve()
        if not resolved.is_dir():
            raise RuntimeErrorBase(f"workspace does not exist: {resolved}")
        return resolved

    def _path(self, root: Path, relative: str, *, allow_root: bool = False) -> Path:
        workspace = self._root(root)
        candidate = Path(relative or ".")
        if candidate.is_absolute():
            raise RuntimeErrorBase("absolute paths are not allowed")
        resolved = (workspace / candidate).resolve()
        if not resolved.is_relative_to(workspace):
            raise RuntimeErrorBase("path escapes the engineering-session worktree")
        if not allow_root and resolved == workspace:
            raise RuntimeErrorBase("a file path is required")
        return resolved

    async def read_text(
        self,
        root: Path,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        max_chars: int = 120_000,
    ) -> dict:
        target = self._path(root, path)
        if not target.is_file():
            raise RuntimeErrorBase(f"file not found: {path}")
        if target.stat().st_size > _MAX_FILE_BYTES:
            raise RuntimeErrorBase(
                f"file exceeds native runtime read limit ({_MAX_FILE_BYTES} bytes)"
            )
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeErrorBase("file is not UTF-8 text") from exc
        lines = text.splitlines(keepends=True)
        first = max(1, int(start_line or 1))
        last = int(end_line or len(lines))
        if last < first:
            raise RuntimeErrorBase("end_line must be >= start_line")
        selected = "".join(lines[first - 1 : last])
        truncated = len(selected) > max_chars
        if truncated:
            selected = selected[:max_chars]
        return {
            "path": target.relative_to(self._root(root)).as_posix(),
            "start_line": first,
            "end_line": min(last, len(lines)),
            "total_lines": len(lines),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "content": selected,
            "truncated": truncated,
        }

    async def list_files(
        self,
        root: Path,
        path: str = "",
        *,
        recursive: bool = False,
        max_entries: int = 500,
    ) -> dict:
        base = self._path(root, path, allow_root=True)
        if not base.exists():
            raise RuntimeErrorBase(f"path not found: {path}")
        if base.is_file():
            items = [base]
        elif recursive:
            items = (item for item in base.rglob("*") if ".git" not in item.parts)
        else:
            items = iter(base.iterdir())
        result: list[dict] = []
        workspace = self._root(root)
        for item in items:
            if len(result) >= max_entries:
                break
            try:
                resolved = item.resolve()
                if not resolved.is_relative_to(workspace):
                    continue
                result.append(
                    {
                        "path": resolved.relative_to(workspace).as_posix(),
                        "type": "directory" if resolved.is_dir() else "file",
                        "size": resolved.stat().st_size if resolved.is_file() else None,
                    }
                )
            except OSError:
                continue
        result.sort(key=lambda value: value["path"])
        return {"entries": result, "truncated": len(result) >= max_entries}

    async def search_text(
        self,
        root: Path,
        query: str,
        *,
        path: str = "",
        max_results: int = 100,
    ) -> dict:
        needle = query.strip()
        if not needle:
            raise RuntimeErrorBase("query is required")
        base = self._path(root, path, allow_root=True)
        if not base.exists():
            raise RuntimeErrorBase(f"path not found: {path}")
        workspace = self._root(root)
        candidates = [base] if base.is_file() else base.rglob("*")
        matches: list[dict] = []
        for candidate in candidates:
            if len(matches) >= max_results:
                break
            if not candidate.is_file() or ".git" in candidate.parts:
                continue
            try:
                if candidate.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if needle.lower() in line.lower():
                    matches.append(
                        {
                            "path": candidate.resolve().relative_to(workspace).as_posix(),
                            "line": number,
                            "text": line[:1000],
                        }
                    )
                    if len(matches) >= max_results:
                        break
        return {"query": needle, "matches": matches, "truncated": len(matches) >= max_results}

    async def write_text(
        self,
        root: Path,
        path: str,
        content: str,
        *,
        expected_sha256: str | None = None,
        create_only: bool = False,
    ) -> dict:
        target = self._path(root, path)
        existed = target.exists()
        if target.exists() and not target.is_file():
            raise RuntimeErrorBase("target exists and is not a file")
        if create_only and existed:
            raise RuntimeErrorBase("target already exists")
        if expected_sha256 is not None:
            if not existed:
                raise RuntimeErrorBase("expected_sha256 supplied for a missing file")
            current = target.read_bytes()
            actual = hashlib.sha256(current).hexdigest()
            if actual != expected_sha256:
                raise RuntimeErrorBase(
                    "file changed since it was read; expected_sha256 does not match"
                )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.sceneworks-{uuid4().hex}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return {
            "path": target.relative_to(self._root(root)).as_posix(),
            "created": not existed,
            "bytes": len(content.encode("utf-8")),
            "sha256": digest,
        }

    # ---------------------------------------------------------- command/process

    def _cwd(self, root: Path, cwd: str) -> Path:
        path = self._path(root, cwd, allow_root=True)
        if not path.is_dir():
            raise RuntimeErrorBase(f"cwd is not a directory: {cwd}")
        return path

    async def run_command(
        self,
        root: Path,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str = "",
        timeout: int = 300,
    ) -> CommandResult:
        if not command.strip():
            raise RuntimeErrorBase("command is required")
        workdir = self._cwd(root, cwd)
        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *[str(item) for item in args],
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeErrorBase(f"could not start command: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=max(1, timeout))
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeErrorBase(f"command timed out after {timeout}s") from exc
        return CommandResult(
            returncode=int(process.returncode or 0),
            stdout=stdout.decode(errors="replace")[:_MAX_COMMAND_CHARS],
            stderr=stderr.decode(errors="replace")[:_MAX_COMMAND_CHARS],
        )

    async def start_process(
        self,
        root: Path,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str = "",
    ) -> ProcessSnapshot:
        if not command.strip():
            raise RuntimeErrorBase("command is required")
        workdir = self._cwd(root, cwd)
        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *[str(item) for item in args],
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeErrorBase(f"could not start process: {exc}") from exc
        process_id = uuid4().hex
        record = _ProcessRecord(
            process_id=process_id,
            command=[command, *[str(item) for item in args]],
            cwd=workdir,
            process=process,
        )
        record.readers = [
            asyncio.create_task(self._drain(record, "stdout", process.stdout)),
            asyncio.create_task(self._drain(record, "stderr", process.stderr)),
        ]
        async with self._lock:
            self._processes[process_id] = record
        return self._snapshot(record, cursor=0, max_events=0)

    async def _drain(
        self,
        record: _ProcessRecord,
        stream: str,
        reader: asyncio.StreamReader | None,
    ) -> None:
        if reader is None:
            return
        while True:
            chunk = await reader.readline()
            if not chunk:
                return
            record.output.append(
                {
                    "seq": len(record.output),
                    "stream": stream,
                    "text": chunk.decode(errors="replace")[:8000],
                }
            )
            if len(record.output) > _MAX_PROCESS_EVENTS:
                del record.output[: len(record.output) - _MAX_PROCESS_EVENTS]
                for index, item in enumerate(record.output):
                    item["seq"] = index

    def _record(self, process_id: str) -> _ProcessRecord:
        try:
            return self._processes[process_id]
        except KeyError as exc:
            raise RuntimeErrorBase(f"process not found: {process_id}") from exc

    def _snapshot(
        self, record: _ProcessRecord, *, cursor: int, max_events: int
    ) -> ProcessSnapshot:
        start = max(0, cursor)
        events = record.output[start : start + max(0, max_events)] if max_events else []
        next_cursor = start + len(events)
        return ProcessSnapshot(
            process_id=record.process_id,
            command=list(record.command),
            cwd=str(record.cwd),
            running=record.process.returncode is None,
            returncode=record.process.returncode,
            next_cursor=next_cursor,
            output=events,
        )

    async def process_output(
        self, process_id: str, *, cursor: int = 0, max_events: int = 200
    ) -> ProcessSnapshot:
        record = self._record(process_id)
        if record.process.returncode is not None:
            await asyncio.gather(*record.readers, return_exceptions=True)
        return self._snapshot(record, cursor=cursor, max_events=max_events)

    async def stop_process(self, process_id: str) -> ProcessSnapshot:
        record = self._record(process_id)
        if record.process.returncode is None:
            record.process.terminate()
            try:
                await asyncio.wait_for(record.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                record.process.kill()
                await record.process.wait()
        await asyncio.gather(*record.readers, return_exceptions=True)
        return self._snapshot(record, cursor=0, max_events=len(record.output))

    # --------------------------------------------------------------------- git

    async def git_status(self, root: Path) -> dict:
        workspace = self._root(root)
        try:
            porcelain = await run_git(workspace, "status", "--porcelain")
            branch = (await run_git(workspace, "rev-parse", "--abbrev-ref", "HEAD")).strip()
            head = (await run_git(workspace, "rev-parse", "HEAD")).strip()
        except GitError as exc:
            raise RuntimeErrorBase(str(exc)) from exc
        return {"branch": branch, "head": head, "status": porcelain}

    async def git_diff(self, root: Path, *, base_commit: str | None = None) -> dict:
        workspace = self._root(root)
        try:
            if base_commit:
                committed = await run_git(workspace, "diff", f"{base_commit}..HEAD")
                stat = await run_git(workspace, "diff", "--stat", f"{base_commit}..HEAD")
            else:
                committed = ""
                stat = ""
            working = await run_git(workspace, "diff", "HEAD")
            staged = await run_git(workspace, "diff", "--cached")
            status = await run_git(workspace, "status", "--porcelain")
        except GitError as exc:
            raise RuntimeErrorBase(str(exc)) from exc
        return {
            "base_commit": base_commit,
            "stat": stat,
            "committed": committed,
            "working": working,
            "staged": staged,
            "status": status,
        }

    async def git_commit(self, root: Path, message: str) -> dict:
        if not message.strip():
            raise RuntimeErrorBase("commit message is required")
        workspace = self._root(root)
        try:
            await run_git(workspace, "add", "-A")
            await run_git(workspace, "commit", "-m", message.strip())
            head = (await run_git(workspace, "rev-parse", "HEAD")).strip()
        except GitError as exc:
            raise RuntimeErrorBase(str(exc)) from exc
        return {"commit": head, "message": message.strip()}

    async def shutdown(self) -> None:
        for process_id in list(self._processes):
            try:
                await self.stop_process(process_id)
            except Exception:
                pass
        self._processes.clear()

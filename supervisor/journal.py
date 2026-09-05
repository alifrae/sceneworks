from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|API_KEY|SECRET|PASSWORD)[A-Z0-9_]*)\s*=\s*([^\s,;]+)"
)


class OperationJournal:
    """Small durable audit journal owned by the out-of-process supervisor."""

    def __init__(self, path: Path, *, max_detail_chars: int = 512) -> None:
        self.path = Path(path)
        self.max_detail_chars = max(32, int(max_detail_chars))
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    component TEXT,
                    state TEXT NOT NULL,
                    accepted_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    detail TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_operations_accepted_at "
                "ON operations(accepted_at DESC)"
            )

    def accept(self, *, actor: str, action: str, component: str | None) -> str:
        operation_id = str(uuid.uuid4())
        accepted_at = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO operations(
                    operation_id, actor, action, component, state, accepted_at
                ) VALUES (?, ?, ?, ?, 'ACCEPTED', ?)
                """,
                (operation_id, actor, action, component, accepted_at),
            )
        return operation_id

    def mark_running(self, operation_id: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE operations SET state='RUNNING', started_at=? "
                "WHERE operation_id=? AND state='ACCEPTED'",
                (time.time(), operation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"operation {operation_id} is not accepted")

    def finish(
        self,
        operation_id: str,
        *,
        result: str,
        detail: str | None = None,
    ) -> None:
        if result not in {"SUCCEEDED", "FAILED", "PARTIAL", "REJECTED"}:
            raise ValueError(f"invalid operation result: {result}")
        sanitized = self._sanitize_detail(detail)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE operations SET state=?, finished_at=?, detail=? "
                "WHERE operation_id=?",
                (result, time.time(), sanitized, operation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"operation {operation_id} not found")

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT operation_id, actor, action, component, state, accepted_at, "
                "started_at, finished_at, detail FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def list(self, *, limit: int = 20) -> list[dict[str, Any]]:
        bounded = min(max(int(limit), 1), 100)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT operation_id, actor, action, component, state, accepted_at, "
                "started_at, finished_at, detail FROM operations "
                "ORDER BY accepted_at DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _sanitize_detail(self, detail: str | None) -> str | None:
        if detail is None:
            return None
        redacted = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", str(detail))
        return redacted[: self.max_detail_chars]


def default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "SceneWorks" / "supervisor"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "sceneworks" / "supervisor"
    return Path.home() / ".local" / "share" / "sceneworks" / "supervisor"


def ensure_token(data_dir: Path) -> str:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    token_path = data_dir / "token"
    if token_path.is_file():
        existing = token_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    temporary = data_dir / f".token-{os.getpid()}-{secrets.token_hex(4)}"
    temporary.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, token_path)
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    return token


__all__ = ["OperationJournal", "default_data_dir", "ensure_token"]

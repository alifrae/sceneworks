"""Immutable task attachment storage.

Attachments are SceneWorks-owned task context, never repository files.  They are
stored below ``Settings.attachment_root`` and referenced from the database by a
relative storage key.  Content is treated as untrusted user-provided context:
callers may inspect it, but instructions inside an attachment never gain system
or role authority.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config.settings import Settings, resolve_path


class AttachmentError(ValueError):
    pass


# Keep V1 intentionally narrow.  Browser-reported MIME types are not trusted;
# the canonical type is selected from the filename extension.
ALLOWED_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
}
TEXT_MIME_TYPES = {"text/plain", "text/markdown", "application/json", "text/csv"}


@dataclass(frozen=True)
class StoredAttachment:
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_key: str


def _root(settings: Settings) -> Path:
    root = resolve_path(settings.attachment_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_filename(filename: str) -> str:
    name = Path(filename or "").name.strip()
    if not name or name in {".", ".."}:
        raise AttachmentError("attachment filename is required")
    if len(name) > 240:
        raise AttachmentError("attachment filename is too long")
    return name


def canonical_mime_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    mime = ALLOWED_TYPES.get(suffix)
    if mime is None:
        allowed = ", ".join(sorted(ALLOWED_TYPES))
        raise AttachmentError(f"unsupported attachment type {suffix or '(none)'}; allowed: {allowed}")
    return mime


def store_bytes(
    settings: Settings,
    *,
    project_id: int,
    task_id: int,
    filename: str,
    data: bytes,
) -> StoredAttachment:
    name = safe_filename(filename)
    mime = canonical_mime_type(name)
    if not data:
        raise AttachmentError("attachment is empty")
    if len(data) > settings.attachment_max_bytes:
        raise AttachmentError(
            f"attachment exceeds {settings.attachment_max_bytes} byte per-file limit"
        )

    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(name).suffix.lower()
    relative = Path(str(project_id)) / str(task_id) / f"{uuid.uuid4().hex}{suffix}"
    root = _root(settings)
    target = (root / relative).resolve()
    if not target.is_relative_to(root):  # defensive: relative is generated above
        raise AttachmentError("invalid attachment storage path")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return StoredAttachment(
        filename=name,
        mime_type=mime,
        size_bytes=len(data),
        sha256=digest,
        storage_key=relative.as_posix(),
    )


def resolve_storage_key(settings: Settings, storage_key: str) -> Path:
    root = _root(settings)
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise AttachmentError("attachment storage key escapes attachment root")
    return path


def read_bytes(settings: Settings, storage_key: str) -> bytes:
    path = resolve_storage_key(settings, storage_key)
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise AttachmentError("attachment content is missing from local storage") from exc


def delete_file(settings: Settings, storage_key: str) -> None:
    path = resolve_storage_key(settings, storage_key)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    # Remove now-empty task/project directories, but never the configured root.
    root = _root(settings)
    parent = path.parent
    while parent != root and parent.is_relative_to(root):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def delete_task_tree(settings: Settings, project_id: int, task_id: int) -> None:
    root = _root(settings)
    path = (root / str(project_id) / str(task_id)).resolve()
    if path.is_relative_to(root) and path != root:
        shutil.rmtree(path, ignore_errors=True)


def delete_project_tree(settings: Settings, project_id: int) -> None:
    root = _root(settings)
    path = (root / str(project_id)).resolve()
    if path.is_relative_to(root) and path != root:
        shutil.rmtree(path, ignore_errors=True)

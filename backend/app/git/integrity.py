"""Authoritative Git snapshot helpers for control-plane/MCP reads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.git.workspace import GitError, GitWorktreeService, RepoInfo

_DIAGNOSTIC_CODES = {
    "repository_missing",
    "repository_unreadable",
    "not_git",
    "git_probe_failed",
    "git_status_probe_failed",
}


def _exception_text(exc: BaseException) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _redact_paths(text: str, *paths: Path | str | None) -> str:
    """Remove internal/registered host roots from diagnostics before MCP exposure."""
    redacted = text
    for path in paths:
        if path is None:
            continue
        raw = str(path)
        if not raw:
            continue
        variants = {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}
        for variant in sorted(variants, key=len, reverse=True):
            if variant:
                redacted = redacted.replace(variant, "<registered-repository>")
    return redacted


def _classify_git_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if isinstance(exc, GitError) and (
        "not a git repository" in text
        or "not a git work tree" in text
        or "outside repository" in text
    ):
        return "not_git"
    return "git_probe_failed"


def _encoded_error(code: str, detail: str) -> str:
    return f"{code}: {detail}"[:1000]


def _decode_error(error: str | None) -> dict[str, Any] | None:
    if not error:
        return None
    code, separator, detail = error.partition(":")
    code = code.strip()
    if not separator or code not in _DIAGNOSTIC_CODES:
        return {
            "code": "git_probe_failed",
            "exception_type": None,
            "detail": error[:1000],
        }
    detail = detail.strip()
    exception_type: str | None = None
    if code in {"repository_unreadable", "git_probe_failed", "git_status_probe_failed"}:
        candidate = detail.partition(":")[0].strip()
        if candidate and " " not in candidate:
            exception_type = candidate
    return {
        "code": code,
        "exception_type": exception_type,
        "detail": detail[:1000],
    }


class IntegrityGitWorktreeService(GitWorktreeService):
    """Git service that reports probe failure as data instead of fabricating state."""

    async def repo_info(self, repo_path: Path) -> RepoInfo:
        original_path = repo_path
        try:
            repo_path = repo_path.expanduser().resolve()
            exists = repo_path.exists()
            is_dir = repo_path.is_dir()
        except OSError as exc:
            detail = _redact_paths(_exception_text(exc), original_path)
            return RepoInfo(
                original_path,
                False,
                None,
                None,
                _encoded_error("repository_unreadable", detail),
            )

        if not exists:
            return RepoInfo(
                repo_path,
                False,
                None,
                None,
                _encoded_error("repository_missing", "path does not exist"),
            )
        if not is_dir:
            return RepoInfo(
                repo_path,
                False,
                None,
                None,
                _encoded_error("repository_unreadable", "path is not a directory"),
            )

        try:
            inside = await self._run(repo_path, "rev-parse", "--is-inside-work-tree")
            if inside.strip().lower() != "true":
                return RepoInfo(
                    repo_path,
                    False,
                    None,
                    None,
                    _encoded_error("not_git", "Git did not identify a work tree"),
                )
            head = await self._run(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
            commit = await self._run(repo_path, "rev-parse", "HEAD")
        except Exception as exc:  # noqa: BLE001 - Git availability is returned as data
            code = _classify_git_error(exc)
            detail = _redact_paths(_exception_text(exc), repo_path)
            return RepoInfo(
                repo_path,
                False,
                None,
                None,
                _encoded_error(code, detail),
            )

        branch = head.strip() if head.strip() != "HEAD" else None
        return RepoInfo(repo_path, True, branch, commit.strip() or None)

    async def repository_snapshot(
        self,
        repo_path: Path,
        configured_default_branch: str | None,
    ) -> dict[str, Any]:
        """Return MCP-safe repository truth rooted at the persisted repository path."""
        info = await self.repo_info(repo_path)
        snapshot: dict[str, Any] = {
            "is_git": info.is_git,
            "current_branch": info.head_branch,
            "default_branch": configured_default_branch or "",
            "head_commit": info.head_commit,
            "clean": None,
            "dirty": None,
            "availability": {"state": "available" if info.is_git else "unavailable"},
            "diagnostic": _decode_error(info.error),
            # Kept for compatibility with older MCP clients. New clients should
            # consume availability/diagnostic instead of inferring from strings.
            "error": info.error,
        }
        if not info.is_git:
            return snapshot

        try:
            status = await self._run(info.path, "status", "--porcelain")
        except Exception as exc:  # noqa: BLE001 - partial Git truth remains useful
            detail = _redact_paths(_exception_text(exc), info.path)
            diagnostic = {
                "code": "git_status_probe_failed",
                "exception_type": type(exc).__name__,
                "detail": detail[:1000],
            }
            snapshot["availability"] = {"state": "degraded"}
            snapshot["diagnostic"] = diagnostic
            snapshot["error"] = _encoded_error("git_status_probe_failed", detail)
            return snapshot

        dirty = bool(status.strip())
        snapshot["dirty"] = dirty
        snapshot["clean"] = not dirty
        snapshot["diagnostic"] = None
        snapshot["error"] = None
        return snapshot

    async def diff_between(
        self,
        repo_path: Path,
        base_commit: str,
        result_commit: str,
    ) -> dict[str, Any]:
        """Reconstruct a task diff from immutable commits in the registered repo."""
        repo_path = repo_path.expanduser().resolve()
        info = await self.repo_info(repo_path)
        if not info.is_git:
            raise GitError(info.error or "registered repository is unavailable")

        for commit, label in ((base_commit, "base"), (result_commit, "result")):
            try:
                await self._run(repo_path, "cat-file", "-e", f"{commit}^{{commit}}")
            except Exception as exc:  # noqa: BLE001
                detail = _redact_paths(_exception_text(exc), repo_path)
                raise GitError(
                    f"{label} commit {commit!r} is unavailable: {detail}"
                ) from exc

        stat = await self._run(repo_path, "diff", "--stat", f"{base_commit}..{result_commit}")
        full = await self._run(repo_path, "diff", f"{base_commit}..{result_commit}")
        log = await self._run(
            repo_path,
            "log",
            f"{base_commit}..{result_commit}",
            "--pretty=format:%h|%s|%an",
        )
        commits: list[dict[str, str]] = []
        for line in log.splitlines():
            if "|" not in line:
                continue
            sha, subject, author = line.split("|", 2)
            commits.append({"sha": sha, "subject": subject, "author": author})
        changed_files = await self.changed_files(repo_path, base_commit, result_commit)
        return {
            "stat": stat,
            "full": full,
            "commits": commits,
            "changed_files": changed_files,
        }

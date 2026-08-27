"""Deterministic project-policy checks: pure functions, no I/O, no model."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

POLICY_VIOLATION_MARKER = "[SCENEWORKS_POLICY_VIOLATION]"

PATTERN_SYNTAX_NOTE = (
    "Patterns are shell globs matched case-sensitively against repo-relative "
    "POSIX paths with fnmatch.fnmatchcase. `*` matches across `/`."
)


@dataclass(frozen=True)
class ProtectedPathViolation:
    path: str
    pattern: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "pattern": self.pattern}


def check_protected_paths(
    protected_patterns: list[str], changed_files: list[str],
) -> list[ProtectedPathViolation]:
    """Return every deterministic (changed file, protected pattern) match."""
    violations: list[ProtectedPathViolation] = []
    for raw_path in changed_files or []:
        path = str(raw_path).strip().replace("\\", "/")
        if not path:
            continue
        for raw_pattern in protected_patterns or []:
            pattern = str(raw_pattern).strip().replace("\\", "/")
            if pattern and fnmatch.fnmatchcase(path, pattern):
                violations.append(ProtectedPathViolation(path=path, pattern=pattern))
    return sorted(violations, key=lambda item: (item.path, item.pattern))


def render_violations(violations: list[ProtectedPathViolation]) -> str:
    if not violations:
        return ""
    lines = [
        POLICY_VIOLATION_MARKER,
        "SceneWorks computed the following project-policy violations directly "
        "from Git changed-file evidence. These are deterministic findings, not "
        "an agent interpretation. Unless the approved task contract explicitly "
        "authorizes the protected path, the review must not approve:",
        "",
    ]
    lines.extend(
        f"- `{item.path}` matches protected pattern `{item.pattern}`"
        for item in violations
    )
    return "\n".join(lines)

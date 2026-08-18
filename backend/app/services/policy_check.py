"""Deterministic policy checks: pure functions, no I/O, no model.

WP4's whole point is that the Engineer must not be the one who decides whether
its own work complies with project policy. For the parts of a policy that are
mechanically checkable — did a commit touch a path that was declared
protected — SceneWorks answers that itself from Git, the same way qualification
treats `git diff --name-only` as authoritative evidence rather than an agent's
own description of what it changed (see `evaluation/harness.py`,
`GitWorktreeService.changed_files`).

This keeps the LLM Reviewer out of the loop for the one class of violation that
does not need judgement, and gives it grounded evidence — "these paths were
protected and were touched" — for the parts that do.

Pattern syntax deliberately documented, not assumed: see `PATTERN_SYNTAX_NOTE`.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

PATTERN_SYNTAX_NOTE = (
    "Patterns are shell-glob (Python fnmatch), matched case-sensitively against "
    "repo-relative POSIX paths with fnmatch.fnmatchcase — never fnmatch.fnmatch, "
    "whose case-folding and path normalisation are platform-dependent (Windows "
    "vs Linux), which would make the same policy match differently in CI than "
    "in local development. `*` matches across `/`, so `generated/*` protects "
    "everything under generated/, not just its direct children; write "
    "`api/facade.py` for a single file."
)


@dataclass(frozen=True)
class ProtectedPathViolation:
    """One changed file that matched a protected-path pattern."""

    path: str
    pattern: str

    def as_dict(self) -> dict:
        return {"path": self.path, "pattern": self.pattern}


def check_protected_paths(
    protected_patterns: list[str], changed_files: list[str],
) -> list[ProtectedPathViolation]:
    """Which changed files match a protected-path pattern, and by which one.

    Returns one violation per (file, pattern) match, ordered by path then
    pattern, so the result is deterministic and every match is individually
    attributable — a Reviewer prompt should never have to guess which rule a
    flagged file actually broke.
    """
    if not protected_patterns or not changed_files:
        return []

    violations: list[ProtectedPathViolation] = []
    for path in changed_files:
        for pattern in protected_patterns:
            if not pattern.strip():
                continue
            if fnmatch.fnmatchcase(path, pattern):
                violations.append(ProtectedPathViolation(path=path, pattern=pattern))
    return sorted(violations, key=lambda v: (v.path, v.pattern))


def render_violations(violations: list[ProtectedPathViolation]) -> str:
    """Render violations as an unambiguous, grounded prompt block.

    Labelled as SceneWorks' own finding, not the model's, and phrased as a
    requirement to act on rather than a suggestion to weigh — this is exactly
    the evidence the Reviewer must not be allowed to silently overlook.
    """
    if not violations:
        return ""
    lines = [
        "The following files were modified and match a path this project has "
        "declared protected. This was computed directly from the Git diff, not "
        "inferred by any agent. Unless the approved architecture explicitly "
        "authorised touching these paths, this is a policy violation and the "
        "review verdict must be CHANGES_REQUESTED, naming each one:",
        "",
    ]
    for v in violations:
        lines.append(f"- `{v.path}` matches protected pattern `{v.pattern}`")
    return "\n".join(lines)

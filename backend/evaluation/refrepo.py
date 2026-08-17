"""Deterministic reference repositories for qualification.

These are real Git repositories built from scratch in a temporary directory, so
qualification exercises the actual worktree machinery rather than a mock.

Each repository ships its own **check command** — a stdlib-only Python script
that exits non-zero when the code is wrong. That is what makes "tests passing" a
measurement rather than an assumption: the harness runs it at the base commit
and again at the result commit, so a bug fix must move the repository from
failing to passing, and a regression is detected as passing to failing.

No pytest, no third-party imports: the check script must run under the same
interpreter with nothing installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Run with `sys.executable`, from the repository root.
CHECK_COMMAND = "python check.py"


@dataclass(frozen=True)
class ReferenceRepo:
    name: str
    files: dict[str, str]
    #: True when `check.py` is expected to fail at the base commit (the seeded
    #: defect). The harness asserts this, so a scenario cannot silently claim to
    #: have fixed something that was never broken.
    broken_at_base: bool
    description: str = ""


def _calc_core(buggy: bool) -> str:
    """The seeded defect: negative values are silently dropped."""
    if buggy:
        body = "    return sum(v for v in values if v > 0)\n"
    else:
        body = "    return sum(values)\n"
    return (
        '"""Aggregation helpers."""\n'
        "\n"
        "\n"
        "def total(values):\n"
        '    """Sum every value, including negative ones."""\n'
        f"{body}"
        "\n"
        "\n"
        "def average(values):\n"
        '    """Arithmetic mean; 0.0 for an empty sequence."""\n'
        "    values = list(values)\n"
        "    if not values:\n"
        "        return 0.0\n"
        "    return total(values) / len(values)\n"
    )


CALC_CHECK = '''"""Repository check suite. Stdlib only; exits non-zero on failure.

Run: python check.py
"""

import sys

from calc.core import average, total

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def main():
    check("total positives", total([1, 2, 3]), 6)
    check("total empty", total([]), 0)
    # The seeded defect lives here: negative values must not be dropped.
    check("total with negatives", total([5, -3]), 2)
    check("total all negative", total([-1, -2]), -3)
    check("average positives", average([2, 4]), 3.0)
    check("average empty", average([]), 0.0)
    check("average with negatives", average([5, -3]), 1.0)

    if FAILURES:
        for line in FAILURES:
            print(f"FAIL {line}")
        print(f"{len(FAILURES)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


CALC_README = """# calc

A tiny aggregation library used as a SceneWorks qualification fixture.

Run the check suite with `python check.py`.
"""

CALC_AGENTS = """# Agent instructions

- `calc/` holds the library. `check.py` is the repository's own check suite.
- Run `python check.py` before reporting work complete.
- Do not edit `check.py` to make a failure go away; fix `calc/` instead.
"""

CALC_USAGE = """# Usage

```python
from calc.core import total, average

total([1, 2, 3])   # 6
average([2, 4])    # 3.0
```
"""


CALC_BUGGY = ReferenceRepo(
    name="calc-buggy",
    description=(
        "Aggregation library whose total() silently drops negative values. "
        "check.py fails at the base commit."
    ),
    broken_at_base=True,
    files={
        "calc/__init__.py": '"""calc package."""\n',
        "calc/core.py": _calc_core(buggy=True),
        "check.py": CALC_CHECK,
        "README.md": CALC_README,
        "AGENTS.md": CALC_AGENTS,
        "docs/usage.md": CALC_USAGE,
    },
)

CALC_HEALTHY = ReferenceRepo(
    name="calc-healthy",
    description=(
        "Same library with total() correct. check.py passes at the base commit, "
        "so a regression introduced by the Engineer is detectable."
    ),
    broken_at_base=False,
    files={
        "calc/__init__.py": '"""calc package."""\n',
        "calc/core.py": _calc_core(buggy=False),
        "check.py": CALC_CHECK,
        "README.md": CALC_README,
        "AGENTS.md": CALC_AGENTS,
        "docs/usage.md": CALC_USAGE,
    },
)


REPOS: dict[str, ReferenceRepo] = {
    CALC_BUGGY.name: CALC_BUGGY,
    CALC_HEALTHY.name: CALC_HEALTHY,
}

#: The fixed content an Engineer script writes to repair CALC_BUGGY.
CALC_CORE_FIXED = _calc_core(buggy=False)
#: A change that breaks a healthy repository (used by the regression scenario).
CALC_CORE_REGRESSED = (
    '"""Aggregation helpers."""\n'
    "\n"
    "\n"
    "def total(values):\n"
    '    """Sum every value, including negative ones."""\n'
    "    # Regression: multiplies instead of summing.\n"
    "    result = 1\n"
    "    for value in values:\n"
    "        result *= value\n"
    "    return result\n"
    "\n"
    "\n"
    "def average(values):\n"
    '    """Arithmetic mean; 0.0 for an empty sequence."""\n'
    "    values = list(values)\n"
    "    if not values:\n"
    "        return 0.0\n"
    "    return total(values) / len(values)\n"
)


class RepoError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=120,
        env={"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
             "HOME": str(repo), "PATH": _path_env()},
    )
    if result.returncode != 0:
        raise RepoError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "")


def materialize(repo: ReferenceRepo, destination: Path) -> tuple[Path, str]:
    """Create the reference repository on disk. Returns (path, base_commit)."""
    if shutil.which("git") is None:
        raise RepoError("git is not available on PATH")

    root = destination / repo.name
    root.mkdir(parents=True, exist_ok=True)

    for rel, content in repo.files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "qualification@sceneworks.local")
    _git(root, "config", "user.name", "SceneWorks Qualification")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", f"{repo.name}: initial commit")
    base = _git(root, "rev-parse", "HEAD").strip()
    return root, base


def run_check(worktree: Path, timeout: int = 120) -> tuple[bool, str]:
    """Run the repository's own check suite. Returns (passed, output tail)."""
    script = worktree / "check.py"
    if not script.is_file():
        return False, "check.py not found in worktree"
    try:
        result = subprocess.run(
            [sys.executable, "check.py"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"check.py timed out after {timeout}s"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output[-2000:]

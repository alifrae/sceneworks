"""Version synchronization guard.

WP0 found the version reported in four places and disagreeing in three of them:
pyproject said 3.0.0 (set deliberately by commit 4cc41ac, "establish SceneWorks
V3 baseline"), while main.py's FastAPI version and the README title still said
2.5.2.

`app.__version__` is the single runtime source. These tests fail if any other
copy drifts from it, so the next release bump cannot leave stale numbers behind.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from app import __version__

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), (
        f"__version__ must be MAJOR.MINOR.PATCH, got {__version__!r}"
    )


def test_pyproject_version_matches_package() -> None:
    """pyproject is the packaging copy; it must agree with the runtime copy."""
    data = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__, (
        f"pyproject.toml says {data['project']['version']!r} but "
        f"app.__version__ is {__version__!r}. Update both."
    )


def test_api_reports_package_version() -> None:
    """The OpenAPI document must not carry a hard-coded version string."""
    from app.main import create_app

    app = create_app()
    assert app.version == __version__


def test_readme_title_matches_version() -> None:
    """The README H1 announces the release; it must not advertise an old one."""
    first_line = (REPO_ROOT / "README.md").read_text(encoding="utf-8").split("\n", 1)[0]
    match = re.search(r"V(\d+\.\d+(?:\.\d+)?)", first_line)
    assert match is not None, f"could not find a version in README title: {first_line!r}"
    claimed = match.group(1)
    # The title may be abbreviated to MAJOR.MINOR (e.g. "V3.0" for 3.0.0).
    assert __version__.startswith(claimed), (
        f"README title claims V{claimed} but app.__version__ is {__version__!r}"
    )

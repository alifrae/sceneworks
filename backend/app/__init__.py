"""SceneWorks backend package.

Canonical runtime version. `backend/pyproject.toml` carries the same value for
packaging; `tests/test_version.py` asserts the two agree, so they cannot drift
silently again.

Why not `importlib.metadata`: the backend runs as a plain `app` package from
`backend/`, not as an installed distribution, so
`importlib.metadata.version("sceneworks")` raises PackageNotFoundError. The
literal below plus the guard test is the deliberate synchronization mechanism.

Anything that reports a version to a human or an API client must read
`app.__version__` rather than hard-coding a string. Hard-coded copies are what
left `main.py` reporting 2.5.2 after the V3 baseline (commit 4cc41ac) had
already moved the project to 3.0.0.
"""

__version__ = "4.0.0"

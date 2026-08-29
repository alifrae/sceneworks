"""Startup regression tests for the operational API launcher."""

from __future__ import annotations

from types import SimpleNamespace

import uvicorn

import app.main as main_module


def test_module_launcher_disables_uvicorn_reload(monkeypatch):
    """Reload mode can remove asyncio subprocess support on Windows."""
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(host="127.0.0.1", port=8010),
    )

    def fake_run(app: str, **kwargs) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    main_module.main()

    assert captured == {
        "app": "app.main:app",
        "host": "127.0.0.1",
        "port": 8010,
        "reload": False,
    }

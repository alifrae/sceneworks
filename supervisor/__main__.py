from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from supervisor.core import LifecycleSupervisor
from supervisor.http_api import SupervisorApplication, create_server
from supervisor.journal import OperationJournal, default_data_dir, ensure_token
from supervisor.managed_process import (
    LaunchSpec,
    ManagedProcessProvider,
    ProcessMetadataStore,
    WindowsProcessHost,
)
from supervisor.model import ComponentKey
from supervisor.providers import HttpHealthProvider, HttpProbe


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SceneWorks local lifecycle supervisor")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--no-tunnel", action="store_true")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--tunnel-client-path", type=Path)
    parser.add_argument("--mcp-server-url", default="http://127.0.0.1:8010/mcp")
    return parser


def _launch_specs(args: argparse.Namespace) -> dict[ComponentKey, LaunchSpec]:
    root = args.repo_root.resolve()
    backend = root / "backend"
    web = root / "web"
    tunnel_path = (
        args.tunnel_client_path.resolve()
        if args.tunnel_client_path is not None
        else root / "tools" / "tunnel-client-runtime-cloudflared.exe"
    )
    web_script = "dev" if args.dev else "start"
    specs: dict[ComponentKey, LaunchSpec] = {
        ComponentKey.API: LaunchSpec(
            component=ComponentKey.API,
            argv=("uv", "run", "python", "-m", "app.main"),
            cwd=backend,
            fingerprint=("uv", "app.main"),
            adopt_port=8010,
        ),
        ComponentKey.WEB: LaunchSpec(
            component=ComponentKey.WEB,
            argv=("npm.cmd", "run", web_script),
            cwd=web,
            fingerprint=("npm", "run", web_script),
            adopt_port=3000,
        ),
    }
    if not args.no_tunnel:
        specs[ComponentKey.MCP_TUNNEL] = LaunchSpec(
            component=ComponentKey.MCP_TUNNEL,
            argv=(str(tunnel_path), "run"),
            cwd=root,
            fingerprint=(tunnel_path.name, "run"),
            env_overrides={"MCP_SERVER_URL": str(args.mcp_server_url)},
            adopt_port=8080,
        )
    return specs


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.port != 8020:
        raise SystemExit("WP21 supervisor port is fixed at 8020")

    data_dir = default_data_dir()
    token = ensure_token(data_dir)
    specs = _launch_specs(args)
    enabled_components = set(specs)
    provider = ManagedProcessProvider(
        specs=specs,
        store=ProcessMetadataStore(data_dir / "processes.json"),
        host=WindowsProcessHost(),
    )
    health = HttpHealthProvider(
        {
            ComponentKey.API: HttpProbe("http://127.0.0.1:8010/api/health"),
            ComponentKey.WEB: HttpProbe("http://127.0.0.1:3000"),
            ComponentKey.MCP_TUNNEL: HttpProbe("http://127.0.0.1:8080/readyz"),
        }
    )
    supervisor = LifecycleSupervisor(
        process_provider=provider,
        health_provider=health,
        enabled_components=enabled_components,
    )
    supervisor.reconcile()
    application = SupervisorApplication(
        supervisor=supervisor,
        journal=OperationJournal(data_dir / "supervisor.db"),
        token=token,
    )
    server = create_server(application, host="127.0.0.1", port=8020)

    def stop_server(_signum: int, _frame: object) -> None:
        server.shutdown()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_server)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, stop_server)

    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        application.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

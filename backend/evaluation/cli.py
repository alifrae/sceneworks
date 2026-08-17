"""Qualification CLI.

    cd backend
    uv run python -m evaluation                       # full suite
    uv run python -m evaluation --smoke               # fast CI subset
    uv run python -m evaluation --scenario bug-fix    # one scenario
    uv run python -m evaluation --json report.json    # machine-readable result
    uv run python -m evaluation --list                # list scenarios

Exit codes (documented contract — CI depends on these):

    0   PASS      every selected scenario passed, and every required
                  scenario was among them
    1   FAIL      at least one scenario produced a wrong engineering outcome
    2   BLOCKED   nothing failed, but qualification could not be completed:
                  a required scenario was skipped, a scenario declared no
                  checks, or the harness hit an environment problem
    3   NOT_RUN   no scenario was executed
    4   usage error (unknown scenario name, bad arguments)

Exit code 2 exists so a release pipeline cannot treat "we did not actually
check" as success. A partial run (--smoke, --scenario) is BLOCKED by design
unless it happens to cover every required scenario.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from app import __version__

from evaluation.outcomes import QualificationReport, Verdict
from evaluation.report import summary
from evaluation.scenarios import REQUIRED_KEYS, SCENARIOS, select

EXIT_CODES = {
    Verdict.PASS: 0,
    Verdict.FAIL: 1,
    Verdict.BLOCKED: 2,
    Verdict.NOT_RUN: 3,
}
EXIT_USAGE = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation",
        description="SceneWorks release qualification (go/no-go).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        metavar="KEY",
        help="run only this scenario (repeatable)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="run the fast CI subset instead of the full suite",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="write the machine-readable report to PATH ('-' for stdout)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="print passing checks and provenance, not just failures",
    )
    parser.add_argument(
        "--keep-workdir", action="store_true",
        help="keep the temporary repositories and databases for inspection",
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0, metavar="SECONDS",
        help="per-scenario timeout (default: 120)",
    )
    parser.add_argument(
        "--list", action="store_true", help="list scenarios and exit",
    )
    parser.add_argument(
        "--live", metavar="REPO_PATH",
        help=(
            "OPTIONAL live qualification against a real repository, using the "
            "configured agent backend instead of the scripted one. Read-only on "
            "the human working tree: work happens in commit-pinned worktrees."
        ),
    )
    parser.add_argument(
        "--live-commit", metavar="COMMIT",
        help="commit to pin live qualification to (default: the default branch tip)",
    )
    return parser


def _list_scenarios() -> str:
    lines = [f"{len(SCENARIOS)} scenarios ({len(REQUIRED_KEYS)} required for PASS):", ""]
    for scenario in SCENARIOS:
        flag = "required" if scenario.required else "optional"
        lines.append(f"  {scenario.key:<32} [{flag}] {scenario.title}")
    return "\n".join(lines)


async def _run(args) -> int:
    if args.live:
        from evaluation.live import run_live

        report = await run_live(
            repo_path=Path(args.live),
            commit=args.live_commit,
            timeout=args.timeout,
        )
    else:
        from evaluation.harness import run_suite

        try:
            scenarios = select(args.scenarios, smoke=args.smoke)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(file=sys.stderr)
            print(_list_scenarios(), file=sys.stderr)
            return EXIT_USAGE

        mode = "smoke" if args.smoke else ("partial" if args.scenarios else "full")
        print(f"Running {len(scenarios)} scenario(s) [{mode}] ...", flush=True)

        started = time.monotonic()
        results, environment = await run_suite(
            scenarios,
            keep_workdir=args.keep_workdir,
            timeout=args.timeout,
            progress=lambda s: print(f"  -> {s.key}", flush=True),
        )
        report = QualificationReport(
            sceneworks_version=__version__,
            backend="fake (scripted)",
            mode=mode,
            results=results,
            required_scenarios=REQUIRED_KEYS,
            environment=environment,
        )
        report.duration_seconds = time.monotonic() - started
        report.finished_at = _now()

    print()
    print(summary(report, verbose=args.verbose))

    if args.json:
        payload = json.dumps(report.as_dict(), indent=2)
        if args.json == "-":
            print(payload)
        else:
            path = Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            print(f"\nmachine-readable report: {path}")

    return EXIT_CODES[report.verdict]


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _use_utf8_stdout() -> None:
    """Print report text as UTF-8 regardless of the console code page.

    Scenario titles and metric explanations contain em dashes and arrows. On a
    default Windows console (cp1252) writing them raises UnicodeEncodeError and
    the qualification run dies while *formatting* its result — which would make
    a genuine PASS or FAIL look like a crash.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - exotic streams
                pass


def main(argv: list[str] | None = None) -> int:
    _use_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        print(_list_scenarios())
        return 0

    logging.basicConfig(
        level=logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""CLI for SceneWorks productivity benchmarking.

Examples:

    cd backend
    uv run python -m benchmarking --manifest ../benchmarks/pcs.json
    uv run python -m benchmarking --manifest bench.json --mode sceneworks
    uv run python -m benchmarking --manifest bench.json --json evidence.json
    uv run python -m benchmarking --manifest bench.json --validate

Exit status describes whether benchmark evidence is complete, not whether an
agent solved every engineering task. A FAIL trial is valid measured evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from benchmarking.models import BenchmarkManifest, BenchmarkStatus, TrialVerdict
from benchmarking.runner import run_manifest

EXIT_COMPLETE = 0
EXIT_INCOMPLETE = 2
EXIT_USAGE = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarking",
        description="Paired SceneWorks vs direct-agent productivity benchmark.",
    )
    parser.add_argument("--manifest", required=True, metavar="PATH")
    parser.add_argument(
        "--mode",
        choices=("both", "sceneworks", "direct"),
        default="both",
        help="which execution path(s) to benchmark (default: both)",
    )
    parser.add_argument("--json", metavar="PATH", help="write report JSON to PATH or '-'")
    parser.add_argument("--workdir", metavar="PATH", help="explicit benchmark work directory")
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="validate the manifest schema only; do not run agents",
    )
    return parser


def load_manifest(path: Path) -> BenchmarkManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BenchmarkManifest.model_validate(payload)


def summary(report) -> str:
    pass_count = sum(t.verdict is TrialVerdict.PASS for t in report.trials)
    fail_count = sum(t.verdict is TrialVerdict.FAIL for t in report.trials)
    blocked_count = sum(t.verdict is TrialVerdict.BLOCKED for t in report.trials)
    lines = [
        f"Benchmark: {report.manifest_name}",
        f"Status: {report.status.value}",
        f"Trials: {len(report.trials)}  PASS={pass_count} FAIL={fail_count} BLOCKED={blocked_count}",
    ]
    for aggregate in report.aggregates:
        rate = (
            f"{aggregate.success_rate * 100:.1f}%"
            if aggregate.success_rate is not None
            else "n/a"
        )
        median = (
            f"{aggregate.median_success_seconds:.1f}s"
            if aggregate.median_success_seconds is not None
            else "n/a"
        )
        lines.append(
            f"{aggregate.mode}: success={rate}, median successful time={median}, "
            f"mean human interventions={aggregate.mean_human_interventions}, "
            f"mean agent executions={aggregate.mean_agent_executions}, "
            f"backend failures={aggregate.backend_failures}"
        )
    if report.comparisons:
        outcomes: dict[str, int] = {}
        for item in report.comparisons:
            outcomes[item.outcome] = outcomes.get(item.outcome, 0) + 1
        lines.append(
            "Paired outcomes: "
            + ", ".join(f"{key}={value}" for key, value in sorted(outcomes.items()))
        )
    for trial in report.trials:
        model = next(
            (target.get("model") for target in trial.execution_targets if target.get("model")),
            None,
        )
        suffix = f" model={model}" if model else ""
        lines.append(
            f"  {trial.task_key} #{trial.repeat} {trial.mode:<10} "
            f"{trial.verdict.value:<7} {trial.duration_seconds:.1f}s{suffix}"
        )
        if trial.blocker:
            lines.append(f"    blocker: {trial.blocker}")
        if trial.expected_files_missing:
            lines.append(f"    expected files missing: {trial.expected_files_missing}")
        if trial.forbidden_files_changed:
            lines.append(f"    forbidden files changed: {trial.forbidden_files_changed}")
    return "\n".join(lines)


async def _run(args) -> int:
    try:
        manifest = load_manifest(Path(args.manifest))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.validate:
        print(
            f"valid benchmark manifest: {manifest.name} "
            f"({len(manifest.tasks)} task(s), {manifest.repeats} repeat(s))"
        )
        return EXIT_COMPLETE

    modes = (
        ("sceneworks", "direct")
        if args.mode == "both"
        else (args.mode,)
    )
    report = await run_manifest(
        manifest,
        modes=modes,
        keep_workdir=args.keep_workdir,
        workdir=Path(args.workdir) if args.workdir else None,
        progress=lambda task, mode, repeat: print(
            f"-> {task.key} #{repeat} [{mode}]", flush=True
        ),
    )
    print()
    print(summary(report))

    if args.json:
        payload = json.dumps(report.model_dump(mode="json"), indent=2)
        if args.json == "-":
            print(payload)
        else:
            target = Path(args.json)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
            print(f"\nevidence: {target}")

    return EXIT_COMPLETE if report.status is BenchmarkStatus.COMPLETE else EXIT_INCOMPLETE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""Human-readable qualification summary.

Two rules the V2.5 report broke:

- Never print PASS next to a failing measurement. A verdict is derived from the
  checks, and the checks are printed, so the two cannot disagree.
- Never print a number the harness did not measure. Unsupported metrics are
  named as unsupported, with the reason.
"""

from __future__ import annotations

from evaluation.outcomes import QualificationReport, ScenarioResult, Verdict

_WIDTH = 78
_RULE = "=" * _WIDTH
_THIN = "-" * _WIDTH

_MARK = {
    Verdict.PASS: "PASS   ",
    Verdict.FAIL: "FAIL   ",
    Verdict.BLOCKED: "BLOCKED",
    Verdict.NOT_RUN: "NOT_RUN",
}


def summary(report: QualificationReport, verbose: bool = False) -> str:
    lines: list[str] = [
        _RULE,
        f"SceneWorks qualification — {report.verdict.value}",
        _RULE,
        f"version   : {report.sceneworks_version}",
        f"backend   : {report.backend}",
        f"mode      : {report.mode}",
        f"duration  : {report.duration_seconds:.1f}s",
    ]

    counts = report.counts()
    lines.append(
        "scenarios : "
        + ", ".join(f"{k}={v}" for k, v in counts.items() if v or k == "PASS")
    )
    lines.append("")

    for result in report.results:
        lines.extend(_scenario_lines(result, verbose))

    lines.append(_THIN)
    if report.missing_required:
        lines.append("Required scenarios that did not pass:")
        for key in report.missing_required:
            lines.append(f"  - {key}")
        lines.append("")
        lines.append(
            "A release cannot report PASS while a required scenario is missing, "
            "blocked or failing."
        )
    else:
        lines.append("All required scenarios passed.")

    unsupported = report.as_dict()["unsupported_metrics"]
    if unsupported:
        lines.append("")
        lines.append("Metrics this suite does NOT measure (not scored, not estimated):")
        for name, reason in unsupported.items():
            lines.append(f"  - {name}: {_wrap(reason, indent=6)}")

    lines.append(_RULE)
    lines.append(f"VERDICT: {report.verdict.value}")
    lines.append(_RULE)
    return "\n".join(lines)


def _scenario_lines(result: ScenarioResult, verbose: bool) -> list[str]:
    lines = [f"[{_MARK[result.verdict]}] {result.key} — {result.title}"]

    obs = result.observations
    lines.append(
        f"           {len([c for c in result.checks if c.passed])}/"
        f"{len(result.checks)} checks passed, {obs.duration_seconds:.1f}s"
    )

    for blocker in result.blockers:
        lines.append(f"           BLOCKED: {_wrap(blocker, indent=20)}")

    show = result.checks if verbose else result.failed_checks
    for check in show:
        state = "ok  " if check.passed else "FAIL"
        lines.append(f"           {state} {check.name}")
        lines.append(
            f"                expected={_fmt(check.expected)} "
            f"actual={_fmt(check.actual)}"
        )
        if check.detail:
            lines.append(f"                {_wrap(check.detail, indent=16)}")

    if verbose:
        lines.extend(_provenance_lines(result))

    lines.append("")
    return lines


def _provenance_lines(result: ScenarioResult) -> list[str]:
    obs = result.observations
    out = ["           provenance:"]
    out.append(f"                base commit   : {obs.base_commit or '-'}")
    out.append(f"                result commit : {obs.result_commit or '-'}")
    out.append(f"                branch        : {obs.task_branch or '-'}")
    out.append(f"                task          : {obs.task_id}")
    out.append(f"                executions    : {len(obs.execution_ids)}")
    if obs.files_changed:
        out.append(
            f"                files changed : {', '.join(sorted(obs.files_changed))}"
        )
    if obs.diff_bytes is not None:
        out.append(f"                diff bytes    : {obs.diff_bytes}")
    if obs.review_verdicts:
        out.append(f"                review        : {list(obs.review_verdicts)}")
    if obs.recovery:
        out.append(f"                recovery      : {obs.recovery.get('retry_behaviour')}")
    return out


def _fmt(value) -> str:
    if isinstance(value, (set, frozenset)):
        return "{" + ", ".join(sorted(str(v) for v in value)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def _wrap(text: str, indent: int) -> str:
    import textwrap

    wrapped = textwrap.wrap(text, width=_WIDTH - indent) or [""]
    pad = " " * indent
    return ("\n" + pad).join(wrapped)

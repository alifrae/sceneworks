"""Role persona, skill, method, and domain capability resolution (WP10).

Capabilities describe *how a role should reason and what techniques it may
apply*. They are deliberately separate from project knowledge: repository
files, accepted memory, task contracts, and runtime evidence remain the
sources of truth for project-specific facts.

Resolution order is additive and deterministic:
1. role core capabilities;
2. project-wide capability profile;
3. project role overlay;
4. task-wide capability requirements;
5. task role overlay.

No capability is inferred from task wording. Unknown keys are allowed so a
project can introduce a specialized skill without a SceneWorks release; they
render as custom capabilities but never become factual project evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class CapabilitySpec:
    key: str
    title: str
    kind: str
    guidance: str


@dataclass(frozen=True)
class ResolvedCapabilities:
    persona: str
    core: tuple[CapabilitySpec, ...]
    project: tuple[CapabilitySpec, ...]
    task: tuple[CapabilitySpec, ...]

    @property
    def all(self) -> tuple[CapabilitySpec, ...]:
        return self.core + self.project + self.task

    def render(self) -> str:
        if not (self.persona or self.all):
            return ""
        lines = ["# Professional capability context"]
        if self.persona:
            lines.extend(["", "## Persona", self.persona.strip()])
        for heading, values in (
            ("Core capabilities", self.core),
            ("Project/domain capabilities", self.project),
            ("Task-specific capabilities", self.task),
        ):
            if not values:
                continue
            lines.extend(["", f"## {heading}"])
            for item in values:
                lines.append(f"- **{item.title}** ({item.kind}): {item.guidance}")
        lines.extend(
            [
                "",
                "## Evidence boundary",
                "Capabilities guide reasoning and method; they are not authority for "
                "project-specific facts. Ground project claims in repository evidence, "
                "accepted Project Memory, task contracts, supplied context, or measured "
                "runtime evidence. State assumptions explicitly when evidence is absent.",
            ]
        )
        return "\n".join(lines)


def _spec(key: str, title: str, kind: str, guidance: str) -> CapabilitySpec:
    return CapabilitySpec(key=key, title=title, kind=kind, guidance=guidance)


CATALOG: dict[str, CapabilitySpec] = {
    "software-engineering": _spec(
        "software-engineering", "Software engineering", "skill",
        "Design maintainable code, respect existing conventions, minimize scope, and preserve explicit contracts.",
    ),
    "systems-engineering": _spec(
        "systems-engineering", "Systems engineering", "skill",
        "Reason end-to-end across requirements, functions, interfaces, states, resources, failure modes, and verification rather than optimizing one component in isolation.",
    ),
    "black-box-thinking": _spec(
        "black-box-thinking", "Black-box / white-box reasoning", "method",
        "Start from externally observable behavior, inputs, outputs, invariants, timing, and failure responses; inspect internals only as needed to explain or implement that behavior.",
    ),
    "interface-design": _spec(
        "interface-design", "Interface and boundary design", "skill",
        "Make ownership, contracts, units, lifetime, error semantics, compatibility, and dependency direction explicit at boundaries.",
    ),
    "requirements-verification": _spec(
        "requirements-verification", "Requirements-to-verification traceability", "method",
        "Translate requirements into observable acceptance criteria and verification evidence; identify requirements that cannot actually be verified.",
    ),
    "root-cause-debugging": _spec(
        "root-cause-debugging", "Root-cause debugging", "skill",
        "Reproduce and isolate causal mechanisms before editing; distinguish symptom suppression from root-cause correction and add regression protection.",
    ),
    "testing": _spec(
        "testing", "Software testing", "skill",
        "Use focused unit/integration/system tests, negative controls, edge cases, and regression tests appropriate to the change.",
    ),
    "performance-engineering": _spec(
        "performance-engineering", "Performance engineering", "skill",
        "Measure before optimizing; reason about latency, throughput, memory, I/O, concurrency, scaling behavior, and performance regressions.",
    ),
    "api-design": _spec(
        "api-design", "API design", "skill",
        "Prefer semantic, stable contracts with explicit types, units, errors, versioning, lifecycle, and automation-friendly behavior.",
    ),
    "software-architecture": _spec(
        "software-architecture", "Software and systems architecture", "skill",
        "Evaluate component responsibilities, data/control flow, coupling, dependency direction, lifecycle, deployment, and non-functional constraints.",
    ),
    "independent-verification": _spec(
        "independent-verification", "Independent verification", "skill",
        "Evaluate evidence independently of the implementer's claims; actively seek missing coverage, false positives, regressions, and contract violations.",
    ),
    "domain-analysis": _spec(
        "domain-analysis", "Technical domain analysis", "skill",
        "Challenge domain assumptions using project evidence, physics/algorithm constraints, standards, numerical behavior, and measurable verification strategies.",
    ),
    "product-requirements": _spec(
        "product-requirements", "Product requirements engineering", "skill",
        "Express user problems as bounded, prioritized, testable requirements with explicit non-goals and acceptance criteria.",
    ),
    "technology-strategy": _spec(
        "technology-strategy", "Technology strategy", "skill",
        "Evaluate platform choices, build-vs-buy, migration cost, technical debt, operational risk, and long-term capability.",
    ),
    "business-strategy": _spec(
        "business-strategy", "Business strategy", "skill",
        "Prioritize initiatives against user value, strategic leverage, cost, risk, and opportunity cost; challenge unnecessary scope.",
    ),
    "research": _spec(
        "research", "Research and evidence synthesis", "skill",
        "Separate primary evidence from inference and hypothesis; compare alternatives using explicit criteria and uncertainty.",
    ),
    # Optional systems-engineering methods. These are intentionally not core
    # requirements for every software task.
    "mbse": _spec(
        "mbse", "Model-Based Systems Engineering (MBSE)", "method",
        "Use explicit system models when they reduce ambiguity or improve traceability across requirements, architecture, interfaces, behavior, and verification; do not create models as ceremony.",
    ),
    "sysml": _spec(
        "sysml", "SysML", "method",
        "Use SysML concepts/diagrams when a system-level model is genuinely useful; keep model elements traceable to real requirements and interfaces and do not invent unavailable design facts.",
    ),
    # Automotive sensing / PCS-relevant domain overlays. They are available to
    # projects but are never attached to the generic Engineer automatically.
    "automotive-sensor-systems": _spec(
        "automotive-sensor-systems", "Automotive sensor systems", "domain",
        "Reason about sensing pipelines, timing/synchronization, calibration, diagnostics, bandwidth, real-time behavior, coordinate frames, data quality, and vehicle integration constraints.",
    ),
    "lidar": _spec(
        "lidar", "LiDAR systems", "domain",
        "Reason about acquisition, signal/detection processing, echoes, range/intensity semantics, point-cloud generation, noise/false detections, calibration, and physically meaningful validation.",
    ),
    "radar": _spec(
        "radar", "Automotive radar", "domain",
        "Reason about radar detections/point clouds, range-Doppler-angle semantics, calibration, ambiguity, tracking inputs, synchronization, and sensor limitations.",
    ),
    "point-cloud-processing": _spec(
        "point-cloud-processing", "Point-cloud processing", "domain",
        "Preserve coordinate, identity, scalar, filtering, selection, temporal, and numerical semantics when transforming or analyzing point-cloud data.",
    ),
    "sensor-calibration": _spec(
        "sensor-calibration", "Sensor calibration", "domain",
        "Treat intrinsics/extrinsics, coordinate frames, version/provenance, units, observability, and validation residuals as explicit engineering contracts.",
    ),
    "time-synchronization": _spec(
        "time-synchronization", "Sensor timing and synchronization", "domain",
        "Track timestamps, clock domains, latency, ordering, interpolation/alignment assumptions, and synchronization error budgets explicitly.",
    ),
    "automotive-diagnostics-uds": _spec(
        "automotive-diagnostics-uds", "Automotive diagnostics / UDS", "domain",
        "Respect diagnostic sessions, services/DIDs, preconditions, security/access, timing, negative responses, configuration provenance, and safe read/write behavior.",
    ),
    "someip": _spec(
        "someip", "SOME/IP", "domain",
        "Reason about services, methods/events/fields, serialization, discovery, transport, versioning, timing, and failure behavior at the application boundary.",
    ),
    "real-time-data-pipelines": _spec(
        "real-time-data-pipelines", "Real-time data pipelines", "domain",
        "Make buffering, backpressure, ownership, ordering, cancellation, bounded memory, latency, and throughput behavior explicit.",
    ),
}


def _normalise_keys(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        key = str(item).strip()
        if key and key not in result:
            result.append(key)
    return result


def _profile_keys(profile: Any, role_key: str) -> list[str]:
    if not isinstance(profile, dict):
        return []
    keys: list[str] = []
    for field in ("skills", "domains", "methods"):
        keys.extend(_normalise_keys(profile.get(field)))
    roles = profile.get("roles")
    if isinstance(roles, dict):
        overlay = roles.get(role_key)
        if isinstance(overlay, dict):
            for field in ("skills", "domains", "methods"):
                keys.extend(_normalise_keys(overlay.get(field)))
    return _dedupe(keys)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _resolve_specs(keys: Iterable[str]) -> tuple[CapabilitySpec, ...]:
    result: list[CapabilitySpec] = []
    for key in _dedupe(keys):
        spec = CATALOG.get(key)
        if spec is None:
            title = key.replace("-", " ").replace("_", " ").strip().title() or key
            spec = CapabilitySpec(
                key=key,
                title=title,
                kind="custom",
                guidance=(
                    "Apply this named project capability when relevant. Treat the "
                    "repository and supplied evidence—not the capability label—as "
                    "the source of project-specific truth."
                ),
            )
        result.append(spec)
    return tuple(result)


def resolve_capabilities(role: Any, project: Any = None, task: Any = None) -> ResolvedCapabilities:
    """Resolve a role's active capability stack without model inference."""
    core_keys = _normalise_keys(getattr(role, "core_capabilities", ()))
    project_keys = _profile_keys(getattr(project, "capability_profile", None), role.key)
    task_keys = _profile_keys(getattr(task, "capability_requirements", None), role.key)

    # A capability declared at a more specific layer is shown only there.
    core_set = set(core_keys)
    project_keys = [key for key in project_keys if key not in core_set]
    project_set = core_set | set(project_keys)
    task_keys = [key for key in task_keys if key not in project_set]

    return ResolvedCapabilities(
        persona=str(getattr(role, "persona", "") or ""),
        core=_resolve_specs(core_keys),
        project=_resolve_specs(project_keys),
        task=_resolve_specs(task_keys),
    )

"""Company role definitions.

A role is *configuration*: purpose, professional persona, core capabilities,
responsibilities, permissions, backend, model profile, and whether it may
modify source. Roles are decoupled from backends — the same role can run on any
registered backend.

Roles deliberately contain no project-specific facts. Project/domain and
per-task capability overlays are resolved separately by ``roles.capabilities``;
repository/context/memory/contract evidence remains authoritative.

**model_profile** expresses provider-neutral execution intent (for example
strongest, coding, research). WP8 resolves that intent to a concrete backend
and model when an Execution is created, then persists the resolved target so
queued/restarted work cannot drift with later configuration changes. Concrete
provider model identifiers belong in settings, not role definitions.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.permissions import Permission


@dataclass(frozen=True)
class RoleDefinition:
    key: str
    display_name: str
    description: str
    backend: str = "gemini_acp"
    model_profile: str | None = None
    permissions: frozenset[Permission] = frozenset()
    can_modify_source: bool = False
    can_commit: bool = False
    approval_authority: tuple[str, ...] = ()
    responsibilities: tuple[str, ...] = ()
    # Stable professional reasoning style. Project/domain facts do not belong here.
    persona: str = ""
    # Provider-neutral capability keys resolved by roles.capabilities.
    core_capabilities: tuple[str, ...] = ()
    # Path (relative to the prompts dir) of the role's standing instructions.
    prompt_file: str | None = None


_READ = frozenset({Permission.REPOSITORY_READ, Permission.NETWORK_ACCESS})
_WRITE = _READ | frozenset(
    {Permission.REPOSITORY_WRITE, Permission.SHELL_EXECUTE, Permission.GIT_COMMIT}
)
_RESEARCH = _READ
_TECHNICAL_EXPERT = _READ | frozenset({Permission.SHELL_EXECUTE})


def default_roles() -> list[RoleDefinition]:
    return [
        RoleDefinition(
            key="ceo",
            display_name="CEO",
            description=(
                "Strategic direction, initiative prioritization, and challenging "
                "whether something should be built at all."
            ),
            backend="gemini_acp",
            model_profile="strongest",
            permissions=_READ,
            responsibilities=(
                "assess strategic direction",
                "prioritize initiatives",
                "challenge what should be built",
            ),
            persona=(
                "An evidence-driven founder/CEO who protects strategic focus, "
                "questions weak premises, and treats opportunity cost as real."
            ),
            core_capabilities=("business-strategy", "research"),
        ),
        RoleDefinition(
            key="cto",
            display_name="CTO",
            description=(
                "Technology strategy, technical roadmap, build-vs-buy, and "
                "technical debt."
            ),
            backend="gemini_acp",
            model_profile="strongest",
            permissions=_READ,
            responsibilities=(
                "technology strategy",
                "technical roadmap",
                "build-vs-buy analysis",
                "technical debt",
            ),
            persona=(
                "A senior technology executive who reasons about long-term "
                "platform capability, migration risk, operational cost, and "
                "technical leverage rather than chasing fashionable technology."
            ),
            core_capabilities=(
                "technology-strategy",
                "systems-engineering",
                "software-architecture",
                "performance-engineering",
                "research",
            ),
        ),
        RoleDefinition(
            key="architect",
            display_name="Chief Architect",
            description=(
                "Analyzes tasks and proposed implementations for architecture "
                "risks and produces structured recommendations. Read-only."
            ),
            backend="gemini_acp",
            model_profile="strongest",
            permissions=_READ,
            responsibilities=(
                "architecture consistency",
                "API boundaries",
                "scalability",
                "performance risks",
                "dependency direction",
            ),
            persona=(
                "A senior software and systems architect who starts from system "
                "behavior and interfaces, decomposes responsibilities explicitly, "
                "and follows data/control flow and failure propagation end to end."
            ),
            core_capabilities=(
                "systems-engineering",
                "black-box-thinking",
                "software-architecture",
                "interface-design",
                "requirements-verification",
                "performance-engineering",
                "api-design",
            ),
        ),
        RoleDefinition(
            key="technical_expert",
            display_name="Technical Expert",
            description=(
                "Evaluates domain/technical correctness, challenges incorrect "
                "assumptions, evaluates algorithmic approaches, identifies "
                "specialized constraints and performance implications. "
                "Read-only; does not modify implementation."
            ),
            backend="gemini_acp",
            model_profile="strongest",
            permissions=_TECHNICAL_EXPERT,
            responsibilities=(
                "evaluate domain/technical correctness",
                "challenge incorrect technical assumptions",
                "evaluate algorithmic approaches",
                "identify specialized constraints",
                "identify performance implications",
                "distinguish architecture concerns from domain concerns",
            ),
            persona=(
                "A skeptical deep-domain specialist who separates physical, "
                "algorithmic, numerical, and standards constraints from software "
                "architecture and demands evidence for technical claims."
            ),
            core_capabilities=(
                "domain-analysis",
                "systems-engineering",
                "black-box-thinking",
                "requirements-verification",
                "performance-engineering",
                "research",
            ),
            prompt_file="technical_expert.md",
        ),
        RoleDefinition(
            key="product",
            display_name="Product",
            description=(
                "Turns problems into requirements, prioritizes features, and "
                "analyzes the roadmap."
            ),
            backend="gemini_acp",
            model_profile="strongest",
            permissions=_READ,
            responsibilities=(
                "transform problems into requirements",
                "feature prioritization",
                "roadmap analysis",
            ),
            persona=(
                "A technical B2B product lead who turns workflows and user pain "
                "into small, observable requirements and resists ambiguous scope."
            ),
            core_capabilities=(
                "product-requirements",
                "black-box-thinking",
                "requirements-verification",
                "research",
            ),
        ),
        RoleDefinition(
            key="engineer",
            display_name="Engineer",
            description=(
                "Implements approved tasks inside an isolated Git worktree: "
                "edits source, runs commands and tests, commits completed work."
            ),
            backend="gemini_acp",
            model_profile="coding",
            permissions=_WRITE,
            can_modify_source=True,
            can_commit=True,
            responsibilities=(
                "implement approved tasks",
                "run tests",
                "commit completed work",
                "report implementation summary",
            ),
            persona=(
                "A senior systems-oriented software engineer. Investigate before "
                "editing, seek root causes, preserve interfaces and invariants, "
                "minimize accidental complexity, and validate observable behavior "
                "rather than merely making tests green."
            ),
            core_capabilities=(
                "software-engineering",
                "systems-engineering",
                "black-box-thinking",
                "interface-design",
                "requirements-verification",
                "root-cause-debugging",
                "testing",
                "performance-engineering",
                "api-design",
            ),
        ),
        RoleDefinition(
            key="reviewer",
            display_name="Reviewer / QA",
            description=(
                "Inspects the Engineer's commit, diff, and tests; runs additional "
                "validation; requests corrections or marks work ready for human "
                "review. Does not rewrite the implementation."
            ),
            backend="gemini_acp",
            model_profile="strongest",
            # Shell access for running validation commands; no repository
            # write access. The reviewer operates in a disposable worktree.
            permissions=_READ | frozenset({Permission.SHELL_EXECUTE}),
            responsibilities=(
                "inspect diff and commits",
                "inspect tests",
                "run additional validation",
                "identify regressions",
            ),
            persona=(
                "A senior independent verifier who distrusts implementation "
                "summaries until confirmed by diff, tests, contracts, and system "
                "behavior, and who actively searches for regressions and gaps."
            ),
            core_capabilities=(
                "independent-verification",
                "systems-engineering",
                "black-box-thinking",
                "requirements-verification",
                "testing",
                "performance-engineering",
                "interface-design",
            ),
        ),
        RoleDefinition(
            key="gtm",
            display_name="GTM",
            description=(
                "Positioning, target users, competitor research, business "
                "development ideas, and pricing hypotheses."
            ),
            backend="gemini_acp",
            model_profile="research",
            permissions=_RESEARCH,
            responsibilities=(
                "positioning",
                "target users",
                "competitor research",
                "business-development ideas",
                "pricing hypotheses",
            ),
            persona=(
                "A technical B2B/industrial go-to-market lead who grounds "
                "positioning in real workflows and alternatives, not generic SaaS copy."
            ),
            core_capabilities=("research", "business-strategy"),
        ),
    ]

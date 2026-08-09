"""Company role definitions.

A role is *configuration*: purpose, responsibilities, permissions, backend,
model profile, and whether it may modify source. Roles are decoupled from
backends â€” the same role can run on any registered backend.

Roles are deliberately free of project-specific instructions. Project
architecture rules arrive separately as project context files.
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
    # Path (relative to the prompts dir) of the role's standing instructions.
    prompt_file: str | None = None


_READ = frozenset({Permission.REPOSITORY_READ, Permission.NETWORK_ACCESS})
_WRITE = _READ | frozenset(
    {Permission.REPOSITORY_WRITE, Permission.SHELL_EXECUTE, Permission.GIT_COMMIT}
)
_RESEARCH = _READ


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
        ),
    ]

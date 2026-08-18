"""Project Policy service (WP4).

CRUD for a project's structured engineering contract (`ProjectPolicy`), plus
rendering it into the labelled prompt block every role reads. Deterministic
checking (protected paths) is a separate, pure module: `policy_check.py`.

One row per project. `get_or_default()` returns an empty, unsaved policy for a
project that has never configured one, so callers (prompt building, the
Reviewer's deterministic check) never need a null check — an unconfigured
project simply has a policy with every list empty, which renders to nothing
and matches nothing.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ProjectPolicy

#: Field name -> human label, in the order they render. One source of truth
#: for both the renderer and the API schema field list, so a category added
#: to the model cannot silently fail to appear in the prompt.
POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("protected_paths", "Protected paths (must not be hand-edited without explicit authorisation)"),
    ("architecture_invariants", "Architecture invariants"),
    ("forbidden_dependency_directions", "Forbidden dependency directions"),
    ("documentation_requirements", "Documentation requirements"),
    ("performance_constraints", "Performance constraints"),
    ("required_review_checks", "Required review checks"),
    ("go_no_go_commands", "Go/no-go qualification commands"),
    ("release_requirements", "Release requirements"),
)


class ProjectPolicyService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def get(self, project_id: int) -> ProjectPolicy | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProjectPolicy).where(ProjectPolicy.project_id == project_id)
            )
            return result.scalar_one_or_none()

    async def get_or_default(self, project_id: int) -> ProjectPolicy:
        """A policy object for every project, configured or not.

        Not persisted when absent -- this is a read-time convenience, not an
        implicit write. Nothing is created until `upsert()` is called.

        List fields are set explicitly to `[]` rather than left to the
        column's `default=list`: that default is an INSERT-time default,
        evaluated by SQLAlchemy at flush, not at `__init__` -- an unsaved
        instance's list columns are `None`, not `[]`, until it is actually
        committed (verified directly: `ProjectPolicy(project_id=5).protected_paths`
        is `None`). Every caller of this method (prompt rendering, the
        Reviewer's deterministic check, the API schema) depends on getting
        real empty lists back for an unconfigured project, not `None`.
        """
        policy = await self.get(project_id)
        if policy is not None:
            return policy
        return ProjectPolicy(
            project_id=project_id,
            protected_paths=[],
            go_no_go_commands=[],
            forbidden_dependency_directions=[],
            architecture_invariants=[],
            documentation_requirements=[],
            performance_constraints=[],
            required_review_checks=[],
            release_requirements=[],
            policy_file_paths=[],
        )

    async def upsert(
        self,
        project_id: int,
        *,
        protected_paths: list[str] | None = None,
        go_no_go_commands: list[str] | None = None,
        forbidden_dependency_directions: list[str] | None = None,
        architecture_invariants: list[str] | None = None,
        documentation_requirements: list[str] | None = None,
        performance_constraints: list[str] | None = None,
        required_review_checks: list[str] | None = None,
        release_requirements: list[str] | None = None,
        policy_file_paths: list[str] | None = None,
    ) -> ProjectPolicy:
        """Create or fully replace a project's policy.

        Full replace, not a merge: a policy is a project's current declared
        contract, not an accumulating log. Fields left as None are cleared to
        empty, matching PUT semantics -- the caller sends the whole policy.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProjectPolicy).where(ProjectPolicy.project_id == project_id)
            )
            policy = result.scalar_one_or_none()
            if policy is None:
                policy = ProjectPolicy(project_id=project_id)
                session.add(policy)

            policy.protected_paths = list(protected_paths or [])
            policy.go_no_go_commands = list(go_no_go_commands or [])
            policy.forbidden_dependency_directions = list(
                forbidden_dependency_directions or []
            )
            policy.architecture_invariants = list(architecture_invariants or [])
            policy.documentation_requirements = list(documentation_requirements or [])
            policy.performance_constraints = list(performance_constraints or [])
            policy.required_review_checks = list(required_review_checks or [])
            policy.release_requirements = list(release_requirements or [])
            policy.policy_file_paths = list(policy_file_paths or [])

            await session.commit()
            await session.refresh(policy)
            return policy

    async def delete(self, project_id: int) -> bool:
        policy = await self.get(project_id)
        if policy is None:
            return False
        async with self._session_factory() as session:
            row = await session.get(ProjectPolicy, policy.id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    def is_empty(self, policy: ProjectPolicy) -> bool:
        return not any(getattr(policy, field) for field, _ in POLICY_FIELDS)


def render_policy(policy: ProjectPolicy | None) -> str:
    """Render a policy into the labelled, enforceable prompt block.

    Deliberately separate from `PromptBuilder._read_project_context`'s prose
    context files: this block exists to be checked against, not merely read.
    Empty categories are omitted rather than shown as "(none)" -- a role
    should not have to skim past eight empty headings to find the two rules
    that actually apply.
    """
    if policy is None:
        return ""
    sections: list[str] = []
    for field, label in POLICY_FIELDS:
        items = getattr(policy, field) or []
        if not items:
            continue
        body = "\n".join(f"- {item}" for item in items)
        sections.append(f"## {label}\n{body}")
    if not sections:
        return ""
    return "\n\n".join(sections)

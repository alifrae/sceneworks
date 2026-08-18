"""Project Policy tests (WP4).

Three layers, matching the module split:

- pure protected-path matching (`policy_check.py`) -- no I/O, no database
- service CRUD and rendering (`policy.py`) -- against a real database
- prompt injection -- policy reaches every role's actual prompt text through
  `PromptBuilder`, not just the service layer in isolation

The closure criterion this roadmap sets for WP4 -- "a reference project must
demonstrate a policy violation being detected during review" -- is proven at
the workflow level by the `policy-violation` qualification scenario
(`evaluation/scenarios.py`), not here; these tests are the unit-level
foundation under that proof.
"""

from __future__ import annotations

import inspect

import pytest

from app.models import ProjectPolicy
from app.roles.definitions import default_roles
from app.roles.prompts import PromptBuilder
from app.services.policy import POLICY_FIELDS, ProjectPolicyService, render_policy
from app.services.policy_check import (
    PATTERN_SYNTAX_NOTE,
    ProtectedPathViolation,
    check_protected_paths,
    render_violations,
)


@pytest.fixture
def policy_service(context) -> ProjectPolicyService:
    return context.policy


# --------------------------------------------------------- pure: path matching


def test_exact_path_match():
    violations = check_protected_paths(["api/facade.py"], ["api/facade.py"])
    assert len(violations) == 1
    assert violations[0] == ProtectedPathViolation("api/facade.py", "api/facade.py")


def test_glob_matches_across_directory_boundaries():
    """Documented and verified semantics: `*` crosses `/`, unlike shell globs."""
    violations = check_protected_paths(
        ["generated/*"], ["generated/foo/bar.py", "generated/bar.py", "other/file.py"],
    )
    matched = {v.path for v in violations}
    assert matched == {"generated/foo/bar.py", "generated/bar.py"}


def test_unrelated_file_does_not_match():
    assert check_protected_paths(["generated/*"], ["src/main.py"]) == []


def test_matching_is_case_sensitive_and_platform_independent():
    """fnmatchcase, not fnmatch: must behave identically on Windows and Linux."""
    assert check_protected_paths(["api/facade.py"], ["API/facade.py"]) == []
    assert check_protected_paths(["api/facade.py"], ["api/facade.py"]) != []


def test_no_patterns_or_no_changed_files_is_empty():
    assert check_protected_paths([], ["api/facade.py"]) == []
    assert check_protected_paths(["api/*"], []) == []


def test_blank_patterns_are_skipped_not_treated_as_match_everything():
    assert check_protected_paths(["", "   "], ["anything.py"]) == []


def test_one_file_can_match_multiple_patterns():
    """Every match is individually attributable -- a file is not just 'flagged'."""
    violations = check_protected_paths(
        ["api/*", "api/facade.py"], ["api/facade.py"],
    )
    assert len(violations) == 2
    assert {v.pattern for v in violations} == {"api/*", "api/facade.py"}


def test_violations_are_ordered_deterministically():
    violations = check_protected_paths(
        ["z/*", "a/*"], ["z/file.py", "a/file.py"],
    )
    assert [v.path for v in violations] == ["a/file.py", "z/file.py"]


def test_pattern_syntax_is_documented():
    assert "fnmatchcase" in PATTERN_SYNTAX_NOTE
    assert "*" in PATTERN_SYNTAX_NOTE


# ------------------------------------------------------- pure: violation render


def test_no_violations_renders_nothing():
    assert render_violations([]) == ""


def test_violations_render_as_grounded_not_suggested():
    """The Reviewer must be told this was computed, not a hunch to weigh."""
    text = render_violations([ProtectedPathViolation("api/facade.py", "api/*")])
    assert "api/facade.py" in text
    assert "api/*" in text
    assert "computed directly from the Git diff" in text
    assert "CHANGES_REQUESTED" in text


def test_every_violation_is_individually_named():
    text = render_violations([
        ProtectedPathViolation("a.py", "a.py"),
        ProtectedPathViolation("b.py", "b.py"),
    ])
    assert "`a.py`" in text
    assert "`b.py`" in text


# ------------------------------------------------------------------ service CRUD


async def test_get_returns_none_when_unconfigured(policy_service):
    assert await policy_service.get(9999) is None


async def test_get_or_default_never_returns_none(policy_service):
    policy = await policy_service.get_or_default(9999)
    assert policy is not None
    assert policy.project_id == 9999


async def test_get_or_default_has_real_empty_lists_not_none(policy_service):
    """REGRESSION: mapped_column(default=list) is an INSERT-time default.

    An unsaved ProjectPolicy(project_id=x) has every list column as None, not
    [], until actually flushed -- verified directly against the ORM model.
    Every consumer (rendering, the deterministic check, the API schema) needs
    real empty lists for an unconfigured project.
    """
    policy = await policy_service.get_or_default(9999)
    for field, _ in POLICY_FIELDS:
        value = getattr(policy, field)
        assert value == [], f"{field} was {value!r}, not []"


async def test_get_or_default_is_not_persisted(policy_service):
    await policy_service.get_or_default(42)
    assert await policy_service.get(42) is None


async def test_upsert_creates_a_new_policy(policy_service):
    policy = await policy_service.upsert(
        1, protected_paths=["api/facade.py"], architecture_invariants=["x must not depend on y"],
    )
    assert policy.id is not None
    assert policy.protected_paths == ["api/facade.py"]
    assert policy.architecture_invariants == ["x must not depend on y"]
    # Fields not passed default to empty, not None.
    assert policy.go_no_go_commands == []


async def test_upsert_is_a_full_replace_not_a_merge(policy_service):
    await policy_service.upsert(1, protected_paths=["a.py"], required_review_checks=["check x"])
    replaced = await policy_service.upsert(1, protected_paths=["b.py"])

    assert replaced.protected_paths == ["b.py"]
    assert replaced.required_review_checks == [], (
        "upsert must replace the whole policy, not merge into the previous one"
    )


async def test_upsert_is_idempotent_on_the_same_project(policy_service):
    first = await policy_service.upsert(1, protected_paths=["a.py"])
    second = await policy_service.upsert(1, protected_paths=["b.py"])
    assert first.id == second.id, "a project must have exactly one policy row"


async def test_project_isolation(policy_service):
    await policy_service.upsert(1, protected_paths=["a.py"])
    await policy_service.upsert(2, protected_paths=["b.py"])

    assert (await policy_service.get(1)).protected_paths == ["a.py"]
    assert (await policy_service.get(2)).protected_paths == ["b.py"]


async def test_delete_removes_the_policy(policy_service):
    await policy_service.upsert(1, protected_paths=["a.py"])
    assert await policy_service.delete(1) is True
    assert await policy_service.get(1) is None


async def test_delete_of_unconfigured_project_is_false_not_an_error(policy_service):
    assert await policy_service.delete(9999) is False


async def test_is_empty(policy_service):
    empty = await policy_service.get_or_default(9999)
    assert policy_service.is_empty(empty) is True

    configured = await policy_service.upsert(1, protected_paths=["a.py"])
    assert policy_service.is_empty(configured) is False


# --------------------------------------------------------------------- rendering


def test_render_policy_none_is_empty_string():
    assert render_policy(None) == ""


def test_render_unconfigured_policy_is_empty_string():
    """An all-empty policy must render to nothing, not eight empty headings."""
    empty = ProjectPolicy(project_id=1, **{f: [] for f, _ in POLICY_FIELDS})
    assert render_policy(empty) == ""


def test_render_only_shows_populated_categories():
    policy = ProjectPolicy(
        project_id=1,
        protected_paths=["api/facade.py"],
        architecture_invariants=[],
        forbidden_dependency_directions=[],
        documentation_requirements=[],
        performance_constraints=[],
        required_review_checks=[],
        go_no_go_commands=[],
        release_requirements=[],
    )
    text = render_policy(policy)
    assert "api/facade.py" in text
    assert "Architecture invariants" not in text, "empty categories must not appear"


def test_render_labels_every_category_present():
    policy = ProjectPolicy(
        project_id=1,
        protected_paths=["a.py"],
        architecture_invariants=["inv"],
        forbidden_dependency_directions=["ui must not import db"],
        documentation_requirements=["update usage.md"],
        performance_constraints=["p95 < 200ms"],
        required_review_checks=["confirm tests pass"],
        go_no_go_commands=["python check.py"],
        release_requirements=["changelog updated"],
    )
    text = render_policy(policy)
    for field, label in POLICY_FIELDS:
        assert label in text, f"missing label for {field}"
    for expected in (
        "a.py", "inv", "ui must not import db", "update usage.md",
        "p95 < 200ms", "confirm tests pass", "python check.py", "changelog updated",
    ):
        assert expected in text


# -------------------------------------------------------------- prompt injection


def _settings(tmp_path, roles_dir):
    from app.config.settings import Settings

    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 't.db'}",
        worktree_root=tmp_path / "wt",
        roles_dir=roles_dir,
        default_backend="fake",
    )


@pytest.fixture
def prompt_builder(context):
    return context.prompt_builder


async def test_policy_reaches_every_named_role(prompt_builder, tmp_path):
    """WP4: 'the same policy must be available consistently to' every role
    the roadmap names, checked directly against what PromptBuilder produces
    -- not just that the service can render text."""
    from app.git.workspace import run_git
    from app.models import Project, Task

    repo = tmp_path / "repo"
    repo.mkdir()
    await run_git(repo, "init", "-b", "main")
    await run_git(repo, "config", "user.email", "t@t.local")
    await run_git(repo, "config", "user.name", "t")
    (repo / "f.py").write_text("x = 1\n", encoding="utf-8")
    await run_git(repo, "add", "-A")
    await run_git(repo, "commit", "-m", "init")

    project = Project(name="p", repository_path=str(repo), default_branch="main")
    task = Task(project_id=0, title="t", description="d", status="NEW")
    policy_text = "## Protected paths\n- api/facade.py"

    for role in default_roles():
        prompt = await prompt_builder.build(
            role=role, project=project, task=task,
            workspace={"cwd": str(repo), "repo_path": str(repo), "permissions": []},
            context_worktree_path=str(repo),
            policy_text=policy_text,
        )
        assert "api/facade.py" in prompt.user, f"policy missing from {role.key} prompt"
        assert "Project Policy" in prompt.user, f"policy block unlabelled for {role.key}"


async def test_reviewer_gets_the_enforcement_instruction_others_do_not(
    prompt_builder, tmp_path,
):
    """The Engineer must not define the criteria for its own approval --
    checked as an actual asymmetry in the rendered prompts, not just asserted
    in a docstring."""
    from app.models import Project, Task

    project = Project(name="p", repository_path=str(tmp_path), default_branch="main")
    task = Task(project_id=0, title="t", description="d", status="NEW")

    engineer = next(r for r in default_roles() if r.key == "engineer")
    reviewer = next(r for r in default_roles() if r.key == "reviewer")

    eng_prompt = await prompt_builder.build(
        role=engineer, project=project, task=task,
        workspace={"cwd": str(tmp_path), "repo_path": str(tmp_path), "permissions": []},
        policy_text="## Protected paths\n- a.py",
    )
    rev_prompt = await prompt_builder.build(
        role=reviewer, project=project, task=task,
        workspace={"cwd": str(tmp_path), "repo_path": str(tmp_path), "permissions": []},
        policy_text="## Protected paths\n- a.py",
    )

    assert "enforcement point" not in eng_prompt.user
    assert "enforcement point" in rev_prompt.user


async def test_no_policy_configured_adds_nothing_to_the_prompt(prompt_builder, tmp_path):
    from app.models import Project, Task

    project = Project(name="p", repository_path=str(tmp_path), default_branch="main")
    task = Task(project_id=0, title="t", description="d", status="NEW")
    role = next(r for r in default_roles() if r.key == "architect")

    prompt = await prompt_builder.build(
        role=role, project=project, task=task,
        workspace={"cwd": str(tmp_path), "repo_path": str(tmp_path), "permissions": []},
        policy_text=None,
    )
    assert "Project Policy" not in prompt.user


async def test_huge_policy_text_is_capped_not_injected_unbounded(prompt_builder, tmp_path):
    """REGRESSION: policy_text had no size limit of its own.

    Repo-owned policy files were already bounded by _read_files
    (context_max_bytes / context_file_max_bytes); the structured policy_text a
    caller renders from the database was not. A policy with many long
    statements could otherwise inflate every role's prompt unboundedly -- the
    same class of risk _cap() already guards against for architecture_result,
    review_result and upstream context elsewhere in this module.
    """
    from app.models import Project, Task

    project = Project(name="p", repository_path=str(tmp_path), default_branch="main")
    task = Task(project_id=0, title="t", description="d", status="NEW")
    role = next(r for r in default_roles() if r.key == "architect")
    huge_policy_text = "## Architecture invariants\n" + ("x" * 50_000)

    prompt = await prompt_builder.build(
        role=role, project=project, task=task,
        workspace={"cwd": str(tmp_path), "repo_path": str(tmp_path), "permissions": []},
        policy_text=huge_policy_text,
    )

    assert len(prompt.user.encode("utf-8")) < len(huge_policy_text.encode("utf-8")), (
        "an oversized policy_text must be capped, not passed through unbounded"
    )
    assert "[truncated]" in prompt.user


async def test_triage_prompt_also_receives_policy(prompt_builder):
    """Triage does not go through build() -- it has its own prompt path
    (build_triage_prompt) -- and must not be the one role policy misses."""
    from app.models import Project, Task

    project = Project(id=1, name="p", repository_path=".", default_branch="main")
    task = Task(id=1, project_id=1, title="t", description="d", status="NEW")

    system, user = await prompt_builder.build_triage_prompt(
        task, project, policy_text="## Protected paths\n- api/facade.py",
    )
    assert "api/facade.py" in user


async def test_triage_prompt_is_now_an_instance_method(prompt_builder):
    """REGRESSION guard: build_triage_prompt was a @staticmethod called as
    PromptBuilder.build_triage_prompt(task, project) directly. Reading policy
    files needs settings-bound instance state, so it became an instance
    method -- assert the signature actually changed rather than trusting the
    call sites alone, since a caller could silently keep using it as static
    without triggering an obvious error until the policy feature is exercised.
    """
    sig = inspect.signature(PromptBuilder.build_triage_prompt)
    assert "self" in sig.parameters
    assert inspect.iscoroutinefunction(PromptBuilder.build_triage_prompt)

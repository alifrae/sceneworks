from app.models import Project, Task
from app.roles.prompts import (
    PromptBuilder,
    _changed_files_from_review,
    _format_project_policy,
)
from app.services.policy_check import (
    POLICY_VIOLATION_MARKER,
    check_protected_paths,
    enforce_review_verdict,
    render_violations,
)


def test_protected_paths_are_case_sensitive_and_path_normalized():
    violations = check_protected_paths(
        ["generated/*", "api/Public.py"],
        ["generated\\nested\\file.py", "api/public.py", "api/Public.py"],
    )
    assert [(item.path, item.pattern) for item in violations] == [
        ("api/Public.py", "api/Public.py"),
        ("generated/nested/file.py", "generated/*"),
    ]


def test_rendered_violation_forces_changes_requested():
    violations = check_protected_paths(["checks/*"], ["checks/guard.py"])
    rendered = render_violations(violations)
    assert POLICY_VIOLATION_MARKER in rendered
    assert "checks/guard.py" in rendered
    assert enforce_review_verdict("APPROVED", rendered) == "CHANGES_REQUESTED"
    assert enforce_review_verdict("CHANGES_REQUESTED", rendered) == "CHANGES_REQUESTED"
    assert enforce_review_verdict("APPROVED", "normal review") == "APPROVED"


def test_project_policy_and_task_contract_are_both_visible_to_triage():
    project = Project(
        id=1,
        name="Example",
        repository_path=".",
        engineering_policy={
            "architecture_invariants": ["UI must not import persistence"],
            "protected_paths": ["generated/*"],
        },
    )
    task = Task(
        id=2,
        project_id=1,
        title="Change parser",
        engineering_contract={
            "allowed_scope": ["backend/parser"],
            "acceptance_criteria": ["parser tests pass"],
        },
    )

    _, user = PromptBuilder.build_triage_prompt(task, project)

    assert "Project engineering policy" in user
    assert "UI must not import persistence" in user
    assert "generated/*" in user
    assert "Engineering contract" in user
    assert "backend/parser" in user
    assert "parser tests pass" in user


def test_review_changed_files_fall_back_to_git_diff_headers():
    task = Task(
        id=2,
        project_id=1,
        title="Change parser",
        changed_files=[],
    )
    extra = {
        "Diff to review (base_commit..result_commit)": (
            "diff --git a/backend/a.py b/backend/a.py\n"
            "index 111..222 100644\n"
            "--- a/backend/a.py\n"
            "+++ b/backend/a.py\n"
            "diff --git a/generated/x.py b/generated/x.py\n"
        )
    }
    assert _changed_files_from_review(task, extra) == [
        "backend/a.py",
        "generated/x.py",
    ]


def test_project_policy_formatter_omits_empty_categories():
    text = _format_project_policy(
        {
            "protected_paths": ["generated/*"],
            "architecture_invariants": [],
            "release_requirements": ["qualification must pass"],
        }
    )
    assert "## Protected paths" in text
    assert "generated/*" in text
    assert "## Release requirements" in text
    assert "qualification must pass" in text
    assert "Architecture invariants" not in text

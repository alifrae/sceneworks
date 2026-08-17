"""Project Memory V2 tests (WP2): retrieval against a real database, lifecycle.

`test_memory.py` holds the V1 suite. Every retrieval test in it queries a single
word ("decided", "API", "Big") or no word at all — which is precisely why all 23
passed while retrieval was broken for every realistic task description
(docs/wp0-baseline-audit.md, F4).

This file uses realistic multi-word task descriptions and asserts retrieval works
when the query sentence appears **nowhere** in the stored memory.
"""

from __future__ import annotations

import inspect

import pytest

from app.events import types as event_types
from app.services.memory import MemoryService


@pytest.fixture
def memory_service(context):
    return context.memory


REALISTIC_TASK = (
    "Add support for loading LAS files in the point cloud importer so that "
    "users can drag a .las file onto the viewer and have it appear in the scene."
)


async def _seed(memory_service: MemoryService, project_id: int = 1):
    """Accepted decisions and a constraint, as a real project accumulates."""
    io_decision = await memory_service.create(
        project_id=project_id, type="architecture_decision",
        title="Point cloud IO goes through the api facade",
        content=(
            "All point cloud loading and saving must go through api/facade.py. "
            "Direct use of io_api from plugins is forbidden."
        ),
        status="accepted", tags=["io", "facade", "point-cloud"],
        source="architect", source_task_id=41,
    )
    frozen_api = await memory_service.create(
        project_id=project_id, type="constraint",
        title="Public Python API is frozen",
        content=(
            "The public api/ package signatures must not change without a "
            "migration note in the release documentation."
        ),
        status="accepted", tags=["api", "compatibility"], source="cto",
    )
    unrelated = await memory_service.create(
        project_id=project_id, type="product_decision",
        title="Pricing stays per-seat for the pilot",
        content="We will not introduce usage-based billing before the pilot ends.",
        status="accepted", tags=["pricing"], source="product",
    )
    return io_decision, frozen_api, unrelated


# ------------------------------------------------------- the closure criterion


async def test_realistic_task_description_retrieves_relevant_accepted_decision(
    memory_service: MemoryService,
):
    """THE WP2 CLOSURE TEST.

    A realistic multi-word task description must retrieve the relevant accepted
    decision even though that description appears nowhere in the memory. Before
    WP2 this returned zero results while the same store returned two for an empty
    query.
    """
    io_decision, _, _ = await _seed(memory_service)

    # Precondition: the query really is absent from the stored text, so the test
    # cannot pass by substring luck.
    assert REALISTIC_TASK not in io_decision.content
    assert REALISTIC_TASK not in io_decision.title

    results = await memory_service.get_relevant(project_id=1, query=REALISTIC_TASK)

    assert results, "a realistic task description must retrieve relevant decisions"
    assert io_decision.id in [m.id for m in results]


async def test_realistic_task_description_injects_into_workflow_context(
    memory_service: MemoryService,
):
    """The exact path the workflow uses (WorkflowManager._inject_memory)."""
    io_decision, _, _ = await _seed(memory_service)

    ctx = await memory_service.injection_context(
        project_id=1, task_description=REALISTIC_TASK,
    )

    assert ctx["injected_ids"], "workflow injection retrieved nothing"
    assert io_decision.id in ctx["injected_ids"]
    # The terms retrieval searched for are reported, so an empty result is
    # diagnosable rather than mysterious.
    assert "point" in ctx["query_terms"]
    assert "add" not in ctx["query_terms"], "filler must not become a search term"


# -------------------------------------------------------------- selectivity


async def test_irrelevant_accepted_memory_is_not_injected(
    memory_service: MemoryService,
):
    """Retrieval must be selective, not "return whatever happens to be accepted"."""
    _, _, unrelated = await _seed(memory_service)

    ctx = await memory_service.injection_context(
        project_id=1, task_description=REALISTIC_TASK,
    )

    assert unrelated.id not in ctx["injected_ids"]


async def test_injected_memory_explains_why_it_was_selected(
    memory_service: MemoryService,
):
    await _seed(memory_service)

    ctx = await memory_service.injection_context(
        project_id=1, task_description=REALISTIC_TASK,
    )
    retrieval = ctx["memories"][0]["retrieval"]

    for key in ("memory_id", "score", "matched_terms", "matched_tags",
                "type", "status", "source", "coverage", "signals"):
        assert key in retrieval, f"retrieval metadata is missing {key}"
    assert retrieval["score"] > 0
    assert retrieval["matched_terms"]


async def test_injected_memory_carries_its_provenance(
    memory_service: MemoryService,
):
    """A decision without a source is not verifiable."""
    await _seed(memory_service)

    ctx = await memory_service.injection_context(
        project_id=1, task_description=REALISTIC_TASK,
    )
    entry = ctx["memories"][0]

    assert entry["source"] == "architect"
    assert entry["source_task_id"] == 41
    assert entry["created_at"]


async def test_retrieval_prefers_the_more_relevant_decision(
    memory_service: MemoryService,
):
    io_decision, frozen_api, unrelated = await _seed(memory_service)

    api_task = (
        "We are about to change the signature of api/facade.py load_scene(). "
        "Check whether that breaks our public Python API compatibility promise."
    )
    results = await memory_service.get_relevant(project_id=1, query=api_task)

    ids = [m.id for m in results]
    assert frozen_api.id in ids
    assert unrelated.id not in ids


async def test_type_filter_still_applies_to_scored_retrieval(
    memory_service: MemoryService,
):
    """The architect node filters to decisions and constraints."""
    io_decision, _, unrelated = await _seed(memory_service)

    results = await memory_service.get_relevant(
        project_id=1,
        query="point cloud loading through the facade and api compatibility",
        types=["architecture_decision", "constraint"],
    )

    ids = [m.id for m in results]
    assert io_decision.id in ids
    assert unrelated.id not in ids


async def test_search_shares_the_same_scoring(memory_service: MemoryService):
    """The UI search path must not regress to substring matching."""
    io_decision, _, _ = await _seed(memory_service)

    results = await memory_service.search(project_id=1, query=REALISTIC_TASK)

    assert io_decision.id in [m.id for m in results]


async def test_project_isolation_holds_for_scored_retrieval(
    memory_service: MemoryService,
):
    await _seed(memory_service, project_id=1)
    await _seed(memory_service, project_id=2)

    results = await memory_service.get_relevant(project_id=2, query=REALISTIC_TASK)

    assert results
    assert all(m.project_id == 2 for m in results)


async def test_empty_query_falls_back_to_recency_without_faking_a_score(
    memory_service: MemoryService,
):
    """No query means nothing to score, and the result must say exactly that."""
    await _seed(memory_service)

    matches = await memory_service.get_relevant_matches(project_id=1, query="")

    assert matches
    for _, match in matches:
        assert match.score == 0.0
        assert match.signals == {"recency_only": 1.0}
        assert match.matched_terms == ()


async def test_retrieval_respects_the_injection_item_limit(
    memory_service: MemoryService,
):
    from app.services.memory import MAX_INJECTION_ITEMS

    for index in range(MAX_INJECTION_ITEMS + 4):
        await memory_service.create(
            project_id=1, type="constraint",
            title=f"Point cloud loader rule {index}",
            content="Loaders for point cloud files must be registered lazily.",
            status="accepted",
        )

    ctx = await memory_service.injection_context(
        project_id=1, task_description=REALISTIC_TASK,
    )
    assert len(ctx["memories"]) <= MAX_INJECTION_ITEMS


# ------------------------------------------- authoritative vs speculative


async def test_only_accepted_memories_are_injected_as_authoritative(
    memory_service: MemoryService,
):
    accepted = await memory_service.create(
        project_id=1, type="architecture_decision",
        title="Loaders register through the plugin registry",
        content="Every loader must register itself with the plugin registry.",
        status="accepted",
    )
    proposed = await memory_service.create(
        project_id=1, type="architecture_decision",
        title="Loader discovery by directory scan",
        content="We might discover loaders by scanning the loader directory.",
        status="proposed",
    )

    ctx = await memory_service.injection_context(
        project_id=1, task_description="How should a new loader be registered?",
    )

    assert accepted.id in ctx["injected_ids"]
    assert proposed.id not in ctx["injected_ids"]


async def test_proposed_memories_are_returned_separately_not_silently_mixed(
    memory_service: MemoryService,
):
    """A proposal may be displayed, but must not reach the authoritative block."""
    proposed = await memory_service.create(
        project_id=1, type="architecture_decision",
        title="Loader discovery by directory scan",
        content="We might discover loaders by scanning the loader directory.",
        status="proposed", source="architect",
    )

    ctx = await memory_service.injection_context(
        project_id=1, task_description="How should a new loader be registered?",
    )

    assert ctx["memories"] == []
    assert proposed.id in [item["id"] for item in ctx["proposed"]]
    assert proposed.id not in ctx["injected_ids"]


async def test_proposed_can_be_excluded_entirely(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="architecture_decision", title="Loader discovery",
        content="loader directory scan", status="proposed",
    )

    ctx = await memory_service.injection_context(
        project_id=1, task_description="loader registration",
        include_proposed=False,
    )
    assert ctx["proposed"] == []


async def test_rejected_and_archived_memories_are_never_injected(
    memory_service: MemoryService,
):
    rejected = await memory_service.create(
        project_id=1, type="constraint", title="Loader constraint",
        content="Loaders must be lazy.", status="rejected",
    )
    archived = await memory_service.create(
        project_id=1, type="constraint", title="Old loader constraint",
        content="Loaders must be eager.", status="archived",
    )
    superseded = await memory_service.create(
        project_id=1, type="constraint", title="Older loader constraint",
        content="Loaders must be sequential.", status="superseded",
    )

    ctx = await memory_service.injection_context(
        project_id=1, task_description="loader laziness rules",
    )

    for mem in (rejected, archived, superseded):
        assert mem.id not in ctx["injected_ids"]


# ------------------------------------------------------------------ lifecycle


async def test_agent_output_can_only_enter_as_a_proposal(
    memory_service: MemoryService,
):
    """`propose_from_execution` has no status parameter, by design.

    An agent's conclusion is speculative. There must be no argument a caller can
    pass to make it authoritative — only `accept()` can do that.
    """
    signature = inspect.signature(memory_service.propose_from_execution)
    assert "status" not in signature.parameters

    mem = await memory_service.propose_from_execution(
        project_id=1, type="architecture_decision",
        title="Extracted decision",
        content="The architect concluded X.",
        source_role="architect", source_task_id=7, source_execution_id="exec-7",
    )

    assert mem.status == "proposed"
    assert mem.source == "architect"
    assert mem.source_task_id == 7
    assert mem.source_execution_id == "exec-7"


async def test_accept_promotes_a_proposal_to_authoritative(
    memory_service: MemoryService,
):
    mem = await memory_service.propose_from_execution(
        project_id=1, type="architecture_decision",
        title="Loaders register through the plugin registry",
        content="Every loader registers with the plugin registry.",
        source_role="architect",
    )
    query = "How should a new loader be registered in the plugin registry?"

    before = await memory_service.injection_context(project_id=1, task_description=query)
    assert mem.id not in before["injected_ids"]

    accepted = await memory_service.accept(mem.id)
    assert accepted.status == "accepted"

    after = await memory_service.injection_context(project_id=1, task_description=query)
    assert mem.id in after["injected_ids"]


async def test_reject_keeps_the_record_and_its_provenance(
    memory_service: MemoryService,
):
    mem = await memory_service.propose_from_execution(
        project_id=1, type="architecture_decision", title="Speculative idea",
        content="Maybe we should rewrite the loader.",
        source_role="architect", source_task_id=9,
    )

    rejected = await memory_service.reject(mem.id, reason="not now")
    assert rejected.status == "rejected"

    # A rejected proposal is not deleted: its provenance survives for the record.
    still_there = await memory_service.get(mem.id)
    assert still_there is not None
    assert still_there.source == "architect"
    assert still_there.source_task_id == 9


async def test_accept_and_reject_are_recorded_as_events(
    memory_service: MemoryService, context,
):
    """Review provenance — who decided what, and from which state — must survive."""
    accepted = await memory_service.propose_from_execution(
        project_id=1, type="constraint", title="A", content="a",
        source_role="architect", source_task_id=5,
    )
    declined = await memory_service.propose_from_execution(
        project_id=1, type="constraint", title="B", content="b",
        source_role="architect", source_task_id=5,
    )
    await memory_service.accept(accepted.id, actor="founder")
    await memory_service.reject(declined.id, actor="founder", reason="wrong")

    events = await context.event_store.list_for_task(5, limit=100)
    by_type = {e.type: e for e in events}

    assert event_types.MEMORY_ACCEPTED in by_type
    assert by_type[event_types.MEMORY_ACCEPTED].payload["actor"] == "founder"
    assert by_type[event_types.MEMORY_ACCEPTED].payload["from"] == "proposed"
    assert event_types.MEMORY_REJECTED in by_type
    assert by_type[event_types.MEMORY_REJECTED].payload["reason"] == "wrong"


async def test_memory_events_are_attributed_to_their_source_execution(
    memory_service: MemoryService, context,
):
    """Provenance links the memory back to the run that produced it."""
    mem = await memory_service.propose_from_execution(
        project_id=1, type="constraint", title="C", content="c",
        source_role="architect", source_task_id=11, source_execution_id="exec-11",
    )
    await memory_service.accept(mem.id)

    events = await context.event_store.list_for_execution("exec-11", limit=100)
    assert {e.type for e in events} >= {
        event_types.MEMORY_CREATED, event_types.MEMORY_ACCEPTED,
    }


async def test_rejected_is_a_valid_status_and_bad_ones_still_raise(
    memory_service: MemoryService,
):
    mem = await memory_service.create(
        project_id=1, type="constraint", title="T", content="c", status="rejected",
    )
    assert mem.status == "rejected"

    with pytest.raises(ValueError):
        await memory_service.create(
            project_id=1, type="constraint", title="T", content="c",
            status="not-a-status",
        )


async def test_supersede_records_the_replacement(memory_service: MemoryService):
    old = await memory_service.create(
        project_id=1, type="constraint", title="Old rule",
        content="Loaders must be eager.", status="accepted",
    )
    new = await memory_service.create(
        project_id=1, type="constraint", title="New rule",
        content="Loaders must be lazy.", status="accepted",
    )

    superseded, replacement = await memory_service.supersede(old.id, new.id)

    assert superseded.status == "superseded"
    assert replacement.supersedes_id == old.id

    ctx = await memory_service.injection_context(
        project_id=1, task_description="Should loaders be lazy or eager?",
    )
    assert new.id in ctx["injected_ids"]
    assert old.id not in ctx["injected_ids"]

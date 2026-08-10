"""Project Memory V1 tests.

Tests cover: CRUD, project isolation, provenance, archive/supersession,
retrieval, search/filter, workflow injection, bounded context.
"""

from __future__ import annotations

import pytest

from app.models import ProjectMemory
from app.services.memory import MemoryService, MAX_INJECTION_BYTES, MAX_INJECTION_ITEMS


@pytest.fixture
def memory_service(context):
    return context.memory


# ---------------------------------------------------------------- CRUD tests


async def test_create_memory(memory_service: MemoryService):
    mem = await memory_service.create(
        project_id=1,
        type="architecture_decision",
        title="Use SQLite",
        content="We chose SQLite for its simplicity and portability.",
        status="accepted",
        tags=["db", "architecture"],
        source="architect",
        source_task_id=1,
    )
    assert mem.id
    assert mem.project_id == 1
    assert mem.type == "architecture_decision"
    assert mem.title == "Use SQLite"
    assert mem.status == "accepted"
    assert "db" in mem.tags
    assert mem.source == "architect"
    assert mem.source_task_id == 1


async def test_create_memory_invalid_type_raises(memory_service: MemoryService):
    with pytest.raises(ValueError, match="invalid memory type"):
        await memory_service.create(
            project_id=1,
            type="invalid_type",
            title="Bad",
            content="test",
        )


async def test_create_memory_invalid_status_raises(memory_service: MemoryService):
    with pytest.raises(ValueError, match="invalid memory status"):
        await memory_service.create(
            project_id=1,
            type="constraint",
            title="Bad",
            content="test",
            status="deleted",
        )


async def test_get_memory(memory_service: MemoryService):
    created = await memory_service.create(
        project_id=1, type="constraint", title="Mem limit",
        content="Memory must stay under 20KB.",
    )
    fetched = await memory_service.get(created.id)
    assert fetched is not None
    assert fetched.title == "Mem limit"


async def test_get_missing_memory(memory_service: MemoryService):
    assert await memory_service.get(99999) is None


async def test_update_memory(memory_service: MemoryService):
    created = await memory_service.create(
        project_id=1, type="product_decision", title="Old",
        content="Original content.",
    )
    updated = await memory_service.update(created.id, title="New title", status="accepted")
    assert updated is not None
    assert updated.title == "New title"
    assert updated.status == "accepted"


async def test_update_nonexistent_memory(memory_service: MemoryService):
    result = await memory_service.update(99999, title="Never")
    assert result is None


# ---------------------------------------------------------------- archive/supersede


async def test_archive_memory(memory_service: MemoryService):
    mem = await memory_service.create(
        project_id=1, type="technology_decision", title="Old tech",
        content="Use old approach.", status="accepted",
    )
    archived = await memory_service.archive(mem.id)
    assert archived is not None
    assert archived.status == "archived"


async def test_archive_nonexistent_memory(memory_service: MemoryService):
    assert await memory_service.archive(99999) is None


async def test_supersede_memory(memory_service: MemoryService):
    old = await memory_service.create(
        project_id=1, type="technology_decision", title="V1",
        content="First version.", status="accepted",
    )
    replacement = await memory_service.create(
        project_id=1, type="technology_decision", title="V2",
        content="Second version.", status="accepted",
    )
    superseded, repl = await memory_service.supersede(old.id, replacement.id)
    assert superseded is not None
    assert superseded.status == "superseded"
    assert repl.supersedes_id == old.id


async def test_supersede_nonexistent(memory_service: MemoryService):
    old, repl = await memory_service.supersede(99999, 88888)
    assert old is None
    assert repl is None


# ---------------------------------------------------------------- search/filter


async def test_search_by_query(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="constraint", title="Max users",
        content="System supports up to 1000 concurrent users.",
        status="accepted",
    )
    await memory_service.create(
        project_id=2, type="constraint", title="Other project",
        content="Different data.", status="accepted",
    )

    results = await memory_service.search(project_id=1, query="concurrent")
    assert len(results) == 1
    assert results[0].title == "Max users"


async def test_search_by_type(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="constraint", title="C1", content="x",
    )
    await memory_service.create(
        project_id=1, type="architecture_decision", title="A1", content="y",
    )

    results = await memory_service.search(project_id=1, types=["constraint"])
    assert len(results) == 1
    assert results[0].title == "C1"


async def test_search_by_status(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="constraint", title="Accepted", content="x",
        status="accepted",
    )
    await memory_service.create(
        project_id=1, type="constraint", title="Proposed", content="y",
        status="proposed",
    )

    results = await memory_service.search(project_id=1, status="accepted")
    assert len(results) == 1
    assert results[0].title == "Accepted"


async def test_search_by_tags(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="constraint", title="Tagged",
        content="x", tags=["security"],
    )
    await memory_service.create(
        project_id=1, type="constraint", title="Untagged",
        content="y", tags=["performance"],
    )

    results = await memory_service.search(project_id=1, tags=["security"])
    assert len(results) >= 1
    assert any(r.title == "Tagged" for r in results)


# ---------------------------------------------------------------- project isolation


async def test_project_isolation(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="constraint", title="P1 item", content="a",
    )
    await memory_service.create(
        project_id=2, type="constraint", title="P2 item", content="b",
    )

    p1 = await memory_service.search(project_id=1)
    assert all(m.project_id == 1 for m in p1)

    p2 = await memory_service.search(project_id=2)
    assert all(m.project_id == 2 for m in p2)


# ---------------------------------------------------------------- get_relevant


async def test_get_relevant_only_accepted(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="architecture_decision", title="Accepted decision",
        content="We decided X.", status="accepted",
    )
    await memory_service.create(
        project_id=1, type="architecture_decision", title="Proposed decision",
        content="We might decide Y.", status="proposed",
    )

    results = await memory_service.get_relevant(project_id=1, query="decided")
    assert len(results) == 1
    assert results[0].title == "Accepted decision"


async def test_get_relevant_respects_limit(memory_service: MemoryService):
    for i in range(MAX_INJECTION_ITEMS + 2):
        await memory_service.create(
            project_id=1, type="constraint", title=f"Item {i}",
            content=f"Content {i}", status="accepted",
        )

    results = await memory_service.get_relevant(project_id=1)
    assert len(results) <= MAX_INJECTION_ITEMS


async def test_get_relevant_by_types(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="constraint", title="Constraint", content="x",
        status="accepted",
    )
    await memory_service.create(
        project_id=1, type="architecture_decision", title="Decision", content="y",
        status="accepted",
    )

    results = await memory_service.get_relevant(
        project_id=1, types=["architecture_decision"],
    )
    assert len(results) == 1
    assert results[0].title == "Decision"


# ---------------------------------------------------------------- injection context


async def test_injection_context_structure(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="architecture_decision", title="Adopt FastAPI",
        content="We chose FastAPI.", status="accepted",
    )

    ctx = await memory_service.injection_context(project_id=1, task_description="API")

    assert "memories" in ctx
    assert "total_bytes" in ctx
    assert "injected_ids" in ctx
    assert "truncated" in ctx
    assert isinstance(ctx["memories"], list)


async def test_injection_context_truncation(memory_service: MemoryService):
    big_content = "X" * (MAX_INJECTION_BYTES + 1000)
    await memory_service.create(
        project_id=1, type="constraint", title="Big",
        content=big_content, status="accepted",
    )

    ctx = await memory_service.injection_context(project_id=1, task_description="Big")
    assert ctx["truncated"] is True or len(ctx["memories"]) == 0


# ---------------------------------------------------------------- list_project_memories


async def test_list_project_memories(memory_service: MemoryService):
    await memory_service.create(
        project_id=1, type="constraint", title="Item 1", content="a",
    )
    await memory_service.create(
        project_id=1, type="constraint", title="Item 2", content="b",
    )

    items = await memory_service.list_project_memories(project_id=1)
    assert len(items) >= 2


# ---------------------------------------------------------------- provenance


async def test_provenance_fields(memory_service: MemoryService):
    mem = await memory_service.create(
        project_id=1, type="initiative_summary", title="Summary",
        content="Task 42 results.", source="engineer", source_task_id=42,
        source_execution_id="exec-001",
    )
    assert mem.source == "engineer"
    assert mem.source_task_id == 42
    assert mem.source_execution_id == "exec-001"

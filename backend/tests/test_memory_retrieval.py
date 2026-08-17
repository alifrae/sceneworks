"""Deterministic memory retrieval tests (WP2).

The regression these guard against is precise. V2.4 turned an entire task
description into one SQL pattern:

    WHERE title ILIKE '%Add support for loading LAS files in the point cloud
                        importer so that users can drag a .las file...%'

No stored memory contains a task description verbatim, so realistic descriptions
retrieved nothing while the store held directly relevant accepted decisions
(docs/wp0-baseline-audit.md, F4). The 23 existing memory tests could not catch it
because every one of them queried a single word — "decided", "API", "Big" — or
no word at all.

So these tests use **realistic multi-word task descriptions** throughout, and the
central one asserts retrieval succeeds *when the query sentence appears nowhere
in the memory*.

Scoring is a pure function, so most of this needs no database.
"""

from __future__ import annotations

import pytest

from app.services.memory_retrieval import (
    Candidate,
    MIN_TOKEN_LENGTH,
    STOPWORDS,
    W_PHRASE,
    W_TAG_TERM,
    W_TITLE_TERM,
    rank,
    score_candidate,
    tokenize,
    tokenize_tag,
)

# --------------------------------------------------------------- tokenisation


def test_tokenize_splits_identifiers_and_paths():
    """Engineering text is full of paths and snake_case; both must decompose."""
    assert tokenize("calc/core.py") == ["calc", "core"]
    assert tokenize("api_facade") == ["api", "facade"]
    assert tokenize("PointCloud-Loader") == ["pointcloud", "loader"]


def test_tokenize_drops_stopwords_and_short_tokens():
    tokens = tokenize("Add support for the new loader so that it works")
    assert "add" not in tokens
    assert "support" not in tokens
    assert "the" not in tokens
    assert "it" not in tokens  # shorter than MIN_TOKEN_LENGTH
    assert "loader" in tokens


def test_tokenize_deduplicates_but_preserves_order():
    assert tokenize("loader cloud loader point cloud") == ["loader", "cloud", "point"]


def test_tokenize_of_empty_or_stopword_only_text_is_empty():
    assert tokenize("") == []
    assert tokenize("   ") == []
    assert tokenize("the and of to") == []


def test_min_token_length_and_stopwords_are_sane():
    """Guard against a future edit that quietly discards real domain terms."""
    assert MIN_TOKEN_LENGTH <= 3
    for term in ("api", "las", "cloud", "loader", "facade", "commit", "worktree"):
        assert term not in STOPWORDS, f"{term!r} carries retrieval signal"


def test_tokenize_tag_matches_by_parts():
    assert tokenize_tag("point-cloud") == {"point", "cloud"}
    assert tokenize_tag("io") == set()  # too short to be a term


# ------------------------------------------------------------------- scoring


IO_DECISION = Candidate(
    id=1,
    type="architecture_decision",
    title="Point cloud IO goes through the api facade",
    content=(
        "Accepted decision: all point cloud loading and saving must go through "
        "api/facade.py. Direct use of io_api from plugins is forbidden."
    ),
    tags=("io", "facade", "point-cloud"),
)

FROZEN_API = Candidate(
    id=2,
    type="constraint",
    title="Public Python API is frozen",
    content=(
        "The public api/ package signatures must not change without a migration "
        "note in the release documentation."
    ),
    tags=("api", "compatibility"),
)

UNRELATED = Candidate(
    id=3,
    type="product_decision",
    title="Pricing stays per-seat for the pilot",
    content="We will not introduce usage-based billing before the pilot ends.",
    tags=("pricing",),
)


REALISTIC_TASK = (
    "Add support for loading LAS files in the point cloud importer so that "
    "users can drag a .las file onto the viewer and have it appear in the scene."
)


def test_realistic_task_description_matches_a_relevant_decision():
    """THE WP2 closure test, at the scoring layer.

    The query sentence appears nowhere in the memory. Term matching must still
    find it — this is exactly what the substring implementation could not do.
    """
    assert REALISTIC_TASK not in IO_DECISION.content
    assert REALISTIC_TASK not in IO_DECISION.title

    match = score_candidate(IO_DECISION, REALISTIC_TASK)

    assert match is not None
    assert match.score > 0
    assert "point" in match.matched_terms
    assert "cloud" in match.matched_terms
    assert "point-cloud" in match.matched_tags


def test_unrelated_memory_does_not_match_and_is_not_a_zero_score():
    """Returning None, not 0.0, keeps irrelevant items out of injection."""
    assert score_candidate(UNRELATED, REALISTIC_TASK) is None


def test_title_match_outweighs_content_match():
    in_title = Candidate(id=1, type="constraint", title="loader rules", content="x")
    in_content = Candidate(id=2, type="constraint", title="x", content="loader rules")

    title_score = score_candidate(in_title, "loader").score
    content_score = score_candidate(in_content, "loader").score
    assert title_score > content_score


def test_tag_match_contributes_its_own_signal():
    tagged = Candidate(
        id=1, type="constraint", title="unrelated", content="unrelated",
        tags=("loader",),
    )
    match = score_candidate(tagged, "loader")

    assert match.matched_tags == ("loader",)
    assert match.signals["tags"] == pytest.approx(W_TAG_TERM)


def test_exact_phrase_is_scored_decisively():
    phrase = "commit pinned worktree"
    candidate = Candidate(
        id=1, type="constraint", title="Rule",
        content=f"Every role runs in a {phrase} created from the base commit.",
    )
    match = score_candidate(candidate, phrase)

    assert match.signals["phrase"] == pytest.approx(W_PHRASE)
    # And still records the individual terms, so the score is explainable.
    assert set(match.matched_terms) >= {"commit", "pinned", "worktree"}


def test_content_match_is_substring_so_plurals_and_compounds_hit():
    candidate = Candidate(
        id=1, type="constraint", title="x",
        content="The point_cloud loaders are registered lazily.",
    )
    match = score_candidate(candidate, "cloud loader")

    assert "cloud" in match.matched_terms   # inside point_cloud
    assert "loader" in match.matched_terms  # inside loaders


def test_coverage_reports_the_fraction_of_query_terms_matched():
    match = score_candidate(IO_DECISION, "point cloud pricing")
    # "point" and "cloud" match, "pricing" does not.
    assert match.coverage == pytest.approx(2 / 3)


def test_match_explains_itself():
    """A caller must be able to see why an item was selected."""
    payload = score_candidate(IO_DECISION, REALISTIC_TASK).as_dict()

    for key in (
        "memory_id", "score", "matched_terms", "matched_tags",
        "type", "status", "source", "coverage", "signals",
    ):
        assert key in payload, f"retrieval metadata is missing {key}"
    assert payload["memory_id"] == 1


def test_empty_query_matches_nothing_rather_than_everything():
    assert score_candidate(IO_DECISION, "") is None
    assert score_candidate(IO_DECISION, "the and of") is None


# ------------------------------------------------------------------- ranking


def test_rank_orders_by_relevance_and_excludes_non_matches():
    matches = rank([UNRELATED, FROZEN_API, IO_DECISION], REALISTIC_TASK)

    assert [m.memory_id for m in matches] == [IO_DECISION.id]


def test_rank_puts_the_more_relevant_memory_first():
    query = "public api signatures must not change"
    matches = rank([IO_DECISION, FROZEN_API], query)

    assert matches[0].memory_id == FROZEN_API.id


def test_rank_respects_limit():
    candidates = [
        Candidate(id=i, type="constraint", title=f"loader {i}", content="loader")
        for i in range(10)
    ]
    assert len(rank(candidates, "loader", limit=3)) == 3


def test_rank_is_deterministic_for_indistinguishable_candidates():
    """The same store and query must always inject the same context.

    An injected memory changes what an agent does, so a nondeterministic order
    would make runs irreproducible.
    """
    candidates = [
        Candidate(id=i, type="constraint", title="loader", content="loader")
        for i in (3, 1, 2)
    ]
    first = [m.memory_id for m in rank(candidates, "loader")]
    for _ in range(5):
        assert [m.memory_id for m in rank(candidates, "loader")] == first


def test_rank_breaks_score_ties_by_recency():
    older = Candidate(id=1, type="constraint", title="loader", content="x", recency=1.0)
    newer = Candidate(id=2, type="constraint", title="loader", content="x", recency=9.0)

    matches = rank([older, newer], "loader")
    assert matches[0].memory_id == newer.id
    assert matches[0].score == pytest.approx(matches[1].score)


def test_title_weight_is_actually_applied():
    match = score_candidate(
        Candidate(id=1, type="constraint", title="loader", content="unrelated"),
        "loader",
    )
    assert match.signals["title_terms"] == pytest.approx(W_TITLE_TERM)

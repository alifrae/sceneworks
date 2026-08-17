"""Deterministic memory retrieval: tokenisation, matching and scoring.

Pure functions over plain data — no database, no I/O, no model. That is
deliberate: retrieval must be reproducible and explainable, so it is testable in
isolation and every result can say *why* it was selected.

Why this exists
---------------
The V2.4 implementation turned the whole task description into a single SQL
pattern:

    query = "Add support for loading LAS files in the point cloud importer ..."
    WHERE title ILIKE '%<that entire sentence>%' OR content ILIKE '%...%'

No stored memory contains a task description verbatim, so realistic descriptions
retrieved **nothing**. Measured against a store holding two directly relevant
accepted decisions: 0 results for a realistic description, 2 for an empty query.
Project Memory was inert on the only path that mattered
(docs/wp0-baseline-audit.md, F4).

The fix is to match on *terms*, not on the sentence, and to score the matches
deterministically.

No embeddings, no vector database, no knowledge graph. Those are explicitly out
of scope until deterministic retrieval demonstrably falls short.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Words carrying no retrieval signal. Kept small and English-only on purpose:
#: an aggressive stopword list silently discards real domain terms. Includes the
#: filler that dominates engineering task descriptions ("add", "support",
#: "should", "need") because those match almost every memory and only add noise.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "get", "had", "has",
    "have", "how", "if", "in", "into", "is", "it", "its", "make", "makes",
    "may", "might", "modify", "must", "no", "not", "of", "on", "onto", "or", "our",
    "out", "over", "should", "so", "some", "such", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "those", "to",
    "up", "use", "used", "using", "was", "we", "were", "what", "when",
    "where", "which", "while", "who", "why", "will", "with", "would", "you",
    "your",
    # Task-description filler: words that appear in nearly every request *and*
    # nearly every memory, so they discriminate nothing.
    "add", "added", "adding", "also", "already", "based", "case", "cases",
    "current", "currently", "ensure", "existing", "fix", "implement", "instead",
    "like", "need", "needs", "only", "please", "possible", "provide", "same",
    "support", "take", "want",
})

#: Tokens shorter than this carry too little signal ("a", "id", "io"). Two-letter
#: domain terms do exist, so the bar is deliberately low.
MIN_TOKEN_LENGTH = 3

#: Scoring weights. Additive and explainable: a caller can reconstruct a score
#: from the reported matched terms and tags. Tuned by intent, not fitted:
#: a title term is worth more than a body term, an explicit tag is worth more
#: than a body term, and an exact phrase is decisive.
W_TITLE_TERM = 3.0
W_CONTENT_TERM = 1.0
W_TAG_TERM = 2.5
W_PHRASE = 6.0

_SPLIT = re.compile(r"[^0-9a-z]+")


def normalise(text: str) -> str:
    return (text or "").strip().lower()


def tokenize(text: str) -> list[str]:
    """Extract scoring terms from free text, order preserved, deduplicated.

    Splits on anything that is not a lowercase letter or digit, so
    ``calc/core.py``, ``api_facade`` and ``PointCloud-Loader`` all decompose into
    their parts. Compound identifiers additionally keep nothing extra: the parts
    are what a memory's prose is likely to contain.
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in _SPLIT.split(normalise(text)):
        if len(raw) < MIN_TOKEN_LENGTH:
            continue
        if raw in STOPWORDS:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        tokens.append(raw)
    return tokens


def tokenize_tag(tag: str) -> set[str]:
    """Tags are matched by their parts, so ``point-cloud`` matches ``cloud``."""
    return {
        part
        for part in _SPLIT.split(normalise(tag))
        if len(part) >= MIN_TOKEN_LENGTH
    }


@dataclass(frozen=True)
class Candidate:
    """The subset of a memory row that retrieval needs.

    A plain value object rather than the ORM model, so scoring stays free of the
    database and can be unit tested without one.
    """

    id: int
    type: str
    title: str
    content: str
    tags: tuple[str, ...] = ()
    status: str = "accepted"
    source: str | None = None
    #: Higher sorts first among equal scores. Callers pass a recency ordinal.
    recency: float = 0.0


@dataclass
class MemoryMatch:
    """A scored candidate, carrying the reason it was selected."""

    memory_id: int
    score: float
    matched_terms: tuple[str, ...]
    matched_tags: tuple[str, ...]
    type: str
    status: str
    source: str | None
    #: Per-signal contributions, so a score is never a bare number.
    signals: dict[str, float] = field(default_factory=dict)
    #: Fraction of query terms this memory matched (0.0-1.0).
    coverage: float = 0.0

    def as_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "score": round(self.score, 3),
            "matched_terms": list(self.matched_terms),
            "matched_tags": list(self.matched_tags),
            "type": self.type,
            "status": self.status,
            "source": self.source,
            "coverage": round(self.coverage, 3),
            "signals": {k: round(v, 3) for k, v in self.signals.items()},
        }


def score_candidate(candidate: Candidate, query: str) -> MemoryMatch | None:
    """Score one candidate against a query. Returns None when nothing matched.

    Returning None rather than a zero score matters: a memory that matched no
    term must not be injected as relevant project context just because the store
    happened to be small.
    """
    terms = tokenize(query)
    if not terms:
        return None

    title_tokens = set(tokenize(candidate.title))
    content_normalised = normalise(candidate.content)
    title_normalised = normalise(candidate.title)
    tag_tokens: dict[str, set[str]] = {
        tag: tokenize_tag(tag) for tag in candidate.tags
    }

    matched_terms: list[str] = []
    matched_tags: list[str] = []
    signals: dict[str, float] = {}
    score = 0.0

    for term in terms:
        hit = False
        if term in title_tokens:
            score += W_TITLE_TERM
            signals["title_terms"] = signals.get("title_terms", 0.0) + W_TITLE_TERM
            hit = True
        elif term in content_normalised:
            # Substring rather than token match, so "loader" finds "loaders" and
            # "cloud" finds "point_cloud" inside prose.
            score += W_CONTENT_TERM
            signals["content_terms"] = signals.get("content_terms", 0.0) + W_CONTENT_TERM
            hit = True

        for tag, parts in tag_tokens.items():
            if term in parts:
                score += W_TAG_TERM
                signals["tags"] = signals.get("tags", 0.0) + W_TAG_TERM
                if tag not in matched_tags:
                    matched_tags.append(tag)
                hit = True

        if hit:
            matched_terms.append(term)

    # An exact phrase is far stronger evidence than scattered terms, and is what
    # the old substring implementation was reaching for. Keep it as a bonus on
    # top of term matching rather than as the only mechanism.
    phrase = normalise(query)
    if phrase and (phrase in title_normalised or phrase in content_normalised):
        score += W_PHRASE
        signals["phrase"] = W_PHRASE

    if not matched_terms and "phrase" not in signals:
        return None

    return MemoryMatch(
        memory_id=candidate.id,
        score=score,
        matched_terms=tuple(matched_terms),
        matched_tags=tuple(matched_tags),
        type=candidate.type,
        status=candidate.status,
        source=candidate.source,
        signals=signals,
        coverage=len(matched_terms) / len(terms),
    )


def rank(
    candidates: list[Candidate], query: str, limit: int | None = None,
) -> list[MemoryMatch]:
    """Score and order candidates. Deterministic, including on ties.

    Order: score, then coverage, then recency, then id. The final key makes the
    result stable for candidates that are otherwise indistinguishable, so the
    same store and query always inject the same context — which matters when an
    injected memory changes what an agent does.
    """
    by_id = {c.id: c for c in candidates}
    matches = [m for m in (score_candidate(c, query) for c in candidates) if m]
    matches.sort(
        key=lambda m: (
            -m.score,
            -m.coverage,
            -by_id[m.memory_id].recency,
            m.memory_id,
        )
    )
    return matches[:limit] if limit is not None else matches

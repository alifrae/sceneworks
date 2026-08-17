# Project Memory

Persistent project knowledge — accepted architecture decisions, technology
choices, product decisions, initiative summaries and constraints — retrieved
deterministically and injected into workflow nodes as bounded, authoritative
context.

Status: **implemented, unit tested, integration tested, and covered by a
qualification scenario** (`memory-injection`). Not live-model validated.

No embeddings, no vector database, no knowledge graph, no RAG platform. None are
needed until deterministic retrieval demonstrably falls short, and it currently
does not.

## The V2.4 defect this replaced

V2.4 built retrieval as a single SQL pattern from the whole query:

```python
q = f"%{query.strip()}%"          # query == the entire task description
stmt.where(or_(title.ilike(q), content.ilike(q)))
```

`_inject_memory` passed `task.description or task.title`. No stored memory
contains a task description verbatim, so in production this matched nothing.
Measured against a store holding two directly relevant accepted decisions:

```text
realistic task description  -> 0 results
"point cloud"               -> 1 result
""  (no filter at all)      -> 2 results
```

Project Memory was inert on the only path that mattered, and none of the 23
memory tests could catch it because every retrieval test queried a single word
(`"decided"`, `"API"`, `"Big"`) or no word at all. Recorded as
[wp0-baseline-audit.md](wp0-baseline-audit.md) F4.

## Retrieval

Two stages, both deterministic.

**1. SQL prefilter** (`MemoryService._fetch_candidates`) narrows to memories
matching *at least one term* — an `OR` over per-term `ILIKE` across title,
content and the tags column. This is the actual fix: terms, not the sentence.
Filters by project, status and type, ordered most-recently-updated first, bounded
by `CANDIDATE_LIMIT` (200).

**2. Python scoring** (`memory_retrieval.py`) — pure functions, no database, so
scoring is unit testable and reproducible.

### Tokenisation

Lowercased, split on anything not a letter or digit, so `calc/core.py`,
`api_facade` and `PointCloud-Loader` decompose into their parts. Terms shorter
than 3 characters are dropped, as are stopwords — including the filler that
dominates task descriptions (`add`, `support`, `implement`, `need`, `should`),
because those match almost every memory and discriminate nothing.

### Scoring

Additive and explainable: a caller can reconstruct the score from the reported
matches.

| Signal | Weight | Notes |
| --- | --- | --- |
| term in title | 3.0 | token match |
| term in content | 1.0 | substring, so `loader` finds `loaders` and `cloud` finds `point_cloud` |
| term matches a tag | 2.5 | tags match by their parts, so `point-cloud` matches `cloud` |
| whole query appears verbatim | 6.0 | what the old implementation was reaching for, kept as a bonus rather than as the only mechanism |

A candidate matching nothing returns `None`, not a zero score — an irrelevant
memory must not be injected just because the store is small.

Ordering is `score`, then `coverage`, then recency, then id. The final key makes
results stable for otherwise indistinguishable candidates, because an injected
memory changes what an agent does and irreproducible context would make runs
irreproducible.

### Every result explains itself

```json
{
  "memory_id": 1,
  "score": 11.5,
  "matched_terms": ["point", "cloud", "loading", "facade"],
  "matched_tags": ["point-cloud", "facade"],
  "type": "architecture_decision",
  "status": "accepted",
  "source": "architect",
  "coverage": 0.4,
  "signals": {"title_terms": 6.0, "content_terms": 2.0, "tags": 5.0}
}
```

With an empty query there is nothing to score, so the most recently updated
memories are returned carrying `signals: {"recency_only": 1.0}` and a score of
`0.0` — never a fabricated relevance figure.

## Authoritative vs speculative

Only **accepted** memories are injected as authoritative project context
(`AUTHORITATIVE_STATUSES = {"accepted"}`).

`injection_context()` returns them under `memories`. Matching **proposals** come
back under a separate `proposed` key, for display only. The separation is
structural rather than a flag on each item: the prompt builder concatenates
`memories`, and `proposed` is simply not reachable from there, so speculation
cannot be spliced into the authoritative block by accident.

`propose_from_execution()` — the entry point for anything an agent produced —
**has no `status` parameter**. There is no argument a caller can pass to make
agent output authoritative. Only `accept()` can, and that is an explicit human
action.

The prompt block states the standing plainly:

```text
## Accepted project decisions and constraints

These were accepted by the human founder and are authoritative. Follow them.
If one appears wrong for this task, say so rather than silently departing from it.

### [architecture_decision] Point cloud IO goes through the api facade
_(source: architect; from task 41; recorded 2026-08-17T...)_
_tags: io, facade, point-cloud_

All point cloud loading and saving must go through api/facade.py. ...
```

Retrieval scores are deliberately **not** in the prompt: an operator needs to know
why an item was selected, but feeding scores and matched terms to the agent is
noise competing with the decision text. They go into the `memory.injected` event
instead, alongside the ids of proposals that matched and were withheld.

## Lifecycle

```text
task / execution / result
    -> candidate decision, constraint or initiative knowledge
    -> proposed              (anything an agent produced)
    -> human review
    -> accepted | rejected | superseded | archived
```

| Status | Injected? | Meaning |
| --- | --- | --- |
| `proposed` | no | awaiting human review; displayed separately |
| `accepted` | **yes** | authoritative project truth |
| `rejected` | no | declined by a human; kept on record with its provenance |
| `superseded` | no | replaced by a newer memory (`supersedes_id` links them) |
| `archived` | no | retired |

`rejected` is new in V3: a human declining a proposal needs an outcome distinct
from `archived` (retired after being accepted) and from `proposed` (still
awaiting review).

## Provenance

Each memory records `source` (the authoring role, or `manual`),
`source_task_id`, `source_execution_id`, `supersedes_id`, `tags`, and
created/updated timestamps. Review provenance — who accepted or rejected what,
from which previous status, and why — is recorded in the event log as
`memory.accepted` / `memory.rejected`, attributed to the source task and
execution so a decision links back to the run that produced it.

**Not yet captured: the relevant commit.** A memory cannot currently record the
commit its decision was made against. That needs a schema column, and schema
changes are unsafe until migrations exist (WP3) — so it is deferred to WP3 for the
column and WP6 for wiring it into Git provenance. This is a known gap, not an
oversight.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/projects/{id}/memory` | create (defaults to `proposed`) |
| `GET /api/projects/{id}/memory` | list/search — scored when `query` is given |
| `GET /api/projects/{id}/memory/relevant` | preview exactly what the workflow would inject, and why |
| `GET /api/projects/{id}/memory/{mid}` | fetch one |
| `PATCH /api/projects/{id}/memory/{mid}` | edit |
| `POST /api/projects/{id}/memory/{mid}/accept` | **human review**: make authoritative |
| `POST /api/projects/{id}/memory/{mid}/reject` | **human review**: decline |
| `POST /api/projects/{id}/memory/{mid}/archive` | retire |
| `POST /api/projects/{id}/memory/{mid}/supersede` | replace with a newer memory |

`/relevant` and the accept/reject routes are additive; the V2.4 surface is
unchanged, and the 23 V1 memory tests still pass against it.

## Injection sites

| Node | Types requested |
| --- | --- |
| Triage | all |
| Architect | `architecture_decision`, `technology_decision`, `constraint` |

Engineer and Reviewer receive accepted architecture analysis (which already
carries injected decisions) rather than a second memory query.

## Bounds

| Bound | Value | Why |
| --- | --- | --- |
| `MAX_INJECTION_ITEMS` | 5 | keeps the prompt focused on the most relevant decisions |
| `MAX_INJECTION_BYTES` | 20 000 | hard cap; `truncated: true` is reported when it bites |
| `CANDIDATE_LIMIT` | 200 | upper bound on rows scored in Python |

`CANDIDATE_LIMIT` only bites on a project where more than 200 memories match the
same term; those are taken most-recent-first. Retrieval is therefore
O(bounded) rather than O(project history). **SQLite FTS5 is the documented
upgrade path** if that limit is ever reached in practice — it was not chosen now
because BM25 scores cannot be explained in terms of matched terms, and
explainability is the point.

## Tests

| File | Covers |
| --- | --- |
| `tests/test_memory_retrieval.py` (21) | tokenisation, scoring, ranking, determinism — pure, no database |
| `tests/test_memory_v2.py` (22) | realistic multi-word descriptions against a real database, authoritative-vs-speculative separation, lifecycle, review events |
| `tests/test_memory.py` (23) | the V1 suite, still passing — retrieval is backward compatible |

The closure test is
`test_realistic_task_description_retrieves_relevant_accepted_decision`. It
asserts the query sentence is absent from the stored memory *before* asserting
retrieval finds it, so it cannot pass by substring luck.

End to end, the `memory-injection` qualification scenario seeds three memories in
a real project — one relevant and accepted, one relevant but only proposed, one
accepted but irrelevant — runs a real workflow, and asserts from the event log
that exactly the first reached the agent.

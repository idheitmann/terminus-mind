# terminus-mind: Design

A self-evolving agent memory on TerminusDB. Every node and edge starts as a
*hypothesis* and is only proven over time through interaction with a human —
the way a human refines a world model through experience and consolidation.

## Principles

1. **Beliefs, not facts.** The central object is a reified `Claim` carrying
   evidence counters, temporal validity, provenance, and a lifecycle status.
   Nothing the agent extracts is trusted at birth.
2. **Never delete, supersede.** Contradicted or outdated claims get their
   validity interval closed and a `superseded_by` link. Point-in-time queries
   stay possible; the audit trail is total (TerminusDB commits + document
   history).
3. **The human is the oracle.** Human statements are high-weight evidence;
   human corrections pin claims. The agent is *supposed* to ask about its
   most uncertain, most-used beliefs — uncertainty drives questions, answers
   drive counters, counters drive promotion.
4. **Intelligence lives in the agent, structure lives in the store.** This
   package has no LLM or embedding dependency. The agent (hermes) does
   extraction, entity resolution, and adjudication; terminus-mind provides
   the primitives, candidate retrieval, scoring, and the audit substrate.
5. **Ontology follows usage — conservatively.** The vocabulary (predicates,
   entity types) is part of what is learned, but it has built-in *resistance*:
   asserting with a novel term is challenged with the nearest existing terms
   and must be explicitly forced; forced novel terms enter as `provisional`
   and only become `established` through a usage + human-ratification uptake
   path. Typing is additive and behind usage, never blocking ingestion.
   TerminusDB enforces the schema side for free: weakening changes commit;
   strengthening changes are rejected with witnesses.

## Lineage (see research notes in git history / arXiv refs)

| Mechanism | Source |
|---|---|
| Bi-temporal claim edges (`valid_at`/`invalid_at` + `created_at`/`expired_at`), supersession not deletion | Zep/Graphiti (arXiv:2501.13956) |
| Beta evidence counters; explicit uncertainty term distinguishing *unproven* from *disproven* | Jøsang subjective logic; NELL noisy-OR promotion |
| `candidate → confirmed → retired` lifecycle with human curation as high-weight evidence | NELL (Carlson et al. 2010) |
| Activation = f(use frequency, recency), optimized-learning form | ACT-R (Anderson & Schooler 1991) |
| NEW / CONFIRM / SUPERSEDE / CONTRADICT adjudication loop, agent-driven | Mem0 (arXiv:2504.19413) + Graphiti semantics |
| Episodic→semantic split; episodes lossless, claims distilled, evidence pointers back | Graphiti, AriGraph, complementary learning systems |
| Offline consolidation ("sleep") as a distinct phase | Letta sleep-time compute (arXiv:2504.13171) |
| Usage-driven type promotion | Wikidata practice; Graphiti optional ontologies |

## Data model

Four TerminusDB document classes (instance graph). Documents are nodes;
ID-valued properties are edges.

- **Episode** — lossless episodic tier. Raw interaction content, source
  (`human` / `agent` / `document`), `occurred_at`, `consolidated` flag.
  Random key; append-only.
- **Entity** — semantic node. `name` (lexical key → idempotent upsert),
  `entity_type` *(free string)*, `aliases`, optional `summary`.
- **Claim** — reified semantic edge; the central object.
  `subject → Entity`, `predicate` *(free string)*, `object_entity → Entity`
  *or* `object_value` literal, `fact_text` (NL sentence — the retrieval and
  embedding target), bi-temporal fields, counters
  (`confirms`/`contradicts`/`human_confirms`), `status`, `pinned`,
  activation fields (`use_count`/`last_used_at`), `evidence → Set<Episode>`.
- **VocabTerm** — the learned ontology itself: one document per predicate or
  entity-type term. `kind` (`predicate` / `entity_type`), `status`
  (`provisional` / `established` / `deprecated`), `usage_count`,
  `canonical` (alias target for merged terms), `ratified` flag.

### Conservative ontology uptake

The vocabulary learns, but it resists. Three mechanisms:

1. **Resistance at the gate.** `assert_claim()` normalizes the predicate
   (lowercase snake_case) and looks it up. A novel term whose string is
   close to an existing term (normalized similarity above threshold) is
   *rejected with suggestions* — the agent must either reuse the existing
   term or pass `force=True`. The same gate guards `entity_type` and new
   `Entity` names (fuzzy match against names + aliases). Deprecated terms
   with a `canonical` pointer are rewritten transparently, so old vocabulary
   converges instead of fragmenting.
2. **Probation.** A forced novel term is registered `provisional`. Claims
   using it flow normally — ingestion is never blocked — but provisional
   terms are second-class: surfaced in `tm vocab`, counted, and reviewable.
3. **Uptake.** `consolidate()` nominates provisional terms for ratification
   when they cross usage thresholds (≥3 uses including ≥1 human-confirmed
   claim). A human ratifies (`established`) or merges them into an existing
   term (`deprecated` + `canonical`, with existing claims rewritten). Only
   established, repeatedly-used predicates are candidates for the eventual
   typed-schema promotion (real TerminusDB classes/properties via weakening
   schema commits) — the documented upgrade path.

Entities follow the same philosophy: created `provisional`, promoted to
`established` by `consolidate()` once they participate in a confirmed claim.

Derived at read time, never stored (`scoring.py`):

```
credence    = (confirms + a·W) / (confirms + contradicts + W)      W=2, a=0.5
uncertainty = W / (confirms + contradicts + W)
activation  = ln((use_count+1)/(1-d)) − d·ln(age_hours)            d=0.5
```

## Write path (per interaction)

1. `observe()` — append the Episode. Always, losslessly.
2. Agent extracts candidate entities/claims (its LLM, its prompt).
3. Agent calls `recall(subject=…)` to fetch related live claims, then
   adjudicates each candidate:
   - **NEW** → `assert_claim()` (status `candidate`, `confirms=1`).
   - **CONFIRM** → `confirm()` (+1, +3 if human; evidence link).
   - **SUPERSEDE** (world changed / correction) → `supersede()` — closes the
     old claim, links `superseded_by`, inserts the new one.
   - **CONTRADICT** (unresolved conflict) → `contradict()`; both claims
     coexist and the pair surfaces in `conflicts()` for human review.
4. Human corrections use `correct()` = supersede + `pinned=true` + human
   weight. Pinned claims yield only to another human statement.
5. Every mutation is a TerminusDB commit with a structured message and the
   agent as author — `tm log` / `tm history <claim>` reconstruct the entire
   life of any belief, and time-travel reads answer "what did I believe
   last month?"

## Lifecycle (consolidate)

`consolidate()` runs the deterministic "sleep" phases:

- **Promotion:** `candidate → confirmed` when credence ≥ 0.8 *and*
  (≥2 human confirmations or pinned).
- **Retirement:** credence < 0.4, or superseded/expired.
- **Vocabulary uptake:** provisional `VocabTerm`s crossing usage thresholds
  are nominated for human ratification; entities participating in confirmed
  claims are promoted to `established`.
- **Review queue:** rank live candidates by `uncertainty × activation` — the
  beliefs most worth asking the human about next.

LLM-dependent phases (episode distillation, reflection, dedup judging) belong
to the agent's own sleep job: it pulls `unconsolidated_episodes()`, distills,
writes claims through the same primitives, then `mark_consolidated()`.

## Retrieval

No LLM in the read path. `recall()` = template/substring filtering →
score by `relevance + credence + activation`, default-filter expired claims
(point-in-time queries opt in). Every recall the agent actually uses touches
`use_count`/`last_used_at`, closing the frequency/recency loop.

Upgrade path (deliberately not in MVP): sidecar embedding index over
`fact_text` (rebuildable cache, never a second source of truth), then
Personalized PageRank for multi-hop association, then community summaries.

## Deliberately skipped

Hard deletion of beliefs (no code path), full AGM/TMS, NLI contradiction
models, ANN indexes, embedding deps, community detection — each has a noted
upgrade path and none earns its complexity at personal-agent scale.

## Storage / ops

TerminusDB v12 in podman, HTTP API on `127.0.0.1:6363` (thin httpx client;
the PyPI `terminusdb-client` is stale). All behaviors verified empirically
against the live instance: document CRUD, template queries, WOQL, commit
log, per-document history, time-travel reads, diff, branching, GraphQL,
weakening-vs-strengthening schema enforcement.

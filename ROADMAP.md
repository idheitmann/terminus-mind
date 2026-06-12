# Roadmap

Ordered by what unlocks learning value soonest. The system only improves
through use, so everything in phase 1 serves getting real interaction data
flowing; structure and scale come after.

## Phase 1 — Prove the loop in real use

**1.1 Hermes dogfooding + memory skill prompt.** Wire `tm-mcp` into hermes
and run it daily. Write the companion system-prompt fragment that teaches the
adjudication loop (observe each exchange → recall before asserting →
new/confirm/supersede/contradict → weave one `memory_review` question into
conversation when natural). The tool descriptions carry most of this, but a
prompt fragment makes it reliable. Tune from transcripts: `HUMAN_WEIGHT`,
`SIMILARITY_GATE`, promotion thresholds — these are guesses until real use
calibrates them.

**1.2 The sleep job.** A scheduled agent run (cron/systemd timer) that pulls
`unconsolidated_episodes()`, re-extracts claims with full context through the
same primitives, `mark_consolidated()`, then `consolidate()`. Run it on a
TerminusDB **branch** and merge after `tm` review — a bad sleep run becomes
an unmerged branch instead of a polluted world model. The branch plumbing is
verified; needs a `Mind.branch()/merge()` wrapper and a runner script.

**1.3 Semantic recall (embedding sidecar).** Substring matching will miss
paraphrases almost immediately. Add an optional sidecar index over
`fact_text` + entity names: numpy flat cosine, keyed by document id,
rebuildable from the DB (a cache, never a second source of truth). Local
embedding model or API behind a small interface. This also upgrades the
**vocabulary resistance gate** from bigram similarity to semantic similarity
(`employer_of` vs `works_at`), which matters more than recall quality.

## Phase 2 — Structure earns its keep

**2.1 Entity resolution & dedup sweep.** `merge_entities(keep, drop)`
primitive (repoint claims, union aliases, deprecate the duplicate), plus a
sleep-job phase: embedding-blocked candidate pairs → agent judges "same
entity?" → merge. Aliases exist; the merge primitive doesn't yet.

**2.2 Claim→claim evidence (reflection).** Let `evidence` also point at
claims, not just episodes (weakening schema change). Then the sleep job can
write higher-level synthesized beliefs ("Ivan prefers tools he can self-host")
that cite their supporting claims — Generative-Agents reflection with real
provenance. Retiring a supporting claim flags its dependents for review.

**2.3 Typed-schema promotion.** The endgame of vocabulary uptake: an
established, ratified predicate with heavy usage gets promoted to a real
TerminusDB property/class via a weakening schema commit, with matching claims
migrated. From then on the database itself type-checks new claims using it.
`tm promote predicate works_at` with a dry-run diff.

**2.4 Multi-hop recall.** `about` does one hop. Add Personalized PageRank
over an in-memory mirror of the claim graph (seeded at recall hits) for
associative retrieval — ~30 lines with networkx, big step up for "what do I
know that's relevant to X" questions.

## Phase 3 — Longevity

**3.1 Forgetting as review, not deletion.** Low-activation, low-credence
candidates accumulate. Surface a "stale beliefs" queue (inverse of
`tm review`); spaced verification — re-ask about confirmed beliefs whose
last human confirmation is old, with interval growing per confirmation
(spaced repetition where the human is the oracle).

**3.2 Observability.** `tm digest` — human-readable "what changed this
week" from commit diffs (learned/promoted/superseded/conflicts). Static HTML
graph export for visual inspection. These keep the human curation loop cheap
enough to actually happen.

**3.3 Scale hardening.** Current reads are full-type scans and writes are
one commit per touch — fine to ~10k claims. When it hurts: WOQL-side
filtering, batched commits, `/api/optimize` after sleep runs, and the
embedding index goes ANN.

**3.4 Portability insurance.** `tm dump`/`tm load` to plain JSONL in git.
TerminusDB's OSS maintenance cadence is a known risk; the data model is
deliberately plain-documents-with-id-links, so an export keeps the world
model hostage to nothing.

**3.5 Multi-agent trust.** Commit authorship already separates agents.
Next: per-source evidence weighting (a claim confirmed by two different
agents and the human is stronger than three confirmations from one agent),
and speculative-learning branches per agent.

## Non-goals (revisit only with evidence)

Community detection / graph summarization (premature below ~10k entities),
NLI contradiction models (the agent judges), full AGM/TMS belief revision,
hard deletion of beliefs (never), web UI product surface.

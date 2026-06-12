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

**1.2 The sleep job. ✅ DONE.** `tm sleep` (sleep.py): local Qwen2.5-7B
(llama.cpp, TM_LLM_URL) distills unconsolidated episodes into candidate
claims on a `sleep-<timestamp>` branch — adjudication is deterministic
(confirm equivalents by overlap, reuse vocabulary under resistance, skip +
journal weak matches), then `consolidate()`, then rebase into main
(`--review` leaves the branch for inspection). Nightly via
`ops/tm-sleep.timer` (03:47, before the 04:23 backup). Supersession is
deliberately excluded — that judgment stays with hermes/humans. First live
run: 31 extracted, 17 confirms, 5 asserts, 0 duplicates; entity gate
correctly refused `TerminusDB v12` vs `TerminusDB`.

**1.3 Semantic recall (embedding sidecar). ✅ DONE.** llama.cpp +
nomic-embed-text-v1.5 (`ops/tm-embed.service`, port 8089); numpy flat-cosine
index cache, `tm reindex`. Recall blends token overlap with semantic
relevance; the predicate/entity-type resistance gate gets a semantic second
opinion (gate 0.74, calibrated — it only ever *adds* resistance). Entity
*names* deliberately excluded: measured Ada~Grace at 0.747, bare names carry
too little semantics — entity dedup stays in 2.1 where summaries exist.
Degrades gracefully when the server is down.

## Phase 1.5 — Hermes plugin & the hindsight transition

Hermes' memory-provider plugin API
([guide](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin))
justifies revisiting the earlier MCP-only stance: hooks automate what the
prompt fragment can only encourage. MCP stays — it's how every *other*
agent reaches the memory — the plugin is hermes-specific deep integration.
Contract facts that shape the plan: only one external provider can be
active at a time, and there is no cross-provider migration support, so the
transition from hindsight (the current provider) must be deliberate.

**1.5.1 Hindsight feature inventory.** The local hindsight instance
(`hindsight-api.service`) is what terminus-mind must match or consciously
drop. Inventory: which hooks its plugin implements, which of its API
features hermes actually exercises, and what it has that we lack (likely:
semantic/embedding recall — which would promote 1.3 from nice-to-have to
cutover-blocker). Output: a gap table with keep/port/drop decisions,
human-ratified.

**1.5.2 Plugin skeleton.** `plugins/memory/terminus-mind/` wrapping `Mind`
in-process. Thin, because the tool surface already exists: `get_tool_schemas`
/ `handle_tool_call` delegate to `TOOL_SPECS` / `dispatch`. Hook mapping:
`system_prompt_block` → established-belief digest + protocol; `prefetch` →
`recall(query)`; `sync_turn` → `observe()` on a daemon thread (cheap append,
honoring the non-blocking contract); `on_session_end` → distill +
`consolidate()` (the sleep job gets a natural trigger); `on_pre_compress` →
observe a summary episode before context is discarded; `cli.py` → `tm`
passthrough. Storage config honors `hermes_home` profile isolation
(per-profile `TM_DB`).

**1.5.3 Shadow period.** Hindsight stays the active provider; terminus-mind
keeps running via MCP exactly as now. Compare recall quality on real
questions for a few weeks; frictions and misses go to the journal. The
single-provider rule makes this the only clean A/B available — use it.

**1.5.4 Vetted cutover.** Preconditions: gap table resolved, shadow period
shows recall parity, sleep job proven. Then: import worth-keeping hindsight
memories as `candidate` claims (source `document`, provenance marked
`imported:hindsight`, normal credence — they re-prove through use like
everything else), enable the plugin, disable hindsight but keep it readable
as an archive until a full review cycle passes. Rollback = re-enable
hindsight; nothing in terminus-mind is lost by switching back.

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

## Before going public (Reddit / announcement gate)

The core claim — beliefs proven and ontology kept clean *over weeks of real
use* — is exactly what no test suite shows. The dogfooding period produces
the artifact that makes the post: a commit log of a world model actually
converging. Gates, in order:

**Evidence gates (let these accumulate, ~2–3 weeks):**
- [ ] Two or three full weekly cycles unattended: nightly sleep runs clean,
      Monday triage reports stay sane, no babysitting.
- [ ] The candidate:confirmed ratio visibly shifts (currently 21:1) —
      promotions happening in the wild is the proof of life.
- [ ] The first real contradiction fires in anger: a correction supersedes
      cleanly, pins, and reads right in `tm history`.
- [ ] Vocabulary reaches ~50+ predicates and stays convergent — junk terms
      (`cannot`) get pruned/merged faster than they accumulate. This is the
      make-or-break test of the conservative-ontology design.

**Stranger-proofing work (do after the evidence gates, before posting):**
- [ ] `tm load` — restore from a dump, plus one actual restore drill.
      Posting with backups but no restore answer is embarrassing.
- [ ] Parameterize the ops/ systemd units (no hardcoded /home/ivanh paths)
      and a quickstart that doesn't assume this exact podman/llama.cpp
      setup; document non-default credentials.
- [ ] README: address "isn't TerminusDB unmaintained?" up front (plain
      document data model, tm dump portability, versioning is the feature
      being bought — the DESIGN.md argument, surfaced).
- [ ] README leads with a real `tm log` excerpt showing the system
      learning — the debut is the evidence, not the architecture.

## Non-goals (revisit only with evidence)

Community detection / graph summarization (premature below ~10k entities),
NLI contradiction models (the agent judges), full AGM/TMS belief revision,
hard deletion of beliefs (never), web UI product surface.

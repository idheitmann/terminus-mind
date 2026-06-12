# Triage report

## 2026-06-12 (interactive)

4 active entries from `hermes` (2 error/blocking, 2 resistance_misfire/minor).

**error × 2 — `memory_about` 500 on object_entity filter.** Already fixed in
`89548be` (TerminusDB v12 500s on template queries over Optional link
properties; incoming edges now go through WOQL). Regression test
`test_about_includes_incoming_claims` covers it. Verified live. Closed.

**resistance_misfire × 2 — "suggested the same predicate at 0.75".** Not a
misfire: hermes asserted `uses`, the gate suggested the existing `uses_tool`
(0.75 = bigram Dice 0.545 + 0.2 shared-prefix bonus), hermes correctly
reused it. The friction was perceptual — hermes read the suggestion as
self-referential rather than as a nearby existing term to converge on.
Action taken: rewrote the `resisted` hint in `tools.py` to state explicitly
that suggestions are *existing terms to reuse* and that resistance is the
vocabulary working, not an error. No threshold change proposed: the gate
fired correctly on a genuine near-duplicate; `SIMILARITY_GATE=0.55` stands
until there is evidence of resistance blocking *genuinely different*
concepts.

Entries archived to `journal/archive/20260612-hermes.jsonl`.

**Awaiting human ratification:** nothing.

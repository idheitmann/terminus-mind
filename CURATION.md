# Human curation routine

What the human does with `tm`, and how often. The system is designed so the
expensive part of curation happens *inside conversation* (answering hermes'
proving questions costs nothing extra); the CLI part should stay around ten
minutes a week. NELL ran for years on ~10–15 minutes of human curation a
day — this aims lower because the agent does the extraction and the proving
questions are woven into normal use.

## Daily — nothing

No CLI obligation. Just talk to hermes normally and answer its hedged
questions ("I believe X — is that right?") when they come up. Those answers
are the evidence engine; everything else exists to make them count.

## Weekly (~10 min, Monday — paired with the triage timer)

Run in this order; each feeds the next.

1. **`tm doctor`** — 5 seconds. All green or stop and fix.
2. **Check the triage analysis** — the Monday 09:17 timer appends a
   read-only report to `journal/triage-runs.log`. If it found anything
   actionable (bugs, friction patterns), open a Claude Code session here
   and say "run the triage" — fixes, the committed report, and
   `tm journal --archive` happen there, not by hand.
3. **`tm consolidate`** — runs promotion/retirement/establishment and
   prints vocabulary nominations. *Until the sleep job exists (roadmap
   1.2) this is the only thing that runs the lifecycle — skipping it means
   beliefs never get promoted no matter how much evidence accumulates.*
4. **`tm vocab --status provisional`** — act on the nominations from step 3:
   - genuinely new concept → `tm ratify predicate <name>`
   - duplicate of an existing term → `tm merge-term predicate <name> <into>`
   - junk / one-off → leave it provisional; unused terms stay second-class
     and are harmless.
5. **`tm conflicts`** — for each unresolved conflict, decide:
   - one side is right → `tm confirm <claim>` it, `tm correct <other>` or
     let consolidation retire the loser as contradictions accumulate
   - both legitimately true (opinion, change over time) → leave them; they
     coexist by design.
6. **`tm review`** — skim the top uncertain-but-active beliefs. Anything
   you can settle in five seconds, settle: `tm confirm <claim>` or
   `tm correct <claim> --value ...`. Don't grind the list — hermes asks
   these in conversation anyway; this is just a shortcut.
7. **`tm stats`** — one glance: are claims growing, is the
   candidate:confirmed ratio moving, conflict count sane? Drift here is the
   earliest signal something upstream is off.

## Monthly (~20 min)

- **`tm log -n 100`** — skim the commit messages; it reads as a diary of
  what the agent learned. Surprises here mean the prompt fragment or a
  threshold needs attention.
- **`tm vocab`** (full) — look at the *established* vocabulary as a whole:
  near-duplicate predicates that slipped past the gate get
  `tm merge-term`; types that never took hold can stay, they cost nothing.
- **`ls ~/.local/share/terminus-mind/backups/`** — confirm the daily dumps
  are actually appearing (14 kept).
- **`tm about <a-few-key-entities>`** — spot-check that the picture of
  people/projects you care about matches reality; `tm correct` anything
  stale on the spot.

## As needed (no cadence)

- **`tm dump -o pre-change.jsonl`** — before anything risky (schema work,
  bulk imports, the hindsight cutover).
- **`tm reindex`** — after changing the embedding model, or if semantic
  recall behaves oddly (the index is a cache; rebuilding is always safe).
- **`tm history <claim>` / `tm log`** — when you wonder "why does it
  believe that?" — full life of any belief, state by state.
- **`tm journal --tail 20`** — when hermes mentions friction in chat;
  check whether it also filed it (it should).

## What you should never need to do

Hand-edit documents in TerminusDB, delete beliefs (nothing deletes;
`tm correct` supersedes), tune `scoring.py` thresholds by feel (that's
triage-with-evidence territory), or run `tm assert` routinely yourself —
if you're hand-entering facts, the extraction loop is failing and that's a
journal/triage matter.

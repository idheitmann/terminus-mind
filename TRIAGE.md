# Journal triage procedure

Weekly pass over agent-reported friction (`journal/*.jsonl`). Run by Claude
Code in this repo. The contract: **fix what is mechanical, propose what is
judgment, archive what is processed.** Never auto-tune conservatism
thresholds — the reporting agent has a structural bias toward less
resistance, so threshold changes are human decisions.

Two modes:

- **Headless (systemd `terminus-mind-triage.timer`, Mon 09:17): report-only.**
  The scheduled run has no edit or shell permissions by design. It reads
  `journal/*.jsonl` directly, performs steps 1–3 as *analysis*, and writes
  the would-be report to `journal/triage-runs.log`. Nothing is changed or
  archived.
- **Interactive (a human watching): full procedure.** All steps below,
  including fixes, the committed report, and archiving.

## Steps

1. `uv run tm journal` — if `total` is 0, stop (nothing to do).
2. Read all active entries: `uv run tm journal --tail 1000`.
3. Triage by kind:
   - **error / blocking** — try to reproduce against the live TerminusDB
     (podman, 127.0.0.1:6363; never against the production `mind` DB —
     use a throwaway `tm_triage_*` database, delete it afterwards). If
     reproducible: fix, add a regression test, run `uv run pytest`,
     commit. If not reproducible: note it in the report.
   - **resistance_misfire / recall_miss** — do NOT change
     `SIMILARITY_GATE` or other `scoring.py` thresholds. Aggregate the
     evidence; if a pattern is strong (≥3 similar entries), write a
     concrete proposal (what to change, to what value, citing entries)
     in the report for human ratification.
   - **unclear_choice** — improve the relevant tool description in
     `tools.py` or the wording in `prompts/memory.md`; these are cheap,
     reviewable wording fixes. Commit.
   - **slow / other** — aggregate; escalate in the report only on a
     pattern.
4. Write/update `journal/TRIAGE-REPORT.md`: date, entry counts by kind,
   actions taken (with commit hashes), proposals awaiting ratification,
   non-reproducible reports. Prepend the new section; keep prior sections.
5. `uv run tm journal --archive` — move processed entries to
   `journal/archive/`.
6. Commit the report and archive together:
   `git add journal && git commit -m "Triage journal: <summary>"`.

## Boundaries (headless runs especially)

- Work only inside this repository and throwaway `tm_triage_*` databases.
- No pushes, no destructive git operations, no changes to the `mind`
  database, no dependency changes.
- If a fix needs anything beyond these boundaries, describe it in the
  report instead of doing it.

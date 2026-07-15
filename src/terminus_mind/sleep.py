"""The sleep cycle: offline consolidation of episodes into beliefs.

Runs on a TerminusDB branch (`sleep-<date>`), so a bad run is just an
unmerged branch. Phases:

1. Distill — for each unconsolidated episode, a local LLM extracts
   candidate triples (the current vocabulary is shown to encourage reuse).
2. Adjudicate — deterministic, no LLM: equivalent existing claim -> confirm;
   vocabulary resistance with a strong suggestion -> reuse it; weak
   suggestion -> skip and journal (conservatism wins over coverage).
3. Lifecycle — consolidate() runs promotion/retirement/uptake on the branch.
4. Merge — rebase main onto the branch result (skipped with merge=False so
   a human can inspect the branch first).

Extraction quality is allowed to be mediocre: every write enters as an
unproven candidate and must still earn promotion through the normal
evidence lifecycle. Supersession is deliberately NOT attempted here —
deciding that the world changed needs more judgment than a 7B model gets
to exercise unattended; coexisting claims surface in conflicts/review.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import journal
from .llm import ChatLLM
from .mind import Mind, NoveltyResisted

EXTRACT_SYSTEM = """You extract factual claims from an interaction episode \
for a personal knowledge graph about the user and their world.

Return ONLY a JSON array. Each element:
{"subject": "<entity name>", "predicate": "<snake_case relation>",
 "object": "<entity name>" OR null, "value": "<literal string>" OR null,
 "fact_text": "<the claim as one short natural sentence>"}

Rules:
- Extract only durable facts worth remembering (relationships, attributes,
  preferences, biography, projects). Skip pleasantries, transient state,
  meta-discussion, and anything about this extraction task itself.
- Exactly one of object/value per claim: object for entities (people,
  places, organizations, tools), value for literals (dates, numbers,
  free-text attributes).
- REUSE the existing vocabulary whenever a predicate fits; coin a new
  snake_case predicate only for genuinely new relation types.
- Subjects and objects must be canonical entity names, never pronouns:
  resolve "I"/"me"/"he"/"she"/"they" to a name from the known entities (in
  a first-person episode, "I" is the human user). If a pronoun cannot be
  resolved, skip that claim.
- 0 claims is a fine answer: return []."""

# adjudication thresholds (deterministic phase)
CONFIRM_OVERLAP = 0.9    # fact_text overlap fallback — near-identical sentences only
REUSE_SIMILARITY = 0.75  # resistance suggestion strong enough to auto-reuse

# 7B models ignore prompt-level pronoun rules often enough that this must be
# a code guard (first nightly run created an Entity/I with 11 claims).
# First-person pronouns remap to TM_SELF when set; everything else skips.
SELF_PRONOUNS = {"i", "me", "my", "myself", "the user", "user"}
OTHER_PRONOUNS = {"he", "she", "they", "we", "him", "her", "them", "it"}


def _resolve_pronouns(cand: dict, self_name: str | None, report: dict) -> bool:
    """Returns False (and records) when the candidate must be skipped."""
    import re

    for field in ("subject", "object"):
        val = (cand.get(field) or "").strip()
        low = val.lower()
        if low in SELF_PRONOUNS:
            if not self_name:
                report["skipped"].append({"claim": cand.get("fact_text"),
                                          "reason": f"pronoun {field} '{val}' and TM_SELF unset"})
                return False
            cand[field] = self_name
            if cand.get("fact_text"):
                cand["fact_text"] = re.sub(rf"^{re.escape(val)}\b", self_name,
                                           cand["fact_text"], flags=re.IGNORECASE)
        elif low in OTHER_PRONOUNS:
            report["skipped"].append({"claim": cand.get("fact_text"),
                                      "reason": f"unresolvable pronoun {field} '{val}'"})
            return False
    return True


def _overlap(a: str, b: str) -> float:
    ta = {w for w in a.lower().split() if len(w) > 2}
    tb = {w for w in b.lower().split() if len(w) > 2}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def run_sleep(
    mind: Mind,
    llm: ChatLLM | None = None,
    limit: int = 20,
    merge: bool = True,
) -> dict:
    """Run one sleep cycle. Returns a report dict."""
    llm = llm or ChatLLM()
    report: dict = {
        "episodes": 0, "extracted": 0, "asserted": 0, "confirmed": 0,
        "vocab_reused": 0, "skipped": [], "branch": None, "merged": False,
        "consolidation": None,
    }
    if not llm.available():
        report["error"] = f"LLM unavailable at {llm.url}"
        return report
    episodes = mind.unconsolidated_episodes()[:limit]
    if not episodes:
        report["consolidation"] = mind.consolidate()
        return report

    branch = f"sleep-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    mind.client.create_branch(branch)
    report["branch"] = branch
    bmind = Mind(mind.client.on_branch(branch), agent="sleep")

    import os

    self_name = os.environ.get("TM_SELF")
    vocab = sorted(t["name"] for t in bmind.vocab(kind="predicate")
                   if t["status"] != "deprecated")
    entities = sorted(e["name"] for e in bmind.client.list_docs("Entity"))

    # one confirm per claim per run — a night is one observation event regardless
    # of how many overlapping episode segments mention the same fact
    confirmed_this_run: set[str] = set()

    for ep in episodes:
        report["episodes"] += 1
        prompt = (
            f"Existing predicates (REUSE these when they fit):\n{', '.join(vocab) or '(none)'}\n\n"
            f"Known entities (match names exactly when referring to them):\n"
            f"{', '.join(entities) or '(none)'}\n\n"
            f"Episode (source: {ep['source']}, at {ep['occurred_at']}):\n{ep['content']}"
        )
        try:
            candidates = llm.complete_json(EXTRACT_SYSTEM, prompt)
        except Exception as e:
            report["skipped"].append({"episode": ep["@id"], "reason": f"extraction failed: {e}"[:200]})
            continue
        if not isinstance(candidates, list):
            candidates = []
        for cand in candidates:
            if not isinstance(cand, dict) or not cand.get("subject") or not cand.get("predicate"):
                continue
            if (cand.get("object") is None) == (cand.get("value") is None):
                continue
            report["extracted"] += 1
            if not _resolve_pronouns(cand, self_name, report):
                continue
            try:
                _adjudicate(bmind, cand, ep["@id"], report, confirmed_this_run)
            except Exception as e:  # one bad candidate never aborts the run
                report["skipped"].append({"claim": cand.get("fact_text"),
                                          "reason": str(e)[:200]})
        bmind.mark_consolidated(ep["@id"])

    report["consolidation"] = bmind.consolidate()
    if merge:
        mind.client.rebase_from(branch, author="sleep")
        mind.client.delete_branch(branch)
        report["merged"] = True
    return report


def _adjudicate(bmind: Mind, cand: dict, episode_id: str, report: dict,
                confirmed_this_run: set[str] | None = None) -> None:
    """Deterministic adjudication: confirm equivalents, assert novelty,
    let resistance arbitrate vocabulary."""
    from urllib.parse import unquote

    from .mind import _norm

    def same_object(hit: dict) -> bool:
        if cand.get("object") is not None:
            ent = unquote((hit.get("object_entity") or "").removeprefix("Entity/"))
            return ent.lower() == cand["object"].strip().lower()
        return ((hit.get("object_value") or "").strip().lower()
                == (cand.get("value") or "").strip().lower())

    pred = _norm(cand["predicate"])
    existing = bmind.recall(subject=cand["subject"], touch=False, limit=200)
    for hit in existing:
        # equivalence = same relation to the same thing; fact-text overlap
        # alone cross-confirms siblings ("uses tool X" ~ "uses tool Y")
        if hit["predicate"] == pred and (
            same_object(hit)
            or _overlap(hit["fact_text"], cand.get("fact_text") or "") >= CONFIRM_OVERLAP
        ):
            claim_id = hit["@id"]
            if confirmed_this_run is not None and claim_id in confirmed_this_run:
                return  # already confirmed once this run; one night = one observation
            bmind.confirm(claim_id, episode=episode_id, by_human=False)
            if confirmed_this_run is not None:
                confirmed_this_run.add(claim_id)
            report["confirmed"] += 1
            return
    kwargs = dict(
        object=cand.get("object"), value=cand.get("value"),
        fact_text=cand.get("fact_text"), episode=episode_id, by_human=False,
    )
    try:
        bmind.assert_claim(cand["subject"], cand["predicate"], **kwargs)
        report["asserted"] += 1
    except NoveltyResisted as e:
        top = e.suggestions[0]
        if e.kind == "predicate" and top["similarity"] >= REUSE_SIMILARITY:
            try:
                bmind.assert_claim(cand["subject"], top["name"], **kwargs)
                report["asserted"] += 1
                report["vocab_reused"] += 1
            except NoveltyResisted as e2:
                # the retry can hit *entity* resistance in turn
                report["skipped"].append({"claim": cand.get("fact_text"), "reason": str(e2)[:200]})
                journal.write_entry("sleep", "unclear_choice", f"entity resisted on retry: {e2}"[:300],
                                    tool="sleep.assert", severity="minor")
        elif e.kind == "entity" and top["similarity"] >= REUSE_SIMILARITY:
            # High-similarity entity (underscore/case variant, near-duplicate): swap
            # the matching field and retry with the canonical existing name.
            subj = cand["subject"]
            new_kwargs = dict(kwargs)
            if new_kwargs.get("object") == e.name:
                new_kwargs["object"] = top["name"]
            elif subj == e.name:
                subj = top["name"]
            else:
                report["skipped"].append({"claim": cand.get("fact_text"), "reason": str(e)[:200]})
                return
            try:
                bmind.assert_claim(subj, pred, **new_kwargs)
                report["asserted"] += 1
                report["vocab_reused"] += 1
            except NoveltyResisted as e2:
                report["skipped"].append({"claim": cand.get("fact_text"), "reason": str(e2)[:200]})
                journal.write_entry("sleep", "unclear_choice", f"entity resisted on retry: {e2}"[:300],
                                    tool="sleep.assert", severity="minor")
        elif e.kind == "entity":
            # Similarity below threshold — needs human judgment
            report["skipped"].append({"claim": cand.get("fact_text"), "reason": str(e)[:200]})
            journal.write_entry("sleep", "unclear_choice", f"entity resisted: {e}"[:300],
                                tool="sleep.assert", severity="minor")
        else:
            report["skipped"].append({"claim": cand.get("fact_text"), "reason": str(e)[:200]})
            journal.write_entry(
                "sleep", "unclear_choice",
                f"weak vocab suggestion, skipped: {cand.get('predicate')} vs {top['name']} "
                f"({top['similarity']})", tool="sleep.assert", severity="minor")
    except Exception as e:
        report["skipped"].append({"claim": cand.get("fact_text"), "reason": str(e)[:200]})

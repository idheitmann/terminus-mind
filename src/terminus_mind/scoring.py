"""Derived belief scores. Computed at read time, never stored.

credence    — is this true?  Beta evidence counters (subjective logic).
uncertainty — how unproven is it? Explicit, distinct from disbelief.
activation  — will I need it?  ACT-R optimized-learning approximation.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

# subjective-logic prior: W = prior weight, A = base rate
W = 2.0
A = 0.5
# ACT-R decay
D = 0.5
# human testimony counts this many times a plain observation
HUMAN_WEIGHT = 3

# lifecycle thresholds
PROMOTE_CREDENCE = 0.8
PROMOTE_HUMAN_CONFIRMS = 2
RETIRE_CREDENCE = 0.4
# vocabulary uptake
VOCAB_NOMINATE_USES = 3
# resistance: novel terms at least this similar to an existing term are challenged
SIMILARITY_GATE = 0.55


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def credence(confirms: int, contradicts: int) -> float:
    return (confirms + A * W) / (confirms + contradicts + W)


def uncertainty(confirms: int, contradicts: int) -> float:
    return W / (confirms + contradicts + W)


def activation(use_count: int, created_at: str, last_used_at: str | None = None) -> float:
    """Optimized-learning base activation, in units of ln(uses/hour-ish).

    Higher = more frequently and recently needed. Recency enters through a
    small boost from last use; frequency through use_count over lifetime.
    """
    age_h = max((datetime.now(timezone.utc) - parse_dt(created_at)).total_seconds() / 3600, 1e-3)
    base = math.log((use_count + 1) / (1 - D)) - D * math.log(age_h)
    if last_used_at:
        since_h = max(
            (datetime.now(timezone.utc) - parse_dt(last_used_at)).total_seconds() / 3600, 1e-3
        )
        base += 0.5 * math.exp(-since_h / 168)  # gentle one-week recency boost
    return base


def claim_scores(doc: dict) -> dict:
    """All derived scores for a Claim document."""
    c, x = doc.get("confirms", 0), doc.get("contradicts", 0)
    return {
        "credence": round(credence(c, x), 4),
        "uncertainty": round(uncertainty(c, x), 4),
        "activation": round(
            activation(doc.get("use_count", 0), doc["created_at"], doc.get("last_used_at")), 4
        ),
    }


def rank_score(doc: dict, relevance: float = 0.0) -> float:
    """Combined retrieval ranking: relevance + credence + squashed activation."""
    s = claim_scores(doc)
    act01 = 1 / (1 + math.exp(-s["activation"]))
    return 1.5 * relevance + 1.0 * s["credence"] + 0.5 * act01


def review_priority(doc: dict) -> float:
    """What's most worth asking the human about: uncertain AND active."""
    s = claim_scores(doc)
    act01 = 1 / (1 + math.exp(-s["activation"]))
    return s["uncertainty"] * act01


def similarity(a: str, b: str) -> float:
    """Cheap normalized string similarity (token Dice + prefix bonus) for
    vocabulary resistance. Good enough to catch works_for/works_at,
    person/people; an embedding upgrade slots in here later."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 1.0
    ta, tb = set(_bigrams(a)), set(_bigrams(b))
    if not ta or not tb:
        return 0.0
    dice = 2 * len(ta & tb) / (len(ta) + len(tb))
    if a.split("_")[0] == b.split("_")[0]:
        dice = min(1.0, dice + 0.2)
    return dice


def _bigrams(s: str) -> list[str]:
    s = s.replace("_", " ")
    return [s[i : i + 2] for i in range(len(s) - 1)]

"""Agent-facing tool surface (hermes / OpenAI function-calling format).

Usage in a hermes agent:

    from terminus_mind import Mind
    from terminus_mind.tools import TOOL_SPECS, dispatch

    mind = Mind(agent="hermes")
    # register TOOL_SPECS with the model; on a tool call:
    result = dispatch(mind, call.name, call.arguments)

Every result is JSON-serializable. NoveltyResisted is returned as a normal
result with `resisted: true` and suggestions, so the model can adjudicate
(reuse a suggested term, or retry with force=true) instead of crashing.
"""

from __future__ import annotations

import json
from typing import Any

from .mind import Mind, NoveltyResisted

TOOL_SPECS: list[dict] = [
    {
        "name": "memory_observe",
        "description": (
            "Record a raw interaction episode in long-term memory (lossless, append-only). "
            "Call once per meaningful exchange, then extract claims from it with memory_assert. "
            "Returns the episode id for use as provenance."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Raw text of the interaction or observation."},
                "source": {"type": "string", "enum": ["human", "agent", "document"], "default": "human"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "memory_recall",
        "description": (
            "Retrieve beliefs from long-term memory, ranked by relevance, credence "
            "(how proven) and activation (how needed). Each result carries _scores "
            "{credence, uncertainty, activation}. Treat status=candidate beliefs as "
            "hypotheses to hedge or verify with the user; status=confirmed are trusted. "
            "ALWAYS recall before asserting, to decide between new/confirm/supersede."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search over belief statements."},
                "subject": {"type": "string", "description": "Filter: entity name the belief is about."},
                "predicate": {"type": "string", "description": "Filter: relation name, e.g. works_at."},
                "include_expired": {"type": "boolean", "default": False,
                                    "description": "Include superseded/retired beliefs (history)."},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "memory_assert",
        "description": (
            "Store a NEW belief (subject-predicate-object triple plus a natural sentence). "
            "Use only after memory_recall shows no equivalent belief; otherwise use "
            "memory_confirm or memory_supersede. New beliefs start as unproven candidates. "
            "The vocabulary is conservative: a predicate or entity similar to an existing "
            "one is resisted with suggestions - prefer reusing the suggestion; pass "
            "force=true only when it is genuinely a different concept."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Entity name, e.g. 'Ivan'."},
                "predicate": {"type": "string", "description": "snake_case relation, e.g. works_at."},
                "object": {"type": "string", "description": "Target entity name (for entity-valued relations)."},
                "value": {"type": "string", "description": "Literal value (use instead of object for strings/numbers/dates)."},
                "fact_text": {"type": "string", "description": "The belief as one natural sentence."},
                "episode": {"type": "string", "description": "Episode id this was learned from (provenance)."},
                "by_human": {"type": "boolean", "default": False,
                             "description": "True if the human stated this directly (high-weight evidence)."},
                "force": {"type": "boolean", "default": False,
                          "description": "Override vocabulary resistance for genuinely new terms."},
            },
            "required": ["subject", "predicate"],
        },
    },
    {
        "name": "memory_confirm",
        "description": (
            "Reinforce an existing belief that new evidence supports (instead of asserting "
            "a duplicate). Human confirmation weighs 3x. This is how beliefs get proven over time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string"},
                "episode": {"type": "string", "description": "Episode id of the supporting evidence."},
                "by_human": {"type": "boolean", "default": False},
            },
            "required": ["claim_id"],
        },
    },
    {
        "name": "memory_contradict",
        "description": (
            "Record evidence AGAINST an existing belief when it is genuinely disputed but "
            "not clearly replaced (the conflict surfaces for human review). If the world "
            "simply changed or the human corrected you, use memory_supersede instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string"},
                "episode": {"type": "string"},
                "by_human": {"type": "boolean", "default": False},
            },
            "required": ["claim_id"],
        },
    },
    {
        "name": "memory_supersede",
        "description": (
            "Replace a belief because the world changed or it was wrong: the old belief is "
            "closed (never deleted, stays queryable as history) and linked to its replacement. "
            "Set by_human=true and pin=true when the human explicitly corrected it - pinned "
            "beliefs can only be changed by another human statement."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "old_claim_id": {"type": "string"},
                "object": {"type": "string", "description": "New target entity name."},
                "value": {"type": "string", "description": "New literal value."},
                "fact_text": {"type": "string", "description": "The new belief as one sentence."},
                "episode": {"type": "string"},
                "by_human": {"type": "boolean", "default": False},
                "pin": {"type": "boolean", "default": False},
            },
            "required": ["old_claim_id"],
        },
    },
    {
        "name": "memory_about",
        "description": (
            "Everything currently believed about one entity: its profile plus outgoing and "
            "incoming beliefs with scores. Use to build context about a person/thing."
        ),
        "parameters": {
            "type": "object",
            "properties": {"entity": {"type": "string", "description": "Entity name or alias."}},
            "required": ["entity"],
        },
    },
    {
        "name": "memory_review",
        "description": (
            "The beliefs most worth verifying with the human right now (most uncertain x most "
            "used), plus any unresolved conflicts. Weave one of these into conversation as a "
            "natural question when an opening arises - this is how the world model gets proven."
        ),
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 5}},
        },
    },
]


def dispatch(mind: Mind, name: str, arguments: dict | str) -> dict:
    """Execute a tool call against a Mind. Returns a JSON-serializable dict."""
    args: dict[str, Any] = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
    try:
        if name == "memory_observe":
            return {"episode_id": mind.observe(args["content"], source=args.get("source", "human"))}
        if name == "memory_recall":
            return {"claims": mind.recall(
                query=args.get("query"), subject=args.get("subject"),
                predicate=args.get("predicate"),
                include_expired=args.get("include_expired", False),
                limit=args.get("limit", 10))}
        if name == "memory_assert":
            return {"claim_id": mind.assert_claim(
                args["subject"], args["predicate"],
                object=args.get("object"), value=args.get("value"),
                fact_text=args.get("fact_text"), episode=args.get("episode"),
                by_human=args.get("by_human", False), force=args.get("force", False))}
        if name == "memory_confirm":
            c = mind.confirm(args["claim_id"], episode=args.get("episode"),
                             by_human=args.get("by_human", False))
            return {"claim_id": c["@id"], "confirms": c["confirms"]}
        if name == "memory_contradict":
            c = mind.contradict(args["claim_id"], episode=args.get("episode"),
                                by_human=args.get("by_human", False))
            return {"claim_id": c["@id"], "contradicts": c["contradicts"]}
        if name == "memory_supersede":
            return {"new_claim_id": mind.supersede(
                args["old_claim_id"], object=args.get("object"), value=args.get("value"),
                fact_text=args.get("fact_text"), episode=args.get("episode"),
                by_human=args.get("by_human", False), pin=args.get("pin", False))}
        if name == "memory_about":
            return mind.about(args["entity"])
        if name == "memory_review":
            return {"review_queue": mind.review_queue(limit=args.get("limit", 5)),
                    "conflicts": mind.conflicts()}
        raise KeyError(f"unknown tool {name}")
    except NoveltyResisted as e:
        return {"resisted": True, "kind": e.kind, "name": e.name,
                "suggestions": e.suggestions, "hint": str(e)}

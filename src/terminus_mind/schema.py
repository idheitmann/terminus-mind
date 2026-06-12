"""The TerminusDB schema for terminus-mind.

This is the *storage* schema — deliberately small and stable. The learned
ontology (predicates, entity types) lives in VocabTerm instance documents,
not here; see DESIGN.md "Conservative ontology uptake".

Schema changes must be weakening (new classes, new Optional/Set properties):
TerminusDB rejects strengthening changes against live data with witnesses,
which is exactly the conservatism we want.
"""

from __future__ import annotations

from .client import TerminusClient

SCHEMA_VERSION = 1

CONTEXT = {
    "@type": "@context",
    "@base": "terminusdb:///data/",
    "@schema": "terminusdb:///schema#",
    "@documentation": {
        "@title": "terminus-mind",
        "@description": "Self-evolving agent memory: beliefs proven over time through human interaction.",
    },
}

SCHEMA: list[dict] = [
    {"@type": "Enum", "@id": "ClaimStatus", "@value": ["candidate", "confirmed", "retired"]},
    {"@type": "Enum", "@id": "EpisodeSource", "@value": ["human", "agent", "document"]},
    {"@type": "Enum", "@id": "TermStatus", "@value": ["provisional", "established", "deprecated"]},
    {"@type": "Enum", "@id": "TermKind", "@value": ["predicate", "entity_type"]},
    {
        "@type": "Class",
        "@id": "Episode",
        "@key": {"@type": "Random"},
        "@documentation": {"@comment": "Lossless episodic tier. Append-only raw interaction record."},
        "content": "xsd:string",
        "occurred_at": "xsd:dateTime",
        "source": "EpisodeSource",
        "consolidated": "xsd:boolean",
    },
    {
        "@type": "Class",
        "@id": "Entity",
        "@key": {"@type": "Lexical", "@fields": ["name"]},
        "@documentation": {"@comment": "Semantic node. Provisional until it participates in a confirmed claim."},
        "name": "xsd:string",
        "entity_type": {"@type": "Optional", "@class": "xsd:string"},
        "aliases": {"@type": "Set", "@class": "xsd:string"},
        "summary": {"@type": "Optional", "@class": "xsd:string"},
        "status": "TermStatus",
        "created_at": "xsd:dateTime",
    },
    {
        "@type": "Class",
        "@id": "Claim",
        "@key": {"@type": "Random"},
        "@documentation": {
            "@comment": "Reified belief edge: bi-temporal validity, evidence counters, lifecycle status, provenance."
        },
        "subject": "Entity",
        "predicate": "xsd:string",
        "object_entity": {"@type": "Optional", "@class": "Entity"},
        "object_value": {"@type": "Optional", "@class": "xsd:string"},
        "fact_text": "xsd:string",
        # event time: when this was true in the world
        "valid_at": {"@type": "Optional", "@class": "xsd:dateTime"},
        "invalid_at": {"@type": "Optional", "@class": "xsd:dateTime"},
        # transaction time: when the system learned / superseded it
        "created_at": "xsd:dateTime",
        "expired_at": {"@type": "Optional", "@class": "xsd:dateTime"},
        "superseded_by": {"@type": "Optional", "@class": "Claim"},
        # credence evidence (beta counters)
        "confirms": "xsd:integer",
        "contradicts": "xsd:integer",
        "human_confirms": "xsd:integer",
        "status": "ClaimStatus",
        "pinned": "xsd:boolean",
        # activation (ACT-R optimized-learning fields)
        "use_count": "xsd:integer",
        "last_used_at": {"@type": "Optional", "@class": "xsd:dateTime"},
        # provenance
        "evidence": {"@type": "Set", "@class": "Episode"},
    },
    {
        "@type": "Class",
        "@id": "VocabTerm",
        "@key": {"@type": "Lexical", "@fields": ["kind", "name"]},
        "@documentation": {
            "@comment": "The learned ontology: one term per predicate / entity type, with its own uptake lifecycle."
        },
        "kind": "TermKind",
        "name": "xsd:string",
        "status": "TermStatus",
        "usage_count": "xsd:integer",
        "ratified": "xsd:boolean",
        "canonical": {"@type": "Optional", "@class": "VocabTerm"},
        "first_seen": "xsd:dateTime",
    },
]


def bootstrap(client: TerminusClient, *, author: str = "terminus-mind") -> bool:
    """Create the database (if absent) and sync the schema. Returns True if
    anything was created/changed."""
    created = False
    if not client.db_exists():
        client.create_db(
            label=client.db,
            comment="terminus-mind agent memory (schema v%d)" % SCHEMA_VERSION,
        )
        created = True
    existing = {
        d["@id"]
        for d in client.list_docs(graph_type="schema")
        if d.get("@type") in ("Class", "Enum")
    }
    wanted = {d["@id"] for d in SCHEMA}
    if created or existing != wanted:
        # full replace is safe: weakening-only by policy, and TerminusDB
        # rejects anything that breaks live instance data.
        client._request(
            "POST",
            f"/api/document/{client._ref}",
            params={
                "graph_type": "schema",
                "author": author,
                "message": f"sync schema v{SCHEMA_VERSION}",
                "full_replace": "true",
            },
            json=[CONTEXT, *SCHEMA],
        )
        return True
    return False

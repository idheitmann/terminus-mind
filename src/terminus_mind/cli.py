"""tm - operate and introspect a terminus-mind memory.

Connection via env: TM_SERVER, TM_TEAM, TM_DB, TM_USER, TM_PASS
(defaults: http://127.0.0.1:6363, admin, mind, admin, root).
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import TerminusClient
from .mind import Mind, NoveltyResisted


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _claim_line(c: dict) -> str:
    s = c.get("_scores", {})
    flags = "".join(
        f
        for f, on in [
            ("P", c.get("pinned")),
            ("X", bool(c.get("expired_at"))),
            ("!", c.get("contradicts", 0) > 0),
        ]
        if on
    )
    return (
        f"{c['@id']:<46} [{c['status']:<9}{' ' + flags if flags else ''}] "
        f"cred={s.get('credence', '?'):<6} unc={s.get('uncertainty', '?'):<6} "
        f"{c['fact_text']}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tm", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: env TM_DB or 'mind')")
    p.add_argument("--agent", default="cli", help="author recorded on commits")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create database and schema")
    sub.add_parser("stats", help="memory overview")

    sp = sub.add_parser("observe", help="record an episode")
    sp.add_argument("content")
    sp.add_argument("--source", default="human", choices=["human", "agent", "document"])

    sp = sub.add_parser("assert", help="assert a new belief (human-sourced by default)")
    sp.add_argument("subject")
    sp.add_argument("predicate")
    sp.add_argument("object_or_value")
    sp.add_argument("--entity", action="store_true",
                    help="object is an entity (default: literal value)")
    sp.add_argument("--fact", help="natural-sentence form")
    sp.add_argument("--episode", help="provenance episode id")
    sp.add_argument("--agent-sourced", action="store_true")
    sp.add_argument("--force", action="store_true", help="override vocabulary resistance")

    sp = sub.add_parser("recall", help="search beliefs")
    sp.add_argument("query", nargs="?")
    sp.add_argument("--subject")
    sp.add_argument("--predicate")
    sp.add_argument("--all", action="store_true", help="include superseded/retired")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--no-touch", action="store_true", help="don't count as a use")

    sp = sub.add_parser("about", help="everything believed about an entity")
    sp.add_argument("entity")

    for name, help_ in [("confirm", "reinforce a belief"), ("contradict", "evidence against a belief")]:
        sp = sub.add_parser(name, help=help_ + " (human-sourced by default)")
        sp.add_argument("claim_id")
        sp.add_argument("--episode")
        sp.add_argument("--agent-sourced", action="store_true")

    sp = sub.add_parser("correct", help="human correction: supersede + pin")
    sp.add_argument("claim_id")
    sp.add_argument("--object", help="new target entity")
    sp.add_argument("--value", help="new literal value")
    sp.add_argument("--fact", help="new sentence")
    sp.add_argument("--episode")

    sub.add_parser("review", help="beliefs most worth verifying with the human")
    sub.add_parser("conflicts", help="unresolved contradicted beliefs")
    sub.add_parser("consolidate", help="run the deterministic sleep pass")

    sp = sub.add_parser("vocab", help="the learned ontology")
    sp.add_argument("--kind", choices=["predicate", "entity_type"])
    sp.add_argument("--status", choices=["provisional", "established", "deprecated"])

    sp = sub.add_parser("ratify", help="accept a provisional term into the ontology")
    sp.add_argument("kind", choices=["predicate", "entity_type"])
    sp.add_argument("name")

    sp = sub.add_parser("merge-term", help="merge a term into another (rewrites claims)")
    sp.add_argument("kind", choices=["predicate", "entity_type"])
    sp.add_argument("name")
    sp.add_argument("into")

    sp = sub.add_parser("journal", help="friction reports about the memory system itself")
    sp.add_argument("--agent", dest="journal_agent", help="filter to one agent's journal")
    sp.add_argument("--tail", type=int, help="show the last N raw entries instead of the summary")

    sp = sub.add_parser("log", help="commit timeline (every memory change)")
    sp.add_argument("-n", type=int, default=20)

    sp = sub.add_parser("history", help="full life of one belief")
    sp.add_argument("claim_id")

    args = p.parse_args(argv)
    client = TerminusClient(db=args.db) if args.db else TerminusClient()
    mind = Mind(client, agent=args.agent)

    try:
        out = _run(mind, args)
    except NoveltyResisted as e:
        print(f"resisted: {e}", file=sys.stderr)
        return 2
    if args.json or not isinstance(out, str):
        _print(out)
    else:
        print(out)
    return 0


def _run(mind: Mind, args):  # noqa: C901
    if args.cmd == "init":
        changed = mind.init()
        return {"db": mind.client.db, "changed": changed}
    if args.cmd == "stats":
        return mind.stats()
    if args.cmd == "observe":
        return {"episode_id": mind.observe(args.content, source=args.source)}
    if args.cmd == "assert":
        kw = dict(fact_text=args.fact, episode=args.episode,
                  by_human=not args.agent_sourced, force=args.force)
        if args.entity:
            kw["object"] = args.object_or_value
        else:
            kw["value"] = args.object_or_value
        return {"claim_id": mind.assert_claim(args.subject, args.predicate, **kw)}
    if args.cmd == "recall":
        claims = mind.recall(query=args.query, subject=args.subject, predicate=args.predicate,
                             include_expired=args.all, limit=args.limit, touch=not args.no_touch)
        if args.json:
            return claims
        return "\n".join(_claim_line(c) for c in claims) or "(nothing recalled)"
    if args.cmd == "about":
        info = mind.about(args.entity)
        if args.json or not info["entity"]:
            return info
        e = info["entity"]
        lines = [f"{e['name']} [{e['status']}] type={e.get('entity_type', '?')} aliases={e.get('aliases', [])}"]
        lines += ["  out: " + _claim_line(c) for c in info["outgoing"]]
        lines += ["  in:  " + _claim_line(c) for c in info["incoming"]]
        return "\n".join(lines)
    if args.cmd == "confirm":
        c = mind.confirm(args.claim_id, episode=args.episode, by_human=not args.agent_sourced)
        return {"claim_id": c["@id"], "confirms": c["confirms"], "human_confirms": c["human_confirms"]}
    if args.cmd == "contradict":
        c = mind.contradict(args.claim_id, episode=args.episode, by_human=not args.agent_sourced)
        return {"claim_id": c["@id"], "contradicts": c["contradicts"]}
    if args.cmd == "correct":
        return {"new_claim_id": mind.correct(
            args.claim_id, object=args.object, value=args.value,
            fact_text=args.fact, episode=args.episode)}
    if args.cmd == "review":
        q = mind.review_queue()
        if args.json:
            return q
        return "\n".join(f"{c['_priority']:.3f} {_claim_line(c)}" for c in q) or "(nothing to review)"
    if args.cmd == "conflicts":
        return mind.conflicts()
    if args.cmd == "consolidate":
        return mind.consolidate()
    if args.cmd == "vocab":
        terms = mind.vocab(kind=args.kind, status=args.status)
        if args.json:
            return terms
        return "\n".join(
            f"{t['kind']:<12} {t['name']:<28} [{t['status']}] uses={t.get('usage_count', 0)}"
            for t in sorted(terms, key=lambda t: (t["kind"], -t.get("usage_count", 0)))
        ) or "(empty vocabulary)"
    if args.cmd == "ratify":
        return mind.ratify_term(args.kind, args.name)
    if args.cmd == "merge-term":
        return {"claims_rewritten": mind.merge_term(args.kind, args.name, args.into)}
    if args.cmd == "journal":
        from .journal import read_entries, summarize

        entries = read_entries(agent=args.journal_agent)
        if args.tail:
            return entries[-args.tail:]
        return summarize(entries)
    if args.cmd == "log":
        return mind.timeline(count=args.n)
    if args.cmd == "history":
        return mind.claim_history(args.claim_id)
    raise SystemExit(f"unhandled command {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())

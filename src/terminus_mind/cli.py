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


def _getkey() -> str:
    """Read one keypress without requiring Enter. Falls back to input() if not a TTY."""
    if not sys.stdin.isatty():
        return input().strip()[:1].lower()
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1).lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _spo(c: dict) -> str:
    """Compact subject → predicate → object/value triple for a claim."""
    from urllib.parse import unquote
    subj = unquote((c.get("subject") or "").removeprefix("Entity/"))
    pred = c.get("predicate", "?")
    if c.get("object_entity"):
        obj = unquote(c["object_entity"].removeprefix("Entity/"))
    elif c.get("object_value") is not None:
        obj = f'"{c["object_value"]}"'
    else:
        obj = "?"
    return f"{subj}  {pred}  {obj}"


def _print_claim(c: dict, detailed: bool = False) -> None:
    cid = c.get("@id", "")
    fact = c.get("fact_text", "")
    if detailed:
        print(f"         \033[2m{_spo(c)}\033[0m")
        print(f"         {fact}")
        print(f"         \033[2m{cid}  c={c.get('confirms',0)} hc={c.get('human_confirms',0)}\033[0m")
    else:
        print(f"         {fact[:52]:<52}  \033[2m{cid}\033[0m")


def _fetch_examples(mind: Mind, tkind: str, name: str, limit: int) -> list[dict]:
    try:
        if tkind == "predicate":
            return mind.recall(predicate=name, touch=False, limit=limit)
        else:
            by_subj = mind.recall(subject=name, touch=False, limit=limit // 2)
            by_query = mind.recall(query=name, touch=False, limit=limit // 2)
            seen, out = set(), []
            for c in by_subj + by_query:
                if c["@id"] not in seen:
                    seen.add(c["@id"])
                    out.append(c)
            return out[:limit]
    except Exception:
        return []


def _curate(mind: Mind, kind: str | None = None, min_uses: int = 1) -> None:
    """Interactive one-key vocab curation session."""
    terms = [
        t for t in mind.vocab(kind=kind, status="provisional")
        if t.get("usage_count", 0) >= min_uses
    ]
    terms.sort(key=lambda t: (-t.get("usage_count", 0), t["kind"], t["name"]))

    if not terms:
        print("Nothing to curate — no provisional terms meet the threshold.")
        return

    ratified = merged = renamed = skipped = 0
    total = len(terms)

    print(f"\n{'─'*60}")
    print(f"  vocab curation  ({total} provisional terms)")
    print(f"  y=ratify  r=rename  m=merge  d=details  s=skip  q=quit")
    print(f"{'─'*60}\n")

    quit_requested = False
    for i, t in enumerate(terms, 1):
        if quit_requested:
            break
        name, tkind, uses = t["name"], t["kind"], t.get("usage_count", 0)
        examples = _fetch_examples(mind, tkind, name, limit=3)

        print(f"[{i}/{total}]  {tkind}  \033[1m{name}\033[0m  (×{uses} uses)")
        for ex in examples:
            _print_claim(ex, detailed=False)

        print("  y / r / m / d / s / q  > ", end="", flush=True)
        key = _getkey()
        print(key)

        if key == "d":
            # expand: fetch more claims, show SPO + sentence + episode snippet
            examples = _fetch_examples(mind, tkind, name, limit=8)
            for ex in examples:
                for ep_id in (ex.get("evidence") or [])[:1]:
                    try:
                        ep = mind.client.get(ep_id)
                        snippet = ep.get("content", "")[:110].replace("\n", " ")
                        src = ep.get("source", "")
                        ex["_ep_snippet"] = f"[{src}] {snippet}…"
                    except Exception:
                        pass
            print()
            print(f"[{i}/{total}]  {tkind}  \033[1m{name}\033[0m  (×{uses} uses)  — details")
            for ex in examples:
                _print_claim(ex, detailed=True)
                if ex.get("_ep_snippet"):
                    print(f"         \033[2m  {ex['_ep_snippet']}\033[0m")
            print()
            print("  y / r / m / s / q  > ", end="", flush=True)
            key = _getkey()
            print(key)

        if key == "q":
            quit_requested = True
        elif key == "y":
            mind.ratify_term(tkind, name)
            print("  ✓ ratified\n")
            ratified += 1
        elif key == "r":
            new_name = input("  rename to (new canonical name): ").strip()
            if new_name:
                try:
                    canonical = mind._gate(tkind, new_name, force=True)
                    mind.ratify_term(tkind, canonical)
                    n = mind.merge_term(tkind, name, canonical)
                    print(f"  ✓ renamed → {canonical}  ({n} claims rewritten)\n")
                    renamed += 1
                except Exception as e:
                    print(f"  ✗ {e}\n")
            else:
                print("  skipped (no name given)\n")
                skipped += 1
        elif key == "m":
            others = [
                t2 for t2 in mind.vocab(kind=tkind)
                if t2["name"] != name and t2["status"] != "deprecated"
            ]
            if others:
                from . import scoring
                close = sorted(others,
                               key=lambda t2: -scoring.similarity(name, t2["name"]))[:6]
                print("  similar terms: " +
                      ", ".join(f"{t2['name']} ({t2['status']})" for t2 in close))
            target = input("  merge into (name): ").strip()
            if target:
                try:
                    n = mind.merge_term(tkind, name, target)
                    print(f"  ✓ merged → {target}  ({n} claims rewritten)\n")
                    merged += 1
                except Exception as e:
                    print(f"  ✗ {e}\n")
            else:
                print("  skipped (no target given)\n")
                skipped += 1
        else:  # s or anything else
            print("  – skipped\n")
            skipped += 1

    print(f"{'─'*60}")
    print(f"  done: {ratified} ratified, {renamed} renamed, {merged} merged, {skipped} skipped")
    print(f"  wrong claims? copy the Claim/… id and run: tm contradict <id>")
    print(f"{'─'*60}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tm", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: env TM_DB or 'mind')")
    p.add_argument("--agent", default="cli", help="author recorded on commits")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create database and schema")
    sub.add_parser("stats", help="memory overview")
    sub.add_parser("doctor", help="health-check every layer")

    sp = sub.add_parser("dump", help="export the full world model as JSONL")
    sp.add_argument("-o", "--output", help="file path (default: stdout)")

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

    sub.add_parser("reindex", help="rebuild the embedding index from the database")
    sub.add_parser("review", help="beliefs most worth verifying with the human")
    sub.add_parser("conflicts", help="unresolved contradicted beliefs")
    sub.add_parser("consolidate", help="run the deterministic lifecycle pass")

    sp = sub.add_parser("sleep", help="full sleep cycle: distill episodes (local LLM) + consolidate")
    sp.add_argument("--review", action="store_true",
                    help="leave the branch unmerged for human inspection")
    sp.add_argument("--limit", type=int, default=20, help="max episodes to distill")

    sp = sub.add_parser("vocab", help="the learned ontology")
    sp.add_argument("--kind", choices=["predicate", "entity_type"])
    sp.add_argument("--status", choices=["provisional", "established", "deprecated"])

    sp = sub.add_parser("ratify", help="accept a provisional term into the ontology")
    sp.add_argument("kind", choices=["predicate", "entity_type"])
    sp.add_argument("name")

    sp = sub.add_parser("curate", help="interactive one-key vocab curation session")
    sp.add_argument("--kind", choices=["predicate", "entity_type"], help="limit to one kind")
    sp.add_argument("--min-uses", type=int, default=1, metavar="N",
                    help="skip terms used fewer than N times (default 1)")

    sp = sub.add_parser("merge-term", help="merge a term into another (rewrites claims)")
    sp.add_argument("kind", choices=["predicate", "entity_type"])
    sp.add_argument("name")
    sp.add_argument("into")

    sp = sub.add_parser("journal", help="friction reports about the memory system itself")
    sp.add_argument("--agent", dest="journal_agent", help="filter to one agent's journal")
    sp.add_argument("--tail", type=int, help="show the last N raw entries instead of the summary")
    sp.add_argument("--archive", action="store_true",
                    help="move active entries to journal/archive/ (after triage)")
    sp.add_argument("--include-archived", action="store_true",
                    help="include already-triaged entries")

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
    if out is None:
        return 0
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
    if args.cmd == "doctor":
        return mind.doctor()
    if args.cmd == "dump":
        lines = "\n".join(json.dumps(rec, default=str) for rec in mind.dump())
        if args.output:
            with open(args.output, "w") as f:
                f.write(lines + "\n")
            return {"written": args.output, "records": lines.count("\n") + 1}
        return lines
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
    if args.cmd == "reindex":
        return mind.reindex()
    if args.cmd == "review":
        q = mind.review_queue()
        if args.json:
            return q
        return "\n".join(f"{c['_priority']:.3f} {_claim_line(c)}" for c in q) or "(nothing to review)"
    if args.cmd == "conflicts":
        return mind.conflicts()
    if args.cmd == "consolidate":
        return mind.consolidate()
    if args.cmd == "sleep":
        from .sleep import run_sleep

        return run_sleep(mind, limit=args.limit, merge=not args.review)
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
    if args.cmd == "curate":
        _curate(mind, kind=args.kind, min_uses=args.min_uses)
        return None
    if args.cmd == "merge-term":
        return {"claims_rewritten": mind.merge_term(args.kind, args.name, args.into)}
    if args.cmd == "journal":
        from .journal import archive_entries, read_entries, summarize

        if args.archive:
            return {"archived": archive_entries(agent=args.journal_agent)}
        entries = read_entries(agent=args.journal_agent,
                               include_archived=args.include_archived)
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

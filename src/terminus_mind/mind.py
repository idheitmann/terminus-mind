"""The Mind: belief-level operations over the TerminusDB store.

Intelligence (extraction, entity resolution, adjudication) lives in the
calling agent. This layer provides the primitives, the conservative
vocabulary gate, scoring, lifecycle, and introspection.
"""

from __future__ import annotations

import re
from typing import Any

from . import scoring
from .client import TerminusClient
from .schema import bootstrap


class NoveltyResisted(Exception):
    """A novel term/entity was close to existing vocabulary. Reuse one of
    the suggestions, or repeat the call with force=True."""

    def __init__(self, kind: str, name: str, suggestions: list[dict]):
        self.kind, self.name, self.suggestions = kind, name, suggestions
        opts = ", ".join(f"{s['name']} ({s['similarity']:.2f}, {s['status']})" for s in suggestions)
        super().__init__(
            f"novel {kind} '{name}' resisted - did you mean: {opts}? "
            f"Reuse an existing term or pass force=True to register it as provisional."
        )


def _norm(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", term.strip().lower()).strip("_")


class Mind:
    def __init__(self, client: TerminusClient | None = None, agent: str = "agent"):
        self.client = client or TerminusClient()
        self.agent = agent

    def init(self) -> bool:
        return bootstrap(self.client, author=self.agent)

    # -- episodes (lossless, append-only) --------------------------------

    def observe(self, content: str, source: str = "human", occurred_at: str | None = None) -> str:
        ids = self.client.insert(
            {
                "@type": "Episode",
                "content": content,
                "occurred_at": occurred_at or scoring.now(),
                "source": source,
                "consolidated": False,
            },
            author=self.agent,
            message=f"observe ({source}): {content[:60]}",
        )
        return ids[0]

    def unconsolidated_episodes(self) -> list[dict]:
        return self.client.query_template("Episode", {"consolidated": False})

    def mark_consolidated(self, episode_id: str) -> None:
        doc = self.client.get(episode_id)
        if doc:
            doc["consolidated"] = True
            self.client.replace(doc, author=self.agent, message=f"consolidated {episode_id}")

    # -- vocabulary: the learned, conservative ontology -------------------

    def vocab(self, kind: str | None = None, status: str | None = None) -> list[dict]:
        template: dict = {}
        if kind:
            template["kind"] = kind
        if status:
            template["status"] = status
        if template:
            return self.client.query_template("VocabTerm", template)
        return self.client.list_docs("VocabTerm")

    def _gate(self, kind: str, raw: str, force: bool) -> str:
        """Conservative uptake gate. Returns the canonical term to use.

        Known term -> use it (following canonical pointers off deprecated
        terms). Novel term similar to existing vocabulary -> NoveltyResisted
        unless forced. Truly novel or forced -> registered provisional.
        """
        name = _norm(raw)
        terms = {t["name"]: t for t in self.vocab(kind=kind)}
        if name in terms:
            t = terms[name]
            if t["status"] == "deprecated" and t.get("canonical"):
                target = self.client.get(t["canonical"])
                name, t = target["name"], target
            t["usage_count"] = t.get("usage_count", 0) + 1
            self.client.replace(t, author=self.agent, message=f"vocab use: {kind} {name}")
            return name
        similar = sorted(
            (
                {"name": t["name"], "status": t["status"],
                 "similarity": scoring.similarity(name, t["name"])}
                for t in terms.values()
                if t["status"] != "deprecated"
            ),
            key=lambda s: -s["similarity"],
        )
        close = [s for s in similar if s["similarity"] >= scoring.SIMILARITY_GATE]
        if close and not force:
            raise NoveltyResisted(kind, name, close[:3])
        self.client.insert(
            {
                "@type": "VocabTerm",
                "kind": kind,
                "name": name,
                "status": "provisional",
                "usage_count": 1,
                "ratified": False,
                "first_seen": scoring.now(),
            },
            author=self.agent,
            message=f"vocab new (provisional): {kind} {name}"
            + (" [forced past resistance]" if close else ""),
        )
        return name

    def ratify_term(self, kind: str, name: str) -> dict:
        """Human accepts a provisional term into the established ontology."""
        t = self._term(kind, _norm(name))
        t.update(status="established", ratified=True)
        self.client.replace(t, author=self.agent, message=f"vocab ratified: {kind} {name}")
        return t

    def merge_term(self, kind: str, name: str, into: str) -> int:
        """Human merges a term into another: deprecate + canonical pointer,
        and rewrite live claims so old vocabulary converges. Returns number
        of claims rewritten."""
        src, dst = self._term(kind, _norm(name)), self._term(kind, _norm(into))
        src.update(status="deprecated", canonical=dst["@id"])
        dst["usage_count"] = dst.get("usage_count", 0) + src.get("usage_count", 0)
        self.client.replace([src, dst], author=self.agent,
                            message=f"vocab merge: {kind} {src['name']} -> {dst['name']}")
        rewritten = 0
        if kind == "predicate":
            for c in self.client.query_template("Claim", {"predicate": src["name"]}):
                c["predicate"] = dst["name"]
                self.client.replace(c, author=self.agent,
                                    message=f"rewrite predicate {src['name']} -> {dst['name']}")
                rewritten += 1
        else:
            for e in self.client.query_template("Entity", {"entity_type": src["name"]}):
                e["entity_type"] = dst["name"]
                self.client.replace(e, author=self.agent,
                                    message=f"rewrite entity_type {src['name']} -> {dst['name']}")
                rewritten += 1
        return rewritten

    def _term(self, kind: str, name: str) -> dict:
        hits = self.client.query_template("VocabTerm", {"kind": kind, "name": name})
        if not hits:
            raise KeyError(f"no {kind} term '{name}'")
        return hits[0]

    # -- entities -----------------------------------------------------------

    def find_entity(self, name: str) -> dict | None:
        hits = self.client.query_template("Entity", {"name": name})
        if hits:
            return hits[0]
        for e in self.client.list_docs("Entity"):
            if name in e.get("aliases", []):
                return e
        return None

    def entity(
        self,
        name: str,
        entity_type: str | None = None,
        aliases: list[str] | None = None,
        force: bool = False,
    ) -> str:
        """Upsert an entity, with resistance against near-duplicate names."""
        existing = self.find_entity(name)
        if existing:
            changed = False
            if entity_type and not existing.get("entity_type"):
                existing["entity_type"] = self._gate("entity_type", entity_type, force)
                changed = True
            new_aliases = set(existing.get("aliases", [])) | set(aliases or [])
            if new_aliases != set(existing.get("aliases", [])):
                existing["aliases"] = sorted(new_aliases)
                changed = True
            if changed:
                self.client.replace(existing, author=self.agent, message=f"entity update: {name}")
            return existing["@id"]
        if not force:
            close = []
            for e in self.client.list_docs("Entity"):
                names = [e["name"], *e.get("aliases", [])]
                best = max(scoring.similarity(name, n) for n in names)
                if best >= scoring.SIMILARITY_GATE:
                    close.append({"name": e["name"], "status": e["status"], "similarity": best})
            if close:
                raise NoveltyResisted(
                    "entity", name, sorted(close, key=lambda s: -s["similarity"])[:3]
                )
        doc: dict[str, Any] = {
            "@type": "Entity",
            "name": name,
            "aliases": sorted(set(aliases or [])),
            "status": "provisional",
            "created_at": scoring.now(),
        }
        if entity_type:
            doc["entity_type"] = self._gate("entity_type", entity_type, force)
        return self.client.insert(
            doc, author=self.agent, message=f"entity new (provisional): {name}"
        )[0]

    # -- claims ---------------------------------------------------------------

    def assert_claim(
        self,
        subject: str,
        predicate: str,
        *,
        object: str | None = None,
        value: str | None = None,
        fact_text: str | None = None,
        episode: str | None = None,
        valid_at: str | None = None,
        by_human: bool = False,
        force: bool = False,
    ) -> str:
        """Insert a new belief as a candidate. subject/object are entity
        names (upserted, gated) or existing Entity/... ids."""
        if (object is None) == (value is None):
            raise ValueError("exactly one of object (entity) or value (literal) required")
        pred = self._gate("predicate", predicate, force)
        subj_id = subject if subject.startswith("Entity/") else self.entity(subject, force=force)
        doc: dict[str, Any] = {
            "@type": "Claim",
            "subject": subj_id,
            "predicate": pred,
            "fact_text": fact_text
            or f"{subject} {pred.replace('_', ' ')} {object or value}",
            "created_at": scoring.now(),
            "confirms": scoring.HUMAN_WEIGHT if by_human else 1,
            "contradicts": 0,
            "human_confirms": 1 if by_human else 0,
            "status": "candidate",
            "pinned": False,
            "use_count": 0,
            "evidence": [episode] if episode else [],
        }
        if object is not None:
            doc["object_entity"] = (
                object if object.startswith("Entity/") else self.entity(object, force=force)
            )
        else:
            doc["object_value"] = value
        if valid_at:
            doc["valid_at"] = valid_at
        return self.client.insert(
            doc, author=self.agent,
            message=f"claim new ({'human' if by_human else 'agent'}): {doc['fact_text'][:80]}",
        )[0]

    def confirm(self, claim_id: str, episode: str | None = None, by_human: bool = False) -> dict:
        c = self._claim(claim_id)
        c["confirms"] += scoring.HUMAN_WEIGHT if by_human else 1
        if by_human:
            c["human_confirms"] += 1
        if episode and episode not in c.get("evidence", []):
            c.setdefault("evidence", []).append(episode)
        self.client.replace(c, author=self.agent,
                            message=f"confirm ({'human' if by_human else 'agent'}): {c['fact_text'][:80]}")
        return c

    def contradict(self, claim_id: str, episode: str | None = None, by_human: bool = False) -> dict:
        c = self._claim(claim_id)
        c["contradicts"] += scoring.HUMAN_WEIGHT if by_human else 1
        if episode and episode not in c.get("evidence", []):
            c.setdefault("evidence", []).append(episode)
        self.client.replace(c, author=self.agent,
                            message=f"contradict ({'human' if by_human else 'agent'}): {c['fact_text'][:80]}")
        return c

    def supersede(
        self,
        old_claim_id: str,
        *,
        predicate: str | None = None,
        object: str | None = None,
        value: str | None = None,
        fact_text: str | None = None,
        episode: str | None = None,
        by_human: bool = False,
        pin: bool = False,
        force: bool = False,
    ) -> str:
        """The world changed or we were wrong: close the old claim's validity
        and link its replacement. Nothing is deleted."""
        old = self._claim(old_claim_id)
        if old.get("pinned") and not by_human:
            raise PermissionError(
                f"claim {old_claim_id} is pinned by a human; only a human statement may supersede it"
            )
        if object is None and value is None:
            object, value = old.get("object_entity"), old.get("object_value")
        new_id = self.assert_claim(
            old["subject"],
            predicate or old["predicate"],
            object=object,
            value=value,
            fact_text=fact_text,
            episode=episode,
            by_human=by_human,
            force=True,  # vocabulary was already gated on the old claim's predicate
        )
        if pin:
            new = self._claim(new_id)
            new["pinned"] = True
            self.client.replace(new, author=self.agent, message=f"pin: {new['fact_text'][:80]}")
        ts = scoring.now()
        old.update(invalid_at=ts, expired_at=ts, superseded_by=new_id, status="retired")
        self.client.replace(
            old, author=self.agent,
            message=f"supersede: '{old['fact_text'][:60]}' -> {new_id}",
        )
        return new_id

    def correct(self, old_claim_id: str, **kw: Any) -> str:
        """Human correction: authoritative supersession, pinned."""
        kw.setdefault("by_human", True)
        kw.setdefault("pin", True)
        return self.supersede(old_claim_id, **kw)

    def _claim(self, claim_id: str) -> dict:
        c = self.client.get(claim_id)
        if not c:
            raise KeyError(f"no claim {claim_id}")
        return c

    # -- retrieval ---------------------------------------------------------

    def recall(
        self,
        query: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        include_expired: bool = False,
        limit: int = 10,
        touch: bool = True,
    ) -> list[dict]:
        """Score-ranked belief retrieval. No LLM in this path. Recalled
        claims are 'touched' (use_count/last_used_at), feeding activation."""
        template: dict = {}
        if subject:
            ent = self.find_entity(subject)
            template["subject"] = ent["@id"] if ent else f"Entity/{subject}"
        if predicate:
            template["predicate"] = _norm(predicate)
        claims = (
            self.client.query_template("Claim", template)
            if template
            else self.client.list_docs("Claim")
        )
        if not include_expired:
            claims = [c for c in claims if not c.get("expired_at")]
        results = []
        terms = [t for t in re.split(r"\W+", query.lower()) if t] if query else []
        for c in claims:
            rel = 0.0
            if terms:
                text = c["fact_text"].lower()
                rel = sum(1 for t in terms if t in text) / len(terms)
                if rel == 0:
                    continue
            results.append((scoring.rank_score(c, rel), c))
        results.sort(key=lambda rc: -rc[0])
        top = [c for _, c in results[:limit]]
        if touch and top:
            ts = scoring.now()
            for c in top:
                c["use_count"] = c.get("use_count", 0) + 1
                c["last_used_at"] = ts
            self.client.replace(top, author=self.agent, message=f"recall touch x{len(top)}")
        return [dict(c, **{"_scores": scoring.claim_scores(c)}) for c in top]

    def about(self, entity_name: str, include_expired: bool = False) -> dict:
        """Everything believed about an entity: outgoing and incoming claims."""
        ent = self.find_entity(entity_name)
        if not ent:
            return {"entity": None, "outgoing": [], "incoming": []}
        out = self.client.query_template("Claim", {"subject": ent["@id"]})
        # template queries 500 on Optional link properties (TerminusDB v12),
        # so incoming edges go through WOQL instead
        bindings = self.client.woql(
            {
                "@type": "Triple",
                "subject": {"@type": "NodeValue", "variable": "C"},
                "predicate": {"@type": "NodeValue", "node": "@schema:object_entity"},
                "object": {"@type": "Value", "node": ent["@id"]},
            }
        )
        inc = [c for b in bindings if (c := self.client.get(b["C"]))]
        if not include_expired:
            out = [c for c in out if not c.get("expired_at")]
            inc = [c for c in inc if not c.get("expired_at")]
        annotate = lambda cs: [dict(c, **{"_scores": scoring.claim_scores(c)}) for c in cs]
        return {"entity": ent, "outgoing": annotate(out), "incoming": annotate(inc)}

    def conflicts(self) -> list[dict]:
        """Live claims with unresolved contradicting evidence — for the human."""
        out = []
        for c in self.client.list_docs("Claim"):
            if c.get("contradicts", 0) > 0 and c["status"] != "retired" and not c.get("expired_at"):
                out.append(dict(c, **{"_scores": scoring.claim_scores(c)}))
        return sorted(out, key=lambda c: -c["contradicts"])

    def review_queue(self, limit: int = 10) -> list[dict]:
        """Candidates most worth asking the human about: uncertain x active."""
        cands = [
            c
            for c in self.client.query_template("Claim", {"status": "candidate"})
            if not c.get("expired_at")
        ]
        cands.sort(key=lambda c: -scoring.review_priority(c))
        return [
            dict(c, **{"_scores": scoring.claim_scores(c),
                       "_priority": round(scoring.review_priority(c), 4)})
            for c in cands[:limit]
        ]

    # -- lifecycle ("sleep") -----------------------------------------------

    def consolidate(self) -> dict:
        """Deterministic consolidation pass: claim promotion/retirement,
        entity establishment, vocabulary nomination. LLM phases (episode
        distillation, reflection, dedup judging) belong to the agent."""
        report: dict[str, list] = {
            "promoted": [], "retired": [], "entities_established": [], "vocab_nominated": [],
        }
        confirmed_entities: set[str] = set()
        for c in self.client.list_docs("Claim"):
            if c.get("expired_at"):
                continue
            s = scoring.claim_scores(c)
            if (
                c["status"] == "candidate"
                and s["credence"] >= scoring.PROMOTE_CREDENCE
                and (c["human_confirms"] >= scoring.PROMOTE_HUMAN_CONFIRMS or c.get("pinned"))
            ):
                c["status"] = "confirmed"
                self.client.replace(c, author=self.agent,
                                    message=f"promote -> confirmed: {c['fact_text'][:70]}")
                report["promoted"].append(c["@id"])
            elif c["status"] != "retired" and s["credence"] < scoring.RETIRE_CREDENCE:
                c["status"] = "retired"
                ts = scoring.now()
                c.setdefault("invalid_at", ts)
                c["expired_at"] = ts
                self.client.replace(c, author=self.agent,
                                    message=f"retire (credence {s['credence']}): {c['fact_text'][:70]}")
                report["retired"].append(c["@id"])
            if c["status"] == "confirmed":
                confirmed_entities.add(c["subject"])
                if c.get("object_entity"):
                    confirmed_entities.add(c["object_entity"])
        for e in self.client.list_docs("Entity"):
            if e["status"] == "provisional" and e["@id"] in confirmed_entities:
                e["status"] = "established"
                self.client.replace(e, author=self.agent,
                                    message=f"entity established: {e['name']}")
                report["entities_established"].append(e["@id"])
        for t in self.vocab(status="provisional"):
            if t.get("usage_count", 0) >= scoring.VOCAB_NOMINATE_USES:
                report["vocab_nominated"].append(
                    {"kind": t["kind"], "name": t["name"], "usage_count": t["usage_count"]}
                )
        return report

    # -- introspection -------------------------------------------------------

    def stats(self) -> dict:
        claims = self.client.list_docs("Claim")
        live = [c for c in claims if not c.get("expired_at")]
        by_status: dict[str, int] = {}
        for c in claims:
            by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        return {
            "episodes": len(self.client.list_docs("Episode")),
            "entities": len(self.client.list_docs("Entity")),
            "claims": {"total": len(claims), "live": len(live), **by_status},
            "vocab": {
                k: len(self.vocab(status=k))
                for k in ("provisional", "established", "deprecated")
            },
            "conflicts": len(self.conflicts()),
            "commits": len(self.client.log(count=10_000)),
        }

    def timeline(self, count: int = 20) -> list[dict]:
        return [
            {"id": e["identifier"], "author": e["author"],
             "message": e["message"], "timestamp": e["timestamp"]}
            for e in self.client.log(count=count)
        ]

    def claim_history(self, claim_id: str) -> list[dict]:
        """Every commit that touched one belief, with the belief's state then."""
        out = []
        for h in self.client.history(claim_id):
            out.append(
                {"commit": h["identifier"], "message": h["message"],
                 "timestamp": h["timestamp"],
                 "state": self.client.get(claim_id, commit=h["identifier"])}
            )
        return out

    def believed_at(self, commit: str, type_: str = "Claim") -> list[dict]:
        """Time travel: the world model as of a past commit."""
        return self.client.list_docs(type_, commit=commit)

"""Integration tests against a live TerminusDB (podman, 127.0.0.1:6363).

Each test module run gets a fresh throwaway database.
"""

import uuid

import pytest

from terminus_mind import Mind, NoveltyResisted, TerminusClient


@pytest.fixture(scope="module")
def mind():
    client = TerminusClient(db=f"tm_test_{uuid.uuid4().hex[:8]}")
    m = Mind(client, agent="pytest")
    m.init()
    yield m
    client.delete_db()
    client.close()


def test_init_idempotent(mind):
    assert mind.init() is False  # second call: nothing to change


def test_observe_and_assert_with_provenance(mind):
    ep = mind.observe("Ivan said he works at Hyphae.", source="human")
    assert ep.startswith("Episode/")
    cid = mind.assert_claim(
        "Ivan", "works_at", object="Hyphae",
        fact_text="Ivan works at Hyphae.", episode=ep, by_human=True,
    )
    c = mind.client.get(cid)
    assert c["status"] == "candidate"
    assert c["confirms"] == 3  # human weight
    assert c["human_confirms"] == 1
    assert ep in c["evidence"]
    # entities were created provisional
    assert mind.find_entity("Ivan")["status"] == "provisional"


def test_vocabulary_resistance_and_uptake(mind):
    # 'works_for' is close to existing 'works_at' -> resisted
    with pytest.raises(NoveltyResisted) as exc:
        mind.assert_claim("Ada", "works_for", object="Hyphae", force=False)
    assert any(s["name"] == "works_at" for s in exc.value.suggestions)
    # genuinely new predicate passes the gate unforced
    cid = mind.assert_claim("Ada", "plays_chess_with", object="Ivan")
    assert cid.startswith("Claim/")
    terms = {t["name"]: t for t in mind.vocab(kind="predicate")}
    assert terms["plays_chess_with"]["status"] == "provisional"
    # forcing past resistance works and registers provisional
    mind.assert_claim("Ada", "works_for", object="Hyphae", force=True)
    assert {t["name"] for t in mind.vocab(kind="predicate", status="provisional")} >= {
        "works_at", "works_for", "plays_chess_with",
    }


def test_entity_resistance(mind):
    with pytest.raises(NoveltyResisted):
        mind.entity("Ivan H")  # near-duplicate of Ivan
    eid = mind.entity("Iván", force=True)
    assert eid.startswith("Entity/")


def test_merge_term_rewrites_claims(mind):
    rewritten = mind.merge_term("predicate", "works_for", "works_at")
    assert rewritten == 1
    # deprecated term now transparently rewrites to canonical on use
    cid = mind.assert_claim("Grace", "works_for", object="Hyphae", force=True)
    assert mind.client.get(cid)["predicate"] == "works_at"


def test_confirm_promote_lifecycle(mind):
    [claim] = mind.recall(subject="Ivan", predicate="works_at", touch=False)
    mind.confirm(claim["@id"], by_human=True)
    report = mind.consolidate()
    assert claim["@id"] in report["promoted"]
    c = mind.client.get(claim["@id"])
    assert c["status"] == "confirmed"
    # entities participating in a confirmed claim become established
    assert mind.find_entity("Ivan")["status"] == "established"


def test_supersede_keeps_history(mind):
    [old] = mind.recall(subject="Ivan", predicate="works_at", touch=False)
    new_id = mind.correct(old["@id"], object="Anthropic",
                          fact_text="Ivan works at Anthropic now.")
    old_doc = mind.client.get(old["@id"])
    assert old_doc["expired_at"] and old_doc["superseded_by"] == new_id
    assert mind.client.get(new_id)["pinned"] is True
    # default recall hides the expired claim; history view still has it
    live = mind.recall(subject="Ivan", predicate="works_at", touch=False)
    assert [c["@id"] for c in live] == [new_id]
    all_ = mind.recall(subject="Ivan", predicate="works_at",
                       include_expired=True, touch=False)
    assert {c["@id"] for c in all_} == {old["@id"], new_id}
    # pinned claims resist non-human supersession
    with pytest.raises(PermissionError):
        mind.supersede(new_id, object="Elsewhere")


def test_recall_ranking_and_touch(mind):
    before = mind.client.get(
        mind.recall(query="Anthropic", touch=False)[0]["@id"])["use_count"]
    hits = mind.recall(query="Ivan Anthropic")
    assert hits and "Anthropic" in hits[0]["fact_text"]
    after = mind.client.get(hits[0]["@id"])["use_count"]
    assert after == before + 1
    assert "_scores" in hits[0] and 0 < hits[0]["_scores"]["credence"] <= 1


def test_contradict_surfaces_conflict(mind):
    cid = mind.assert_claim("Ada", "lives_in", value="London",
                            fact_text="Ada lives in London.")
    mind.contradict(cid)
    assert cid in [c["@id"] for c in mind.conflicts()]


def test_review_queue_prefers_uncertain(mind):
    q = mind.review_queue(limit=5)
    assert q and all(c["status"] == "candidate" for c in q)
    # queue is sorted by priority
    prios = [c["_priority"] for c in q]
    assert prios == sorted(prios, reverse=True)


def test_introspection(mind):
    stats = mind.stats()
    assert stats["claims"]["total"] >= 5
    assert stats["commits"] > 10
    [claim] = mind.recall(subject="Ivan", predicate="works_at", touch=False)
    hist = mind.claim_history(claim["@id"])
    assert hist and all(h["commit"] for h in hist)
    # time travel: at the first commit that touched it, the claim existed
    assert hist[-1]["state"] is not None

"""Embedding sidecar tests. Index/math tests are pure; the semantic tests
need the tm-embed llama.cpp server and skip when it is down."""

import uuid

import numpy as np
import pytest

from terminus_mind import Mind, NoveltyResisted, TerminusClient
from terminus_mind.embeddings import Embedder, VectorIndex, semantic_relevance


def test_vector_index_roundtrip(tmp_path):
    idx = VectorIndex("t", directory=str(tmp_path))
    v = np.eye(3, dtype=np.float32)
    idx.upsert(["a", "b", "c"], v)
    assert idx.missing(["a", "d"]) == ["d"]
    sims = idx.similarities(np.array([1, 0, 0], dtype=np.float32), ["a", "b", "nope"])
    assert sims["a"] == pytest.approx(1.0) and sims["b"] == pytest.approx(0.0)
    # persists across reload; upsert overwrites in place
    idx2 = VectorIndex("t", directory=str(tmp_path))
    assert len(idx2) == 3
    idx2.upsert(["a"], np.array([[0, 1, 0]], dtype=np.float32))
    assert len(idx2) == 3
    assert idx2.similarities(np.array([0, 1, 0], dtype=np.float32), ["a"])["a"] == pytest.approx(1.0)


def test_semantic_relevance_mapping():
    assert semantic_relevance(0.60) == 0.0
    assert semantic_relevance(0.85) == 1.0
    assert semantic_relevance(-1.0) == 0.0
    assert 0 < semantic_relevance(0.75) < 1


needs_embedder = pytest.mark.skipif(
    not Embedder().available(), reason="tm-embed server not running"
)


@pytest.fixture(scope="module")
def mind():
    client = TerminusClient(db=f"tm_emb_test_{uuid.uuid4().hex[:8]}")
    m = Mind(client, agent="pytest")
    m.init()
    yield m
    client.delete_db()
    client.close()


@needs_embedder
def test_semantic_recall_without_token_overlap(mind):
    mind.assert_claim("Ivan", "works_at", object="Anthropic",
                      fact_text="Ivan works at Anthropic.")
    mind.assert_claim("Ivan", "lives_in", value="Oakland",
                      fact_text="Ivan lives in Oakland.")
    # 'employer' shares no token with either fact; semantics must carry it
    hits = mind.recall(query="employer", touch=False)
    assert hits and hits[0]["fact_text"] == "Ivan works at Anthropic."


@needs_embedder
def test_semantic_vocabulary_gate(mind):
    # employer_of is string-dissimilar but semantically near works_at
    with pytest.raises(NoveltyResisted) as exc:
        mind.assert_claim("Ada", "employer_of", object="Ivan", force=False)
    assert any(s["name"] == "works_at" for s in exc.value.suggestions)


@needs_embedder
def test_reindex_and_stats(mind):
    out = mind.reindex()
    assert out["available"] and out["indexed"] >= 2
    assert mind.stats()["embeddings"]["indexed"] == out["indexed"]

"""Hermes plugin tests: lifecycle simulated against a throwaway DB."""

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "hermes"))
from terminus_mind_provider import FLUSH_TURNS, TerminusMindProvider  # noqa: E402

from terminus_mind import Mind, TerminusClient  # noqa: E402


@pytest.fixture()
def provider(monkeypatch):
    db = f"tm_plug_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("TM_DB", db)
    p = TerminusMindProvider()
    p.initialize("sess-1", hermes_home="/tmp/hermes-test", platform="cli",
                 agent_context="primary", agent_identity="tester")
    yield p
    client = TerminusClient(db=db)
    client.delete_db()
    client.close()
    p._mind.client.close()


def _wait_flush(mind: Mind, n: int, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        eps = mind.client.list_docs("Episode")
        if len(eps) >= n:
            return eps
        time.sleep(0.1)
    raise AssertionError(f"expected {n} episodes")


def test_turn_buffering_one_episode_per_segment(provider):
    for i in range(FLUSH_TURNS):
        provider.sync_turn(f"user msg {i}", f"assistant reply {i}", session_id="sess-1")
    [ep] = _wait_flush(provider._mind, 1)
    assert ep["source"] == "agent" and ep["session"] == "sess-1"
    assert "user msg 0" in ep["content"] and f"assistant reply {FLUSH_TURNS-1}" in ep["content"]
    assert provider._buffer == []  # buffer cleared


def test_session_end_flushes_partial_buffer(provider):
    provider.sync_turn("a question", "an answer", session_id="sess-1")
    provider.on_session_end([])
    [ep] = _wait_flush(provider._mind, 1)
    assert "a question" in ep["content"]


def test_prompt_block_is_confirmed_only(provider):
    m = provider._mind
    m.assert_claim("Ivan", "works_at", object="Hyphae",
                   fact_text="Ivan works at Hyphae.", by_human=True)
    provider._refresh_prompt_block()
    assert provider.system_prompt_block() == ""  # candidate: stays out
    [c] = m.recall(subject="Ivan", touch=False)
    m.confirm(c["@id"], by_human=True)
    m.consolidate()
    provider._refresh_prompt_block()
    assert "Ivan works at Hyphae." in provider.system_prompt_block()


def test_prefetch_labels_candidates(provider):
    provider._mind.assert_claim("Ivan", "lives_in", value="Oakland",
                                fact_text="Ivan lives in Oakland.", by_human=True)
    out = provider.prefetch("where does Ivan live", session_id="sess-1")
    assert "Ivan lives in Oakland." in out and "candidate" in out


def test_tools_no_observe_and_dispatch(provider):
    names = {s["name"] for s in provider.get_tool_schemas()}
    assert "memory_observe" not in names and "memory_assert" in names
    result = json.loads(provider.handle_tool_call(
        "memory_assert",
        {"subject": "Ada", "predicate": "plays_chess_with", "object": "Ivan",
         "by_human": True}))
    assert result["claim_id"].startswith("Claim/")


def test_non_primary_context_is_read_only(monkeypatch):
    db = f"tm_plug_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("TM_DB", db)
    p = TerminusMindProvider()
    p.initialize("sess-cron", hermes_home="/tmp/h", platform="cron", agent_context="cron")
    try:
        names = {s["name"] for s in p.get_tool_schemas()}
        assert names == {"memory_recall", "memory_about", "memory_review"}
        p.sync_turn("u", "a")
        p.on_session_end([])
        time.sleep(0.3)
        assert p._mind.client.list_docs("Episode") == []
    finally:
        c = TerminusClient(db=db)
        c.delete_db()
        c.close()
        p._mind.client.close()


def test_reset_flushes_and_rescopes(provider):
    provider.sync_turn("before reset", "ok", session_id="sess-1")
    provider.on_session_switch("sess-2", reset=True)
    [ep] = _wait_flush(provider._mind, 1)
    assert ep["session"] == "sess-1"
    provider.sync_turn("after reset", "ok", session_id="sess-2")
    provider._flush("shutdown")  # shutdown() also closes the client; keep it open for asserts
    eps = _wait_flush(provider._mind, 2)
    by_sess = {e["session"]: e for e in eps}
    assert "after reset" in by_sess["sess-2"]["content"]

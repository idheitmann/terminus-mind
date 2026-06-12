"""Sleep cycle tests with a deterministic fake LLM (no model needed)."""

import json
import uuid

import pytest

from terminus_mind import Mind, TerminusClient
from terminus_mind.sleep import run_sleep


class FakeLLM:
    def __init__(self, batches):
        self.batches = list(batches)

    def available(self):
        return True

    def complete_json(self, system, user, max_tokens=1500):
        return self.batches.pop(0) if self.batches else []


@pytest.fixture()
def mind(_isolated_index):
    client = TerminusClient(db=f"tm_sleep_test_{uuid.uuid4().hex[:8]}")
    m = Mind(client, agent="pytest")
    m.init()
    yield m
    client.delete_db()
    client.close()


EXTRACTION = [
    {"subject": "Ivan", "predicate": "works_at", "object": "Hyphae", "value": None,
     "fact_text": "Ivan works at Hyphae."},
    {"subject": "Ivan", "predicate": "lives_in", "object": None, "value": "Oakland",
     "fact_text": "Ivan lives in Oakland."},
]


def test_sleep_distills_and_merges(mind):
    mind.observe("Ivan mentioned he works at Hyphae and lives in Oakland.")
    report = run_sleep(mind, llm=FakeLLM([EXTRACTION]))
    assert report["merged"] and report["asserted"] == 2 and not report["skipped"]
    # results visible on main after rebase; episode consolidated
    assert len(mind.recall(subject="Ivan", touch=False)) == 2
    assert mind.unconsolidated_episodes() == []
    # sleep branch cleaned up; commits authored by 'sleep' in the audit log
    assert any(e["author"] == "sleep" for e in mind.client.log(count=50))


def test_sleep_confirms_instead_of_duplicating(mind):
    mind.observe("ep1")
    run_sleep(mind, llm=FakeLLM([EXTRACTION]))
    mind.observe("Ivan said again that he works at Hyphae.")
    report = run_sleep(mind, llm=FakeLLM([[EXTRACTION[0]]]))
    assert report["confirmed"] == 1 and report["asserted"] == 0
    [claim] = mind.recall(subject="Ivan", predicate="works_at", touch=False)
    assert claim["confirms"] == 2


def test_sleep_reuses_vocabulary_under_resistance(mind):
    mind.observe("ep1")
    run_sleep(mind, llm=FakeLLM([EXTRACTION]))
    mind.observe("Ada also works at Hyphae apparently.")
    drifted = [{"subject": "Ada", "predicate": "works_for", "object": "Hyphae",
                "value": None, "fact_text": "Ada is employed by Hyphae."}]
    report = run_sleep(mind, llm=FakeLLM([drifted]))
    assert report["vocab_reused"] == 1
    [claim] = mind.recall(subject="Ada", touch=False)
    assert claim["predicate"] == "works_at"  # converged, not fragmented


def test_sleep_review_mode_leaves_branch(mind):
    mind.observe("Ivan works at Hyphae.")
    report = run_sleep(mind, llm=FakeLLM([EXTRACTION]), merge=False)
    assert report["branch"] and not report["merged"]
    # main untouched; branch holds the work
    assert mind.recall(subject="Ivan", touch=False) == []
    bmind = Mind(mind.client.on_branch(report["branch"]), agent="pytest")
    assert len(bmind.recall(subject="Ivan", touch=False)) == 2
    mind.client.delete_branch(report["branch"])


def test_pronoun_subjects_remap_to_self(mind, monkeypatch):
    monkeypatch.setenv("TM_SELF", "Ivan")
    mind.observe("first-person episode")
    cands = [{"subject": "I", "predicate": "uses_tool", "object": "Logseq", "value": None,
              "fact_text": "I use Logseq as a tool."},
             {"subject": "He", "predicate": "lives_in", "object": None, "value": "Berlin",
              "fact_text": "He lives in Berlin."}]
    report = run_sleep(mind, llm=FakeLLM([cands]))
    assert report["asserted"] == 1
    assert any("unresolvable pronoun" in s["reason"] for s in report["skipped"])
    [claim] = mind.recall(subject="Ivan", touch=False)
    assert claim["fact_text"] == "Ivan use Logseq as a tool."
    assert not mind.find_entity("I")


def test_pronouns_skip_without_self(mind, monkeypatch):
    monkeypatch.delenv("TM_SELF", raising=False)
    mind.observe("ep")
    report = run_sleep(mind, llm=FakeLLM([[{"subject": "I", "predicate": "wants",
                                            "object": None, "value": "tea",
                                            "fact_text": "I want tea."}]]))
    assert report["asserted"] == 0
    assert any("TM_SELF unset" in s["reason"] for s in report["skipped"])


def test_sibling_facts_do_not_cross_confirm(mind):
    mind.observe("ep1")
    run_sleep(mind, llm=FakeLLM([[
        {"subject": "Ivan", "predicate": "uses_tool", "object": "ClickUp", "value": None,
         "fact_text": "Ivan uses tool ClickUp"}]]))
    mind.observe("ep2")
    report = run_sleep(mind, llm=FakeLLM([[
        {"subject": "Ivan", "predicate": "uses_tool", "object": "Everhour", "value": None,
         "fact_text": "Ivan uses tool Everhour"},
        {"subject": "Ivan", "predicate": "uses_tool", "object": "ClickUp", "value": None,
         "fact_text": "Ivan relies on the tool ClickUp daily"}]]))
    # different object -> new claim; same object (different words) -> confirm
    assert report["asserted"] == 1 and report["confirmed"] == 1
    claims = {c["fact_text"]: c for c in mind.recall(subject="Ivan", touch=False)}
    assert claims["Ivan uses tool ClickUp"]["confirms"] == 2
    assert claims["Ivan uses tool Everhour"]["confirms"] == 1


def test_sleep_no_llm_still_consolidates(mind):
    class DownLLM:
        url = "http://down"

        def available(self):
            return False

    report = run_sleep(mind, llm=DownLLM())
    assert "error" in report

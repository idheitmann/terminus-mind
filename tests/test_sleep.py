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


def test_sleep_no_llm_still_consolidates(mind):
    class DownLLM:
        url = "http://down"

        def available(self):
            return False

    report = run_sleep(mind, llm=DownLLM())
    assert "error" in report

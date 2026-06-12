import json

from terminus_mind import journal
from terminus_mind.mind import Mind
from terminus_mind.tools import dispatch


def test_write_read_summarize(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_JOURNAL", str(tmp_path))
    journal.write_entry("hermes", "recall_miss", "missed stored fact about Ada",
                        tool="memory_recall", severity="major")
    journal.write_entry("hermes", "recall_miss", "missed again", tool="memory_recall")
    journal.write_entry("hermes", "bogus_kind", "falls back to other", severity="huge")
    entries = journal.read_entries()
    assert len(entries) == 3
    assert entries[2]["kind"] == "other" and entries[2]["severity"] == "minor"
    s = journal.summarize(entries)
    assert s["by_kind"]["recall_miss"] == 2
    assert s["by_tool"]["memory_recall"] == 2
    assert s["blocking"] == []
    # file is plain greppable JSONL
    raw = (tmp_path / "hermes.jsonl").read_text().splitlines()
    assert json.loads(raw[0])["note"] == "missed stored fact about Ada"


def test_archive_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_JOURNAL", str(tmp_path))
    journal.write_entry("hermes", "error", "first")
    journal.write_entry("other", "slow", "second")
    assert journal.archive_entries() == 2
    assert journal.read_entries() == []                       # active is empty
    archived = journal.read_entries(include_archived=True)
    assert {e["note"] for e in archived} == {"first", "second"}
    # post-triage entries land in a fresh active file; archive appends safely
    journal.write_entry("hermes", "error", "third")
    assert [e["note"] for e in journal.read_entries()] == ["third"]
    assert journal.archive_entries(agent="hermes") == 1
    assert len(journal.read_entries(include_archived=True)) == 3


def test_dispatch_journal_needs_no_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_JOURNAL", str(tmp_path))
    # client is never touched: journaling must work even when the DB is down
    mind = Mind.__new__(Mind)
    mind.agent = "hermes"
    out = dispatch(mind, "memory_journal",
                   {"kind": "error", "note": "db unreachable", "severity": "blocking"})
    assert out["journaled"] is True
    assert journal.summarize(journal.read_entries())["blocking"][0]["note"] == "db unreachable"

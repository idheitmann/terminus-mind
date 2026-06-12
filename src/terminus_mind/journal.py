"""Friction journal: where the agent documents problems with the memory
system itself.

Deliberately NOT stored in TerminusDB — the thing being reported on may be
the memory system malfunctioning, so the bug reporter must not depend on it.
Append-only JSONL, one file per agent, under $TM_JOURNAL (default:
./journal/ if a journal/ dir exists in cwd, else ~/.local/share/terminus-mind/journal/).

Closing the loop: `tm journal` aggregates by kind; repeated frictions become
threshold changes or roadmap items — candidates promoted by evidence, same
as beliefs.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("resistance_misfire", "recall_miss", "unclear_choice", "error", "slow", "other")
SEVERITIES = ("minor", "major", "blocking")


def journal_dir() -> Path:
    env = os.environ.get("TM_JOURNAL")
    if env:
        return Path(env)
    local = Path.cwd() / "journal"
    if local.is_dir():
        return local
    return Path.home() / ".local" / "share" / "terminus-mind" / "journal"


def write_entry(
    agent: str,
    kind: str,
    note: str,
    *,
    tool: str | None = None,
    expected: str | None = None,
    got: str | None = None,
    severity: str = "minor",
) -> dict:
    if kind not in KINDS:
        kind = "other"
    if severity not in SEVERITIES:
        severity = "minor"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "kind": kind,
        "severity": severity,
        "tool": tool,
        "expected": expected,
        "got": got,
        "note": note,
    }
    d = journal_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{agent}.jsonl", "a") as f:
        f.write(json.dumps({k: v for k, v in entry.items() if v is not None}) + "\n")
    return entry


def read_entries(agent: str | None = None, include_archived: bool = False) -> list[dict]:
    d = journal_dir()
    if not d.is_dir():
        return []
    if agent:
        files = [d / f"{agent}.jsonl"]
        if include_archived:
            files += sorted((d / "archive").glob(f"*-{agent}.jsonl"))
    else:
        files = sorted(d.glob("*.jsonl"))
        if include_archived:
            files += sorted((d / "archive").glob("*.jsonl"))
    entries = []
    for path in files:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if line.strip():
                entries.append(json.loads(line))
    return sorted(entries, key=lambda e: e["ts"])


def archive_entries(agent: str | None = None) -> int:
    """Move triaged entries out of the active journal. Returns count moved.

    Active files become journal/archive/<utc-date>-<agent>.jsonl (appended,
    so multiple triages per day are safe). Nothing is ever deleted."""
    d = journal_dir()
    if not d.is_dir():
        return 0
    archive = d / "archive"
    moved = 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    files = [d / f"{agent}.jsonl"] if agent else sorted(d.glob("*.jsonl"))
    for path in files:
        if not path.exists():
            continue
        content = path.read_text()
        n = sum(1 for line in content.splitlines() if line.strip())
        if not n:
            continue
        moved += n
        archive.mkdir(parents=True, exist_ok=True)
        with open(archive / f"{stamp}-{path.name}", "a") as f:
            f.write(content)
        path.unlink()
    return moved


def summarize(entries: list[dict]) -> dict:
    """Aggregate frictions: the repeated ones are the actionable ones."""
    by_kind = Counter(e["kind"] for e in entries)
    return {
        "total": len(entries),
        "by_kind": dict(by_kind.most_common()),
        "by_severity": dict(Counter(e["severity"] for e in entries)),
        "by_tool": dict(Counter(e["tool"] for e in entries if e.get("tool")).most_common()),
        "blocking": [e for e in entries if e["severity"] == "blocking"],
        "recent": entries[-5:],
    }

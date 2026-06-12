"""Import external memory content (e.g. a hindsight export) as episodes.

Usage:
    uv run python scripts/import_episodes.py export.txt           # blank-line-separated chunks
    uv run python scripts/import_episodes.py export.jsonl --jsonl # one JSON object per line

Each chunk becomes one Episode with source=document and session
"imported:<filename>". Nothing becomes a belief directly: the nightly sleep
distills imported episodes through the same extraction, vocabulary
resistance, and candidate lifecycle as live conversation — imported content
re-proves itself like everything else.

For JSONL, text is taken from the first present key of: text, content,
memory, fact.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from terminus_mind import Mind

TEXT_KEYS = ("text", "content", "memory", "fact")


def chunks_from(path: Path, jsonl: bool):
    if jsonl:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            text = next((obj[k] for k in TEXT_KEYS if isinstance(obj.get(k), str)), None)
            yield text or json.dumps(obj)
    else:
        for chunk in path.read_text().split("\n\n"):
            if chunk.strip():
                yield chunk.strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--jsonl", action="store_true")
    args = ap.parse_args()

    mind = Mind(agent="import")
    mind.init()
    n = 0
    for chunk in chunks_from(args.file, args.jsonl):
        mind.observe(chunk, source="document", session=f"imported:{args.file.name}")
        n += 1
    print(f"imported {n} episodes from {args.file} - the nightly sleep will distill them")


if __name__ == "__main__":
    main()

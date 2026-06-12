"""Embedding sidecar: semantic recall and a semantic vocabulary gate.

A llama.cpp server with an embedding model (see tm-embed.service:
nomic-embed-text-v1.5 on 127.0.0.1:8089) provides vectors; a numpy index
keyed by document id persists them on disk. The index is a CACHE,
rebuildable from the database at any time (`tm reindex`) — never a second
source of truth.

Everything here is optional: if the embedding server is unreachable, the
Mind degrades to exact/string behavior identically to before the sidecar
existed.

Env: TM_EMBED_URL (default http://127.0.0.1:8089), TM_EMBED_GATE
(vocabulary-resistance cosine threshold, default 0.74 — calibrated on
nomic-embed-text-v1.5, whose short-phrase cosines are compressed: unrelated
pairs reach ~0.74, near-synonyms 0.75-0.87), TM_INDEX (index directory).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import numpy as np

EMBED_GATE = float(os.environ.get("TM_EMBED_GATE", "0.74"))
# semantic relevance for recall: map cosine [0.60, 0.85] -> [0, 1]
_REL_LO, _REL_HI = 0.60, 0.85


class Embedder:
    """Client for an OpenAI-compatible /v1/embeddings endpoint.

    Uses nomic's asymmetric prefixes: documents and queries are embedded
    differently, which measurably improves retrieval."""

    def __init__(self, url: str | None = None):
        self.url = (url or os.environ.get("TM_EMBED_URL", "http://127.0.0.1:8089")).rstrip("/")
        self._http = httpx.Client(timeout=30.0)
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            try:
                self._http.get(f"{self.url}/health")
                self._available = True
            except httpx.HTTPError:
                self._available = False
        return self._available

    def embed(self, texts: list[str], kind: str = "document") -> np.ndarray:
        """Returns L2-normalized vectors, one row per text."""
        prefix = "search_query: " if kind == "query" else "search_document: "
        resp = self._http.post(
            f"{self.url}/v1/embeddings",
            json={"input": [prefix + t for t in texts], "model": "embed"},
        )
        resp.raise_for_status()
        mat = np.array([d["embedding"] for d in resp.json()["data"]], dtype=np.float32)
        return mat / np.linalg.norm(mat, axis=1, keepdims=True)


class VectorIndex:
    """Disk-persisted id->vector cache (one .npz per database)."""

    def __init__(self, name: str, directory: str | None = None):
        d = directory or os.environ.get("TM_INDEX") or str(
            Path.home() / ".local" / "share" / "terminus-mind" / "index"
        )
        self.path = Path(d) / f"{name}.npz"
        self._ids: list[str] = []
        self._vecs: np.ndarray | None = None
        if self.path.exists():
            data = np.load(self.path, allow_pickle=False)
            self._ids = [str(i) for i in data["ids"]]
            self._vecs = data["vecs"]
        self._pos = {i: n for n, i in enumerate(self._ids)}

    def __len__(self) -> int:
        return len(self._ids)

    def missing(self, ids: list[str]) -> list[str]:
        return [i for i in ids if i not in self._pos]

    def upsert(self, ids: list[str], vecs: np.ndarray) -> None:
        for i, v in zip(ids, vecs):
            if i in self._pos:
                self._vecs[self._pos[i]] = v
            else:
                self._pos[i] = len(self._ids)
                self._ids.append(i)
                self._vecs = v[None, :] if self._vecs is None else np.vstack([self._vecs, v])
        self._save()

    def similarities(self, query_vec: np.ndarray, ids: list[str]) -> dict[str, float]:
        """Cosine of query against each requested id (skips unindexed ids)."""
        rows = [(i, self._pos[i]) for i in ids if i in self._pos]
        if not rows:
            return {}
        sub = self._vecs[[r for _, r in rows]]
        sims = sub @ query_vec
        return {i: float(s) for (i, _), s in zip(rows, sims)}

    def drop(self) -> None:
        self.path.unlink(missing_ok=True)
        self._ids, self._vecs, self._pos = [], None, {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self.path, ids=np.array(self._ids), vecs=self._vecs)


def semantic_relevance(cos: float) -> float:
    """Map raw cosine to a 0..1 relevance comparable with token overlap."""
    return max(0.0, min(1.0, (cos - _REL_LO) / (_REL_HI - _REL_LO)))

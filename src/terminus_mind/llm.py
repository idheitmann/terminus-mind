"""Thin client for a local OpenAI-compatible chat endpoint (llama.cpp).

Used only by the sleep cycle; the core memory remains LLM-free. Env:
TM_LLM_URL (default http://127.0.0.1:8080 — the llama-server instance).
"""

from __future__ import annotations

import json
import os
import re

import httpx


class ChatLLM:
    def __init__(self, url: str | None = None):
        self.url = (url or os.environ.get("TM_LLM_URL", "http://127.0.0.1:8080")).rstrip("/")
        self._http = httpx.Client(timeout=180.0)
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            try:
                self._http.get(f"{self.url}/health")
                self._available = True
            except httpx.HTTPError:
                self._available = False
        return self._available

    def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        resp = self._http.post(
            f"{self.url}/v1/chat/completions",
            json={
                "model": "local",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def complete_json(self, system: str, user: str, max_tokens: int = 1500):
        """Complete and parse a JSON answer; one retry on parse failure."""
        for attempt in (1, 2):
            text = self.complete(system, user, max_tokens=max_tokens)
            try:
                return _extract_json(text)
            except ValueError:
                if attempt == 2:
                    raise
                user = user + "\n\nYour previous answer was not valid JSON. Answer with ONLY the JSON."
        raise ValueError("unreachable")


def _extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(f"no JSON found in: {text[:200]}")
    return json.loads(text[start:])

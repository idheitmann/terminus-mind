"""terminus-mind as a hermes memory provider.

Design decisions (each solves a specific integration problem minimally):

- Turn buffering: sync_turn appends to an in-memory buffer; ONE episode is
  written per conversation segment (every FLUSH_TURNS turns, or at session
  end / pre-compress / reset / shutdown). Episode granularity stays
  "meaningful exchange", the nightly sleep's per-episode LLM extraction
  gets coherent context, and heavy chat can't outrun consolidation.
- Ambient context is confirmed-only: system_prompt_block() injects only
  confirmed or pinned beliefs. Candidates never enter the prompt
  unlabeled — they reach the model exclusively through prefetch()/recall
  results, where status and credence are shown so it can hedge.
- Writes are skipped entirely for non-primary contexts (cron, subagents),
  per the MemoryProvider contract; reads still work.
- All flush work runs on daemon threads (sync_turn must not block).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

# When this package is symlinked into hermes-agent/plugins/memory/, resolve
# the symlink back to the terminus-mind checkout and use its src/ directly —
# no install step, always the current code.
try:
    import terminus_mind  # noqa: F401
except ImportError:
    _src = Path(__file__).resolve().parents[3] / "src"
    if _src.is_dir():
        sys.path.insert(0, str(_src))

from terminus_mind import Mind, TerminusClient  # noqa: E402
from terminus_mind.tools import TOOL_SPECS, dispatch  # noqa: E402

logger = logging.getLogger(__name__)

FLUSH_TURNS = 6          # matches hermes' own flush_min_turns default
PROMPT_BELIEFS = 12      # confirmed beliefs in the static prompt block
PREFETCH_LIMIT = 6


class TerminusMindProvider:
    @property
    def name(self) -> str:
        return "terminus-mind"

    def __init__(self) -> None:
        self._mind: Mind | None = None
        self._session_id = ""
        self._writes_enabled = True
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        self._prompt_block = ""

    # -- core lifecycle ----------------------------------------------------

    def is_available(self) -> bool:
        # contract: no network calls here — deps import fine, config is env
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._writes_enabled = kwargs.get("agent_context", "primary") == "primary"
        agent = os.environ.get("TM_AGENT") or f"hermes:{kwargs.get('agent_identity', 'default')}"
        self._mind = Mind(TerminusClient(), agent=agent)
        self._mind.init()
        self._refresh_prompt_block()

    def _refresh_prompt_block(self) -> None:
        try:
            claims = [
                c for c in self._mind.client.query_template("Claim", {"status": "confirmed"})
                if not c.get("expired_at")
            ]
            pinned = [
                c for c in self._mind.client.query_template("Claim", {"pinned": True})
                if not c.get("expired_at") and c["status"] != "confirmed"
            ]
            from terminus_mind import scoring

            claims.sort(key=lambda c: -scoring.rank_score(c))
            top = (pinned + claims)[:PROMPT_BELIEFS]
            if not top:
                self._prompt_block = ""
                return
            lines = "\n".join(f"- {c['fact_text']}" for c in top)
            self._prompt_block = (
                "## Established world model (terminus-mind)\n"
                "Proven beliefs; trust these:\n" + lines
            )
        except Exception:
            logger.exception("terminus-mind: prompt block refresh failed")
            self._prompt_block = ""

    def system_prompt_block(self) -> str:
        return self._prompt_block

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not query or not query.strip() or self._mind is None:
            return ""
        try:
            hits = self._mind.recall(query=query, limit=PREFETCH_LIMIT)
        except Exception:
            logger.exception("terminus-mind: prefetch recall failed")
            return ""
        if not hits:
            return ""
        lines = []
        for c in hits:
            s = c["_scores"]
            tag = "pinned" if c.get("pinned") else f"{c['status']}, credence {s['credence']:.2f}"
            lines.append(f"- {c['fact_text']} ({tag}; id {c['@id']})")
        return (
            "## Recalled beliefs (terminus-mind)\n"
            "Hedge or verify anything not confirmed/pinned; confirm or correct "
            "via memory tools as the conversation clarifies:\n" + "\n".join(lines)
        )

    # -- auto-observe: turn buffering ---------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages=None) -> None:
        if not self._writes_enabled:
            return
        with self._lock:
            self._buffer.append(f"User: {user_content}\nAssistant: {assistant_content}")
            should_flush = len(self._buffer) >= FLUSH_TURNS
        if should_flush:
            self._flush_async("segment")

    def _flush_async(self, reason: str) -> None:
        threading.Thread(target=self._flush, args=(reason,), daemon=True).start()

    def _flush(self, reason: str) -> None:
        with self._lock:
            if not self._buffer:
                return
            content, self._buffer = "\n\n".join(self._buffer), []
        try:
            self._mind.observe(content, source="agent", session=self._session_id)
        except Exception:
            logger.exception("terminus-mind: episode flush (%s) failed", reason)

    # -- tools ----------------------------------------------------------------

    def get_tool_schemas(self):
        # memory_observe is excluded: sync_turn auto-observes, and a manual
        # observe on top would double-record episodes.
        specs = [s for s in TOOL_SPECS if s["name"] != "memory_observe"]
        if not self._writes_enabled:
            ro = {"memory_recall", "memory_about", "memory_review"}
            specs = [s for s in specs if s["name"] in ro]
        return specs

    def handle_tool_call(self, tool_name: str, args, **kwargs) -> str:
        return json.dumps(dispatch(self._mind, tool_name, args or {}), default=str)

    # -- optional hooks ---------------------------------------------------------

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass  # recall is local (numpy + localhost HTTP); fast enough inline

    def on_session_end(self, messages) -> None:
        self._flush("session_end")
        # lifecycle (promotion/retirement) is the nightly sleep's job;
        # session end just guarantees the episode is durable.

    def on_pre_compress(self, messages) -> str:
        self._flush("pre_compress")  # capture before the transcript is discarded
        return ""

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "",
                          reset: bool = False, rewound: bool = False, **kwargs) -> None:
        if reset:
            self._flush("reset")
        self._session_id = new_session_id
        self._refresh_prompt_block()  # cheap; picks up newly confirmed beliefs

    def on_memory_write(self, action: str, target: str, content: str, metadata=None) -> None:
        # built-in memory writes are curated facts — worth observing
        if self._writes_enabled and action in ("add", "replace"):
            try:
                self._mind.observe(f"[builtin memory {action} -> {target}] {content}",
                                   source="agent", session=self._session_id)
            except Exception:
                logger.exception("terminus-mind: memory_write mirror failed")

    def get_config_schema(self):
        return [
            {"key": "server", "description": "TerminusDB URL", "env_var": "TM_SERVER",
             "default": "http://127.0.0.1:6363"},
            {"key": "db", "description": "Database name (use a distinct one per profile)",
             "env_var": "TM_DB", "default": "mind"},
            {"key": "agent", "description": "Author recorded on memory commits",
             "env_var": "TM_AGENT", "default": "hermes"},
            {"key": "journal", "description": "Friction journal directory",
             "env_var": "TM_JOURNAL", "default": ""},
        ]

    # env-var-only config: save_config stays a no-op per the contract
    def save_config(self, values, hermes_home: str) -> None:
        pass

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        pass

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        pass

    def shutdown(self) -> None:
        self._flush("shutdown")
        if self._mind is not None:
            self._mind.client.close()


def register(ctx) -> None:
    ctx.register_memory_provider(TerminusMindProvider())

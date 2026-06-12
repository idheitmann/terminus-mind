# terminus-mind as Hermes Memory Plugin — Architecture Assessment

Written by Hermes Agent, 2026-06-12, at Ivan's request to brief Claude.

---

## Comparison: terminus-mind vs Hindsight

| Dimension | Hindsight | terminus-mind |
|---|---|---|
| Data model | Flat text blobs with embeddings | Structured S→P→O triples with provenance |
| Belief lifecycle | None (additive only) | Candidate → confirmed → retired, with evidence scoring |
| Vocabulary | Uncontrolled | Conservative with semantic resistance gate (nomic-embed) |
| Correctness | Cannot correct, only accumulate | Supersede with provenance chain |
| Human testimony | No concept | 3× weight, drives promotion |
| Semantic search | ✅ Mature | ✅ Built into `memory_recall` (nomic-embed via llama.cpp sidecar) |
| Retrieval pattern | Passive (auto-injected) | Same — via plugin `system_prompt_block()` + `prefetch()` hooks |
| Auto-observe | Fire-and-forget | Same — via plugin `sync_turn()` hook |
| Cost | ~$50/month cloud | Free (local TerminusDB on podman) |
| Privacy | Data leaves machine | Stays local |
| Audit trail | None | `tm log`, `tm history`, git commits |
| Schema enforcement | None | TerminusDB schema — no garbage triples |
| Friction self-healing | None | `memory_journal` feeds weekly triage cycle |
| Ecosystem risk | Vendor pricing/availability | DFRNT (for-profit company maintaining TerminusDB) |

**Hindsight has no remaining architectural advantage.** Its only practical advantage is that it's the currently-configured provider. Once terminus-mind is wired as a plugin, the comparison is one-sided.

---

## Plugin Architecture: The Hooks Exist

Hermes has a `MemoryProvider` ABC at `agent/memory_provider.py`. All the hooks terminus-mind needs are already defined:

### Auto-injection (passive context into system prompt)

| Hook | When Called | terminus-mind Use |
|---|---|---|
| `system_prompt_block()` | System prompt assembly | Inject top-N high-credence, high-activation beliefs |
| `prefetch(query)` | **Before each API call** | Semantic recall of beliefs relevant to the current user message |
| `queue_prefetch(query)` | After each turn completes | Pre-warm embedding results for next turn |

### Auto-observe (capturing exchanges)

| Hook | When Called | terminus-mind Use |
|---|---|---|
| `sync_turn(user, assistant)` | **After each completed turn** | Auto-file an episode — replaces the manual `memory_observe` step |
| `on_session_end(messages)` | Session exit / `/reset` / gateway expiry | Run `tm consolidate` to promote/retire beliefs |
| `on_pre_compress(messages)` | Before context compression | Extract claims from messages about to be permanently discarded |

### Tool Registration (expose tm-mcp tools natively)

| Hook | What It Does |
|---|---|
| `get_tool_schemas()` | Register `memory_recall`, `memory_assert`, `memory_confirm`, `memory_supersede`, `memory_about`, `memory_review`, `memory_journal` as native agent tools |
| `handle_tool_call(name, args)` | Dispatch tool calls — no MCP round-trip overhead |

### Other Lifecycle Hooks

| Hook | Use |
|---|---|
| `on_turn_start()` | Turn counting, periodic maintenance |
| `on_session_switch()` | Handle `/resume`, `/branch`, `/reset` mid-process without tearing down the provider |
| `on_memory_write()` | Mirror built-in `memory` tool writes to terminus-mind |
| `on_delegation()` | Observe subagent task delegation and results |
| `shutdown()` | Close TerminusDB connection, flush queues |

### Config & Setup

- `get_config_schema()` — declare config fields (`TM_SERVER`, `TM_TEAM`, `TM_DB`, etc.)
- `save_config(values, hermes_home)` — write non-secret config
- `initialize(session_id, hermes_home=...)` — connect to TerminusDB, verify tm-embed.service
- `is_available()` — check TerminusDB reachable, credentials present
- Profile isolation: all storage paths use `hermes_home` kwarg, not hardcoded `~/.hermes`

---

## Plugin Readiness Summary

| Capability | Status |
|---|---|
| Structured belief graph (S→P→O with provenance) | ✅ |
| Belief lifecycle (candidate → confirmed → retired) | ✅ |
| Evidence scoring (credence, activation) | ✅ |
| Vocabulary discipline + semantic resistance gate | ✅ |
| Semantic search (`memory_recall` + `tm-embed.service`) | ✅ |
| Audit trail (`tm log`, `tm history`, git) | ✅ |
| Local/private (no cloud dependency, no API cost) | ✅ |
| Commercial maintainer (DFRNT) | ✅ |
| Plugin hooks for passive injection | ✅ (in `MemoryProvider` ABC) |
| Plugin hooks for auto-observe | ✅ (in `MemoryProvider` ABC) |
| Native tool registration (no MCP round-trip) | ✅ (in `MemoryProvider` ABC) |
| Conflict surface for human review | ✅ |
| Friction self-healing (`memory_journal`) | ✅ |

**No gaps remain.** The three earlier concerns are resolved:
- Semantic search: already built, wired into `memory_recall`, needs `tm-embed.service` running
- Passive injection: `system_prompt_block()` + `prefetch()` handle it
- Auto-observe: `sync_turn()` handles it

---

## What the Plugin Needs to Do

```
initialize(session_id):
  → connect to TerminusDB (localhost:6363), verify tm-embed.service (127.0.0.1:8089)
  → scope writes to this session

system_prompt_block():
  → return top-N high-credence, high-activation beliefs as formatted text
  → injected into every system prompt

prefetch(query):
  → semantic recall via memory_recall(query)
  → return relevant beliefs as context for the upcoming turn

sync_turn(user, assistant):
  → memory_observe(content=user+assistant, source="agent")
  → background thread (must not block)

get_tool_schemas():
  → register memory_recall, memory_assert, memory_confirm, memory_supersede,
    memory_about, memory_review, memory_journal as native tools

on_session_end(messages):
  → run tm consolidate for promotion/retirement pass
```

---

## Files to Reference

- Hermes MemoryProvider ABC: `agent/memory_provider.py` (in hermes-agent repo)
- Memory provider plugin docs: `website/docs/developer-guide/memory-provider-plugin.md`
- Existing memory plugin examples: `plugins/memory/honcho/`, `plugins/memory/hindsight/`
- terminus-mind source: `/home/ivanh/dev/terminus-mind/`
- MCP server: `src/terminus_mind/mcp_server.py`
- Embedding sidecar: `src/terminus_mind/embeddings.py`
- Embedding service: `tm-embed.service` (llama.cpp with nomic-embed-text-v1.5 on 127.0.0.1:8089)

# terminus-mind

Self-evolving agent memory on [TerminusDB](https://terminusdb.com). Every
node and edge starts as a hypothesis and is only **proven over time through
interaction with a human** — the way a person refines a world model through
experience and consolidation.

Built for long-lived agents (target framework: hermes, Python). No LLM or
embedding dependencies: intelligence lives in the calling agent, structure
and evidence live here. See [DESIGN.md](DESIGN.md) for the architecture and
its research lineage (Graphiti bi-temporality, subjective-logic credence,
NELL promotion, ACT-R activation, Mem0 adjudication).

## What makes it different

- **Beliefs, not facts.** Claims carry evidence counters
  (`confirms`/`contradicts`, human testimony weighs 3×), derived
  `credence`/`uncertainty`/`activation` scores, and a
  `candidate → confirmed → retired` lifecycle.
- **Never deletes.** Corrections supersede: the old belief keeps its history,
  validity interval, and a `superseded_by` link. Human corrections pin.
- **Conservative, learning ontology.** Predicates and entity types are
  learned vocabulary with built-in *resistance*: a novel term close to an
  existing one is rejected with suggestions (`works_for` → "did you mean
  `works_at`?"); forced novelty enters as `provisional` and is ratified or
  merged by a human after proving its usage.
- **Total introspection for free.** Every memory change is a TerminusDB
  commit: `tm log` is the audit trail, `tm history <claim>` replays one
  belief's life, time-travel reads answer "what did I believe last month?"

## Setup

Requires a running TerminusDB (the dev instance here runs in podman, HTTP on
`127.0.0.1:6363`). Configure via env if non-default: `TM_SERVER`, `TM_TEAM`,
`TM_DB`, `TM_USER`, `TM_PASS`.

```sh
uv sync
uv run tm init          # create the 'mind' database + schema
uv run pytest           # integration tests (uses a throwaway database)
```

## CLI

```sh
tm observe "Ivan said he works at Hyphae."        # lossless episode, returns id
tm assert Ivan works_at Hyphae --entity --episode Episode/...
tm recall "where does Ivan work"                  # ranked; counts as a use
tm about Ivan                                     # full entity neighborhood
tm confirm Claim/...                              # human reinforcement (3x)
tm correct Claim/... --object Anthropic           # supersede + pin
tm review                                         # what to ask the human next
tm conflicts                                      # contradicted, unresolved
tm consolidate                                    # promotion/retirement/uptake pass
tm vocab / tm ratify predicate works_at / tm merge-term predicate works_for works_at
tm log / tm history Claim/... / tm stats
```

## Agent integration (hermes)

**Recommended: MCP.** `tm-mcp` serves the memory over stdio to any
MCP-speaking agent — the world model stays decoupled from (and outlives) any
one framework. Point hermes' MCP config at:

```json
{
  "mcpServers": {
    "tm-mcp": {
      "command": "uv",
      "args": ["run", "--no-active", "--project", "/path/to/terminus-mind", "tm-mcp"],
      "env": {
        "TM_AGENT": "hermes",
        "TM_JOURNAL": "/path/to/terminus-mind/journal"
      }
    }
  }
}
```

`TM_AGENT` is the author recorded on every memory commit; `TM_DB` etc.
override the connection (defaults match the local podman instance).
`--no-active` stops uv warning about the calling agent's own `VIRTUAL_ENV`.
Alternative with no uv in the path at all (faster spawn, but you own keeping
the venv synced): use `/path/to/terminus-mind/.venv/bin/tm-mcp` as the
command directly.

**Planned: hermes memory-provider plugin** (ROADMAP 1.5) — deep hook
integration (`sync_turn`, `prefetch`, `on_session_end`) wrapping the same
`Mind` in-process; MCP remains the interface for all other agents.

**Fallback: direct import** (hermes runs Python) — same tools, in-process:

```python
from terminus_mind import Mind
from terminus_mind.tools import TOOL_SPECS, dispatch

mind = Mind(agent="hermes")
mind.init()
# register TOOL_SPECS (OpenAI function-calling format) with the model
result = dispatch(mind, tool_call.name, tool_call.arguments)
```

The nine tools encode the adjudication loop in their descriptions: observe
each exchange, **recall before asserting**, then choose
assert / confirm / supersede / contradict. Vocabulary resistance comes back
as a normal result (`resisted: true` + suggestions) so the model adjudicates
instead of crashing. `memory_review` gives the agent questions to weave into
conversation — uncertainty is the engine of the human-proving loop. Add
[prompts/memory.md](prompts/memory.md) to the agent's system prompt to make
the loop reliable.

**Friction journal.** `memory_journal` is where the agent reports problems
with the memory system *itself* (resistance misfires, recall misses, errors)
— plain JSONL under `journal/`, deliberately outside the database so it
works even when the database doesn't. `tm journal` aggregates by kind;
repeated frictions become threshold changes or roadmap items.

The agent's offline "sleep" job should pull `unconsolidated_episodes()`,
distill them through the same primitives, `mark_consolidated()`, then run
`consolidate()`.

## Status

MVP, evolution-ready. Deliberate upgrade paths (see DESIGN.md): sidecar
embedding index over `fact_text`, Personalized PageRank recall, typed-schema
promotion of established predicates, branch-per-sleep-job consolidation.

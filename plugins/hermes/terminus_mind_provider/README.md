# terminus-mind hermes plugin

Wraps the terminus-mind belief graph as a hermes `MemoryProvider`.

## Install

```sh
ln -s /home/ivanh/dev/terminus-mind/plugins/hermes/terminus_mind_provider \
      ~/.hermes/hermes-agent/plugins/memory/terminus-mind
```

Then in the target profile's `config.yaml`:

```yaml
memory:
  provider: terminus-mind
```

Config is env-var-only: `TM_SERVER`, `TM_DB`, `TM_AGENT`, `TM_JOURNAL`
(see `get_config_schema`). **For a test profile, set a distinct `TM_DB`
(e.g. `mind_test`)** so experiments never touch the real world model.
The symlinked plugin imports `terminus_mind` straight from the repo's
`src/` — no install step; the checkout is the deployment.

Requires running: TerminusDB (podman, :6363), `tm-embed.service` (:8089,
optional — degrades to string matching), and for the nightly distillation
`llama-server` (:8080) + `tm-sleep.timer`.

## Behavior

- **Episodes are conversation segments, not turns**: turns buffer in memory
  and flush as one episode per 6 turns or at session end / pre-compress /
  reset / shutdown. Bounded episode volume; coherent context for the
  nightly sleep extraction.
- **Ambient context is confirmed-only**: the static prompt block carries
  only confirmed/pinned beliefs. Candidates appear solely in `prefetch`
  results, labeled with status and credence so the model hedges.
- **Tools**: the standard memory tool surface minus `memory_observe`
  (auto-observe replaces it). Non-primary contexts (cron, subagents) get
  read-only tools and no writes.
- **Built-in memory writes** (`MEMORY.md`/`USER.md`) are mirrored as
  episodes — curated facts re-prove themselves through the normal lifecycle.
- **Lifecycle stays with the nightly sleep** (`tm-sleep.timer`), not the
  plugin: session end only guarantees the episode is durable.

## Migrating from hindsight

Export the hindsight memories to text or JSONL, then:

```sh
uv run python scripts/import_episodes.py hindsight-export.jsonl --jsonl
```

Imported content enters as `document` episodes and is distilled by the
nightly sleep through the same gates as live conversation — it re-proves
itself; nothing is imported as established truth.

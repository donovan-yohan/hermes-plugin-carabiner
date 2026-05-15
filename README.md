# hermes-plugin-carabiner

Carabiner is a standalone Hermes plugin that connects specialist agents through Honcho-backed collaboration memory.

It is intentionally **not** a Hermes core/bundled plugin and **not** a `memory.provider`. Hindsight can remain the active semantic memory provider while Carabiner stores narrow, evidence-backed relationship memory for handoffs, QA, reviews, and agent-to-agent working patterns.

## Install

```bash
hermes plugins install donovan-yohan/hermes-plugin-carabiner --enable
```

If your Hermes build can load pip entry-point plugins but not Git plugin roots, clone or copy this repository into the active profile instead:

```bash
mkdir -p "$HERMES_HOME/plugins"
git clone https://github.com/donovan-yohan/hermes-plugin-carabiner.git "$HERMES_HOME/plugins/carabiner"
hermes plugins enable carabiner
```

Restart the gateway or start a new CLI session after enabling plugins.

## Configuration

Set secrets in the active Hermes profile `.env`, not in tracked config:

```bash
CARABINER_BASE_URL=https://memory.fish-rattlesnake.ts.net
CARABINER_WORKSPACE=hermes-ocean-dev
CARABINER_API_KEY=<scoped Honcho JWT>
# optional
CARABINER_TIMEOUT=15
CARABINER_ENABLED=true
CARABINER_CAPTURE_SUBAGENTS=false
```

Config block fallback is also supported:

```yaml
plugins:
  enabled:
    - carabiner

carabiner:
  base_url: https://memory.fish-rattlesnake.ts.net
  workspace: hermes-ocean-dev
  enabled: true
```

For migration from the early prototype, `HONCHO_RELATIONSHIP_*` env vars and the `agent_relationship_memory` config block are accepted as fallback aliases. Prefer the `CARABINER_*` names going forward.

## Tools

- `carabiner_record_feedback` — record evidence-backed peer critique/feedback about observable work or handoff behavior.
- `carabiner_record_episode` — record a compact collaboration episode.
- `carabiner_handoff_context` — retrieve scoped context before one agent hands work to another.
- `carabiner_context` — retrieve observer/target relationship context.
- `carabiner_audit` — raw search/audit for relationship-memory records.

## Design constraints

- Store compact operational episodes, not full transcripts.
- Critique work products and collaboration behavior, not agent identity/personality labels.
- Scope memories by repo/task/relationship and cite evidence/work IDs.
- Fail soft: if Honcho is unavailable, tools return JSON errors instead of blocking the agent loop.
- Keep Honcho as a sidecar. Do not switch `memory.provider` away from Hindsight just to use Carabiner.

## Honcho deploy notes

The current local deployment expects:

```text
Honcho API: https://memory.fish-rattlesnake.ts.net
Health:     https://memory.fish-rattlesnake.ts.net/health
Hindsight:  http://memory.fish-rattlesnake.ts.net:8888
```

`/health` is open. `/v3/*` requires an HS256 Bearer JWT. Empty representations after successful small smoke writes are not automatically a broken deriver: Honcho may need roughly 1024 tokens of accumulated session/message content before representation processing kicks in. Carabiner falls back to raw scoped search and labels that as `raw_search_fallback` while representations are empty.

## Development

```bash
python -m pytest -q
```

The unit tests use a fake Honcho transport and do not need network access or secrets.

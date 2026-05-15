# hermes-plugin-carabiner

Carabiner is a standalone Hermes plugin that connects specialist agents through Honcho-backed collaboration memory.

It is intentionally **not** a Hermes core/bundled plugin and **not** a `memory.provider`. Hindsight can remain the active semantic memory provider while Carabiner stores narrow, evidence-backed relationship memory for handoffs, QA, reviews, and agent-to-agent working patterns.

## What problem this solves

Specialist agents get better when they remember how other specialists work:

- `ika-frontend` learns what `kame-qa` needs before passing frontend work to QA.
- `kame-qa` learns recurring verification gaps in `ika-frontend` handoffs.
- `kani-backend` and `fugu-reviewer` build a history of review expectations.
- `koi-product` can leave acceptance-criteria feedback that implementers see later.

Without Carabiner, every handoff starts cold unless the prompt manually restates the whole relationship history. With Carabiner, agents can record compact collaboration episodes and retrieve scoped context before the next handoff.

This is **relationship memory**, not user/project memory. Keep durable user facts in Hindsight. Use Carabiner for operational agent-to-agent patterns.

## Mental model: shared pool, directional brains

Honcho is not one flat shared note file, and it is not separate databases per agent.

Think of it as one workspace with many peers and directional views:

```text
workspace: hermes-ocean-dev
peers:
  agent:ika-frontend
  agent:kani-backend
  agent:kame-qa
  agent:fugu-reviewer
  agent:koi-product

views / representations:
  kame-qa -> ika-frontend     # what Kame has learned about Ika's handoffs
  ika-frontend -> kame-qa     # what Ika has learned about Kame's QA expectations
  fugu-reviewer -> kani       # what Fugu has learned about Kani's review patterns
```

So yes, you can loosely think of “Ika having thoughts about Kame” and “Kame having thoughts about Ika,” but mechanically those thoughts live in the same Honcho workspace as directional peer representations plus raw evidence records.

Raw records are still useful even before Honcho derives a representation. Carabiner retrieval falls back to scoped raw search and labels it as `raw_search_fallback`.

## Getting started

Install the plugin in every Hermes profile that should either:

1. call Carabiner tools manually;
2. retrieve relationship context; or
3. automatically record `delegate_task` outcomes from that profile.

For the ocean team, that usually means installing it into each participating profile: `tako-planner`, `ika-frontend`, `kani-backend`, `kame-qa`, `fugu-reviewer`, `kujira-ops`, `kurage-writer`, `koi-product`, etc.

A profile does **not** need the plugin merely to be mentioned as a peer in someone else's memory. But if that profile should form its own memories or record its own delegations, install and configure Carabiner there too.

```bash
hermes --profile ika-frontend plugins install donovan-yohan/hermes-plugin-carabiner --enable
hermes --profile kani-backend plugins install donovan-yohan/hermes-plugin-carabiner --enable
hermes --profile kame-qa plugins install donovan-yohan/hermes-plugin-carabiner --enable
```

Restart gateways/workers or start new CLI sessions after enabling plugins.

## Configuration

Set secrets in each active Hermes profile `.env`, not in tracked config:

```bash
CARABINER_BASE_URL=https://memory.fish-rattlesnake.ts.net
CARABINER_WORKSPACE=hermes-ocean-dev
CARABINER_API_KEY=<scoped Honcho JWT>

# identify the current profile as a Honcho peer
CARABINER_AGENT_ID=agent:ika-frontend

# optional defaults used by automatic delegate_task capture
CARABINER_DEFAULT_REPO=relay-ide
CARABINER_DEFAULT_TASK_TYPE=frontend

# automatic delegate_task capture is opt-in
CARABINER_CAPTURE_SUBAGENTS=true

# optional
CARABINER_TIMEOUT=15
CARABINER_ENABLED=true
```

Config block fallback is also supported:

```yaml
plugins:
  enabled:
    - carabiner

carabiner:
  base_url: https://memory.fish-rattlesnake.ts.net
  workspace: hermes-ocean-dev
  agent_id: agent:ika-frontend
  default_repo: relay-ide
  default_task_type: frontend
  enabled: true
```

For migration from the early prototype, `HONCHO_RELATIONSHIP_*` env vars and the `agent_relationship_memory` config block are accepted as fallback aliases. Prefer the `CARABINER_*` names going forward.

## How agents use this

### 1. Automatic `delegate_task` capture

When `CARABINER_CAPTURE_SUBAGENTS=true`, Carabiner listens for Hermes' `subagent_stop` hook. Every completed `delegate_task` child creates a low-confidence collaboration episode:

```text
actor: agent:ika-frontend
participant: agent:leaf / agent:orchestrator
relationship: delegation_outcome
claim: ika delegated work; child finished with status completed: <summary>
evidence: parent_session_id, child_role, duration_ms, source=subagent_stop_hook
confidence: 0.35
```

This is good for organic background learning, but do not over-trust it. A delegate summary is weaker evidence than explicit QA/review feedback.

Important current limitation: Hermes' `subagent_stop` hook exposes child role/status/summary, not a full named ocean profile identity or task goal. If you need “Kame gave feedback to Ika” or “Fugu reviewed Kani,” use explicit feedback records or add richer Kanban/profile lifecycle hooks.

### 2. Explicit feedback records

Use this when a reviewer, QA agent, designer, or product agent gives concrete feedback about another agent's work product:

```text
carabiner_record_feedback(
  observer="agent:kame-qa",
  subject="agent:ika-frontend",
  relationship="qa_handoff",
  repo="relay-ide",
  task_type="frontend",
  artifact_type="ui_change",
  raw_feedback="The handoff lacked screenshot and console evidence.",
  operational_critique="For frontend QA handoffs, include screenshot, console output, and loading/error state notes.",
  recommended_change="Attach browser evidence before asking QA to pass the task.",
  evidence={"issue":"#473", "source":"qa_review"},
  confidence=0.82
)
```

This is the highest-quality memory path because it is evidence-backed and scoped.

### 3. Handoff context retrieval

Before one agent hands work to another:

```text
carabiner_handoff_context(
  from_agent="agent:ika-frontend",
  to_agent="agent:kame-qa",
  repo="relay-ide",
  task_type="frontend",
  artifact_type="ui_change"
)
```

Inject the returned compact context into the handoff prompt/checklist. Do not dump huge raw memory into prompts.

### 4. Auditing raw records

```text
carabiner_audit(
  peer="agent:kame-qa",
  target="agent:ika-frontend",
  repo="relay-ide",
  task_type="frontend"
)
```

Use audit for debugging and curation, not routine prompt stuffing.

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
- Treat automatic delegation capture as low-confidence until reinforced by review/QA/user feedback.
- Fail soft: if Honcho is unavailable, tools return JSON errors instead of blocking the agent loop.
- Keep Honcho as a sidecar. Do not switch `memory.provider` away from Hindsight just to use Carabiner.
- Do not directly mutate the Honcho database/state for routine cleanup. Use public APIs only for explicitly scoped smoke-test cleanup or user-approved repair.

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

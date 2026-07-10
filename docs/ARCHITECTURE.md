# Architecture

## Components

### Runner (`runner/`)
A small scheduler (cron or a systemd timer per routine). For each routine file:
1. Parse frontmatter (schedule, model, allowed tools, budget).
2. Run non-LLM adapter steps directly as scripts; collect output.
3. If output differs from last run (or the routine is synthesis-type), invoke `claude -p` headless with the routine body as prompt, `--allowedTools` from frontmatter, `--max-budget-usd`, `--output-format json`.
4. Write results to `state/<routine>.json`; append to `logs/audit.jsonl`; hand alerts to the bot.

### Routines (`routines/*.md`)
Markdown with YAML frontmatter:

```yaml
---
name: site-health
schedule: "*/15 * * * *"
model: haiku            # cheap by default
llm: on-change          # never | on-change | always
adapters: [uptime, ssl_dns, forms]
allowed_tools: [Read, Bash(python3 adapters/*)]
budget_usd: 0.10
alert: telegram
---
Check every site in config/sites.yml ...
```

### Adapters (`adapters/`)
Plain scripts, one concern each, no LLM inside. Contract: read config, do the thing, print JSON to stdout, exit non-zero on failure. Adapters marked `destructive: true` in `config/systems.yml` refuse to run without an approval token issued by the bot.

### Bot (`bot/`)
Telegram (long-polling — no public endpoint needed). Outbound: alerts (deduped, cooldown), digest, approval requests (inline keyboard). Inbound: allowlisted user only → messages routed into a Claude Code session with the OS skills loaded; destructive intents produce an approval request rather than direct execution.

### Common record schema
Every unit of work, regardless of which routine/adapter produced it, normalizes to the same shapes in state and the audit log — this is what makes the dashboard, digest, and cost tracking uniform:

- **Task** — what was requested (routine trigger or operator command), which business it belongs to.
- **Run** — one execution: model used, tools allowed, cost, duration, outcome (`ok | degraded | failed | awaiting_approval`).
- **Artifact** — what it produced (state JSON, screenshot, draft note, report), with a path.
- **Approval** — tier, what was shown to the operator, decision, Telegram message ID, timestamp.
- **Event** — alerts and notable happenings (site down, cert expiring, anomaly).

Flat JSON lines, no database. The headline cost metric is **cost per accepted deliverable** (spend ÷ artifacts the operator actually used/approved), not raw tokens — raw spend appears in the digest, but the ratio is what tells you whether a routine earns its keep.

### Layer discipline
This system deliberately owns only the durable layers: the command surface (bot + dashboard), orchestration (routines), memory (state + knowledge bridge), and outcomes (audit + cost). It does **not** build an agent runtime, a model router, or an MCP execution layer — those belong to Claude Code / provider SDKs and are consumed through adapters. If a feature idea requires owning one of those layers, it's out of scope.

### State & memory (`state/`)
- `state/*.json` — latest result per routine; the dashboard's only data source.
- `state/agent-memory/` — now.md / tasks.md / log.md, read at session start, written at session end. Keep each file small; the runner truncates log.md beyond N entries (long memory files silently fall out of context).

### Dashboard (`dashboard/`)
Static HTML/JS reading `state/*.json`. No build step required to start; graduate to Astro if it grows. Served behind basic auth or an unguessable path.

### Skills (`skills/`)
See docs/SKILLS.md (Phase 4). A skill = trigger + procedure + allowed adapters + guardrails, in markdown. Skills are the growth mechanism: the core (runner/bot/dashboard) should almost never change.

## Model routing

| Work | Model |
|---|---|
| Adapter runs (no LLM) | none |
| Change triage, routine summaries | small/cheap |
| Morning digest synthesis, anomaly reasoning | strong |
| Conversation with the operator | strong |
| Drafting new skills/routines | strong, human-reviewed |

## Reference patterns this design borrows from

- danielmiessler/Personal_AI_Infrastructure (PAI/LifeOS) — skills + flat-file memory + daemon.
- mimurchison/claude-chief-of-staff — commands/, schedules.yaml, four-pillar layout.
- smixs/agent-second-brain — persistent session + Telegram, watchdog/self-heal, $5 VPS.
- kylemclaren/claude-tasks — cron + Claude Code + notification runner.
- Anthropic "Chief of Staff" cookbook — subagent delegation, hooks, output styles.

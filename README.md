# Agentic OS

A personal AI operations system for solo operators running multiple businesses. One always-on orchestrator agent (built on Claude Code / the Claude Agent SDK) that:

- **Watches everything** — uptime, SSL, DNS, deploy health, form endpoints, email deliverability — on a schedule.
- **Tells you when something's wrong** — alerts pushed to Telegram, plus a morning digest of everything that matters (deadlines, pipeline status, anomalies).
- **Acts when you tell it to** — you text it from your phone ("redeploy site X", "run a smoke test on everything", "what's due this week") and it executes through per-system adapters, with approval gates on anything destructive.
- **Grows over time** — new capabilities are added as skills, adapters, and routines (plain files), not code rewrites. The agent can learn new skills without touching the core.

## Why this exists

Running several businesses means dozens of small operational surfaces: websites, DNS, email infrastructure, bid pipelines, content calendars, client deliverables. Each one is easy to check; checking all of them every day is a job. This system makes that job an agent's job, and gives you a single chat thread to steer it.

## Architecture (short version)

```
┌─────────────┐     ┌──────────────────────────────┐
│  Telegram    │◄───►│  Orchestrator                │
│  (you, phone)│     │  Claude Code headless / SDK  │
└─────────────┘     │  on an always-on machine     │
                    └──────┬───────────────────────┘
                           │
        ┌──────────┬───────┴────────┬─────────────┐
        ▼          ▼                ▼             ▼
    routines/   adapters/        skills/      state/
    (cron jobs  (per-system     (learned      (JSON status
    defined as  control:        capabilities, files feeding
    markdown)   ssh, dns,       plain md)     the dashboard)
                playwright,
                email, ...)
```

- **Routines** are markdown files describing a scheduled job ("every 15 min, check all sites; every morning 7am, build the digest"). A cron-driven runner feeds them to Claude Code headless (`claude -p`) with a cheap model by default.
- **Adapters** are thin, auditable scripts (uptime checks, ssh deploy, DNS snapshot/update, Playwright smoke tests, form-endpoint tests). The agent calls adapters; adapters touch the world. Every call is logged.
- **The bot** bridges Telegram to the orchestrator: digests and alerts out, commands in. Destructive actions require an inline confirm-button tap.
- **The dashboard** is a static page rendered from agent-written `state/*.json`. No backend.
- **Skills** are how the system grows: teach the agent a new capability by writing a skill file, not by re-architecting.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [PLAN.md](PLAN.md) (phased build plan), and [SECURITY.md](SECURITY.md).

## Design principles

1. **Read-only by default.** Monitoring never needs write access. Write-capable adapters are opt-in per action.
2. **Approval gates on anything destructive.** DNS changes, service restarts, bulk sends → Telegram confirm button. Snapshot before change (DNS snapshot, VPS snapshot) where the platform supports it.
3. **Flat files over databases.** Routines, skills, and state are markdown/JSON — git-trackable, agent-editable, human-readable.
4. **Cheap models for collection, strong models for judgment.** Routine checks run on small models; synthesis, anomaly triage, and anything conversational runs on the strong model.
5. **Config is private, framework is public.** Your domains, hosts, and credentials live in a gitignored `config/` + `private/`. This repo ships with examples only.
6. **Audit everything.** Every adapter invocation appends to an append-only log: timestamp, tool, params, initiator, outcome.

## Status

**Live.** Phases 0–1 deployed and running in production (2026-07-10): scheduled site monitoring, Telegram alerts, daily digest — with cost guardrails (delta-triggered LLM calls, cheap-model routines, per-call and per-day budget caps, overlap locks). Currently in the one-week unattended soak ([#13](../../issues/13)) that gates Phase 2 (conversational control). See [PLAN.md](PLAN.md) for the roadmap and the issue tracker for progress.

### Cost model in practice
Adapters are plain scripts — **$0**. The LLM is only invoked when a check's status *changes* (haiku, ≤$0.10) or for the daily digest (strong model, ≤$1.00). A hard `daily_usd` cap is enforced by the runner: when reached, checks keep running and you get an alert, but no further LLM calls happen that day. Typical steady-state cost: **well under $1/day**.

## License

MIT — use it, fork it, build your own.

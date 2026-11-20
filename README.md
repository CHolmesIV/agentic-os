# Agentic OS

A personal AI operations system for solo operators running multiple businesses. One always-on orchestrator agent (built on Claude Code / the Claude Agent SDK) that:

- **Watches everything**, uptime, SSL, DNS, deploy health, form endpoints, email deliverability, on a schedule.
- **Tells you when something's wrong**, alerts pushed to Telegram, plus a morning digest of everything that matters (deadlines, pipeline status, anomalies).
- **Acts when you tell it to**, you text it from your phone ("redeploy site X", "run a smoke test on everything", "what's due this week") and it executes through per-system adapters, with approval gates on anything destructive.
- **Grows over time**, new capabilities are added as skills, adapters, and routines (plain files), not code rewrites. The agent can learn new skills without touching the core.

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
3. **Flat files over databases.** Routines, skills, and state are markdown/JSON, git-trackable, agent-editable, human-readable.
4. **Cheap models for collection, strong models for judgment.** Routine checks run on small models; synthesis, anomaly triage, and anything conversational runs on the strong model.
5. **Config is private, framework is public.** Your domains, hosts, and credentials live in a gitignored `config/` + `private/`. This repo ships with examples only.
6. **Audit everything.** Every adapter invocation appends to an append-only log: timestamp, tool, params, initiator, outcome.

## Quickstart

Needs Python 3.10+ and Node 18+. Everything below stays inside the clone.

```bash
git clone <this repo URL> agentic-os && cd agentic-os

# Python deps in a local venv, Node deps in node_modules
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm ci

# Real config is gitignored; start from the examples
cp config/sites.example.yml config/sites.yml
cp config/systems.example.yml config/systems.yml
cp .env.example .env

# Check the config parses and passes the rules
python runner/validate_config.py
# validate_config: OK, sites.yml and systems.yml are clean.

# Run one adapter against the sample site (example.com)
python adapters/uptime.py
```

`uptime.py` prints one JSON document: `status`, `http_status`, `latency_ms` and a
`detail` line per site. Edit `config/sites.yml` to point at your own domains.
Other adapters run the same way (`ssl_dns.py`, `latency.py`, `page_weight.py`, ...).
`hosting.py` needs `systems.vps_ip` set to your server's address.

Run a whole routine without any model call:

```bash
cp routines/site-health.example.md routines/site-health.md
python runner/run_routine.py routines/site-health.md --no-llm
```

Add `--dry-llm` to print the exact `claude -p` command it would run. The sample
config includes a form check against a local relay that does not exist on your
machine, so expect that one result to report a problem.

### Telegram (optional)

1. Create a bot with @BotFather and put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Put your numeric chat id in `TELEGRAM_CHAT_ID` and in
   `systems.telegram.allowed_user_ids` in `config/systems.yml`.
3. `set -a; source .env; set +a`, then run the runner again. Status changes now
   arrive as messages. Send a digest file with `node bot/send-digest.mjs path/to/file.md`.

Details: [bot/README.md](bot/README.md). Scheduling: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## What it looks like

Example output, with made-up sites.

An alert and its all-clear:

```
ALERT: shop.example.com [uptime] is down.
Detail: timeout after 10s
ts: 2026-03-14T06:15:58Z

RECOVERED: shop.example.com [uptime] is back to ok.
Prior alert: down at 2026-03-14T06:15:58Z, timeout after 10s
Detail: checked https://shop.example.com/
```

A morning digest:

```
Morning digest, Sat Mar 14

Red flags
- blog.example.com: SSL cert expires in 6 days

Sites: 9 up, 0 degraded, 0 down
Content: 1 site with title-length warnings
Hosting: all domains point at the VPS

Yesterday: 96 site-health runs, 1 model call (status change), $0.04

One suggestion: add a cert-renewal check to the deploy routine.
```

## Status

Working and in daily use on a small VPS: scheduled site monitoring (uptime, SSL and DNS,
latency, page weight, form relay health, content standards, hosting), Telegram alerts
with dedup and cooldown, a daily digest, and cost guardrails (model calls only on status
change, cheap-model routines, per-call and per-day budget caps, overlap locks).

Not built yet: conversational control from Telegram (commands in, with confirm-button
approvals), the dashboard, and the knowledge bridge. They are designed in
[PLAN.md](PLAN.md) and [docs/](docs/). This is a single-operator system: no multi-user
support, and config is flat YAML by design.

CI runs config validation, a Python compile check and `node --check` on every push.
Release history and lessons learned: [CHANGELOG.md](CHANGELOG.md).

### Models and cost

Adapters are plain scripts and cost nothing. The model is only called when a check's
status changes (the `haiku` alias, `claude-haiku-5-5`) or for the daily digest (the
`sonnet` or `opus` alias, or the CLI default with `strong`). Aliases map to model ids in
`runner/run_routine.py`. Spend is bounded by configurable caps: `budget_usd` per routine
in its frontmatter and `daily_usd` in `config/systems.yml`. When the daily cap is
reached, checks keep running and you get an alert, but no further model calls happen
that day. Prices change; check Anthropic's pricing page for current rates.

## License

MIT, use it, fork it, build your own.

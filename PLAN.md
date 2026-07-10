# Build Plan

Phased, each phase ships something usable on its own. Execute top to bottom; don't start a phase until the previous one has run reliably for a few days.

## Phase 0 — Foundations (prerequisites, ~1 session)

- [ ] **Secrets hygiene first.** Rotate any credential ever pasted into a chat/transcript or committed to a local doc. Move all secrets into a single `.env` (gitignored) or OS keychain. The orchestrator never gets a credential it doesn't need.
- [ ] Pick the always-on host (a home server, mini PC, or small VPS). Install Claude Code + Node + Python there.
- [ ] Create the Telegram bot via @BotFather; store the token in `.env`; restrict the bot to your own Telegram user ID (allowlist, hard fail otherwise).
- [ ] Fill in `config/sites.yml` and `config/systems.yml` from the examples (your real domains, hosts, checks). These files are gitignored.

## Phase 1 — Watch + tell me (monitoring & digest)

Goal: the agent notices problems before you do.

- [ ] `adapters/uptime.py` — HTTP checks per site from `config/sites.yml`: status code, latency, expected-content string, redirect sanity.
- [ ] `adapters/ssl_dns.py` — cert expiry (warn at 21/7/1 days), DNS record snapshot + diff vs last known-good (catches hijacks/typos).
- [ ] `adapters/forms.py` — POST a test submission to each form endpoint; expect the configured success/known-failure response (e.g. a relay intentionally returning 503 until configured is "known-degraded", not "down").
- [ ] `adapters/playwright_check.mjs` — per-site smoke: page renders, nav works, no console errors, screenshot to `state/screens/`. Reuse existing per-site smoke scripts where they exist.
- [ ] `runner/` — small cron-driven runner: reads `routines/*.md`, executes on schedule via `claude -p --allowedTools ... --max-budget-usd ...` with a cheap model; writes results to `state/<check>.json` and `logs/audit.jsonl`.
- [ ] `routines/site-health.md` — every 15 min: run uptime/ssl/forms adapters; only invoke the LLM when something changed (delta-triggered, keeps cost near zero).
- [ ] `routines/morning-digest.md` — daily 7:00: strong model reads all `state/*.json` + configured business sources, writes a digest, sends to Telegram.
- [ ] `bot/` v1 — outbound only: alerts (with cooldown/dedup so a flapping site doesn't spam you) + the digest.

**Done when:** you get a useful digest every morning and a Telegram ping within ~15 min of any site going down, for a week, without babysitting.

## Phase 2 — Talk to it (conversational control)

Goal: you text the bot and it does things.

- [ ] `bot/` v2 — inbound messages routed to a Claude session with the OS's skills/adapters available. Long-running work replies "on it", then follows up.
- [ ] Command surface (natural language, these are just the intents):
  - status / "how's everything" → live summary from `state/`
  - "smoke test <site|all>" → Playwright run, screenshots back in chat
  - "deploy <site>" → adapter runs the site's deploy runbook (build → rsync → nginx -t → reload → post-deploy smoke) — **confirm-gated**
  - "what's due" → deadlines from configured sources
- [ ] **Approval gates:** any adapter marked `destructive: true` in `config/systems.yml` sends an inline keyboard (✅ Approve / ❌ Cancel) and executes only on tap. Approvals are logged with message ID.
- [ ] Session memory: `state/agent-memory/` (now.md, tasks.md, log.md pattern) written at end of each run, read at start — continuity across headless runs without a DB.

**Done when:** you can run a deploy and a full smoke test from your phone, and a destructive command without approval is provably refused.

## Phase 3 — See it (dashboard)

Goal: one page that shows everything.

- [ ] `dashboard/` — static single-page app reading `state/*.json`: site grid (up/down/degraded, cert days, last deploy, screenshot thumbnail), business tiles (per-business status from routines), alert history, agent activity feed from the audit log.
- [ ] Serve it: nginx on the VPS behind basic auth or an unguessable path; or locally. Regenerated data only — the page itself never changes at runtime.
- [ ] Digest links to the dashboard.

**Done when:** the dashboard is your first tab in the morning instead of six admin panels.

## Phase 4 — Let it grow (skills & expansion)

Goal: adding a capability is writing a file, not a project.

- [ ] `skills/` — each skill is a markdown file (trigger, steps, adapters allowed, max risk tier, guardrails). The orchestrator loads them per run. Document the convention in `docs/SKILLS.md`.
- [ ] **Bounded-growth rule:** a new skill or agent role is only justified if it changes at least one of: context loaded, tools/permissions granted, evidence required, evaluation rubric, or cost/latency budget. If none change, it's the same capability with a new name — don't add it. This is the guard against skill sprawl.
- [ ] Skill-authoring skill: the agent can draft a new skill from a conversation ("learn how to check email deliverability"), you review the diff, merge it.
- [ ] Candidate expansions (each is one skill + maybe one adapter):
  - email infrastructure health (mailbox warmup, DMARC/SPF/DKIM checks, deliverability)
  - business pipeline watchers (bid deadlines, CRM follow-ups, content calendar gaps)
  - cost tracking (API spend per routine, monthly rollup in the digest)
  - migration runbook execution (site-by-site WP→static cutover as a supervised skill: mirror → deploy → verify → DNS flip with snapshot — each step confirm-gated)
  - social posting pipeline, client report generation, weekly business review
  - **knowledge-base bridge** — give the agent read access to a scoped projection of your notes vault (Obsidian or similar) and a write path via an agent-inbox folder, so digests are context-aware and findings get filed back as notes. The canonical vault stays wherever it lives (e.g. iCloud); the agent works against a git-synced mirror of an allowlisted subset — never the whole vault, never direct writes to canonical files. See `docs/KNOWLEDGE-BRIDGE.md`.
- [ ] Weekly self-review routine: agent reads its own audit log + failures, proposes routine/skill improvements as PRs to this repo.

**Done when:** you've added two new capabilities without touching runner/bot/dashboard code.

## Non-goals (for now)

- No multi-user support, no auth system beyond the Telegram allowlist + dashboard basic auth.
- No database. If state outgrows flat JSON, revisit — not before.
- The OS does not hold business content (proposals, client files); it points at systems that do.
- No proprietary agent runtime, model router, or visual workflow builder — consume Claude Code / provider SDKs through adapters (see "Layer discipline" in docs/ARCHITECTURE.md).
- No live agent-activity streaming or animated "virtual office" — watching agents work is theater, not operational value. Status, exceptions, and approvals are the product.
- No unscoped "giant shared brain" — all agent memory carries scope, source, and expiry (see docs/KNOWLEDGE-BRIDGE.md).

## Cost discipline

- Delta-triggered LLM calls: adapters run as plain scripts; the model is only invoked when output changed or on scheduled synthesis.
- Cheap model (`haiku`/small) for routine runs; strong model only for digest synthesis and conversation.
- `--max-budget-usd` on every headless invocation; monthly spend line in the digest.

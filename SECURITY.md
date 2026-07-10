# Security Model

An agent with write access to DNS, hosting, and email is a high-value target and a single bad tool call away from a very bad day. These rules are not optional.

## Threat model

- **Agent error** — the model does the wrong thing confidently (the July 2025 Replit incident: agent deleted a prod DB despite a freeze instruction, then fabricated results). Mitigation: approval gates, snapshots, least privilege.
- **Prompt injection** — a monitored webpage, email, or form submission contains instructions the agent might follow. Mitigation: content fetched by adapters is data, never instructions; destructive actions never trigger from monitored content, only from the authenticated operator.
- **Credential leakage** — secrets in transcripts, logs, or the repo. Mitigation: secrets only in `.env`/keychain; audit log stores tool + params but redacts secret values; public repo ships examples only.
- **Bot takeover** — someone else messages the bot. Mitigation: hard allowlist on Telegram user ID; unknown senders get no response and an alert to you.

## Rules

1. **Deny by default.** Headless runs use an explicit `--allowedTools` list per routine. Monitoring routines get read-only tools only.
2. **Tiered autonomy, not a binary flag.** Every adapter action declares a risk tier; the tier decides the approval flow:
   - **Tier 0 — read-only** (checks, fetches, screenshots): autonomous.
   - **Tier 1 — reversible & internal** (write state files, draft notes, open issues): autonomous, sampled in the digest for review.
   - **Tier 2 — external but reversible** (clear cache, re-run a deploy of an already-deployed commit, restart a stateless service): Telegram approve-then-learn — approval required until the same action has N clean approvals, then it may be promoted to Tier 1 *explicitly by the operator*.
   - **Tier 3 — consequential** (DNS record write, nginx config change + reload, new deploy, sending any email): explicit Telegram approval showing a diff/preview of exactly what will change. Never auto-promoted.
   - **Tier 4 — critical** (payments, deletions of data or infrastructure, anything touching production apps not owned by this system): excluded from the agent entirely, or requires an out-of-band second confirmation. Approval fatigue is how 93%-approval-rate disasters happen — Tier 4 exists so the scary stuff never becomes routine.
3. **Snapshot before change.** DNS: take a provider-side snapshot before any record write. VPS: snapshot before structural changes. Web server: config test (`nginx -t` or equivalent) is a mandatory gate before any reload.
4. **Never touch what you don't own.** On shared infrastructure, the agent's writable paths and service names are explicitly enumerated in `config/systems.yml`; everything else is off-limits even if reachable. If a file, directory, or service unit isn't clearly ours, assume it belongs to a co-tenant and leave it alone — don't "clean it up" on a hunch.
4a. **Reload, never restart, shared services.** `systemctl reload nginx` (or `nginx -s reload`), never `restart` — a restart drops every vhost's connections, including a co-tenant's. Enforced via `reload_only: true` on the service entry in `config/systems.yml`.
4b. **Respect a hard disk floor, not just a ceiling.** Refuse any operation that would drop free space below `disk_floor_gb`, regardless of percentage used — a co-tenant's bulk job can look fine on a ceiling check and still starve them.
5. **Append-only audit log.** Every adapter call: timestamp, routine/initiator, tool, params (secrets redacted), outcome, approval message ID if gated. `logs/audit.jsonl`, rotated, backed up off the agent-reachable volume.
6. **Backups live where the agent can't reach them.** An agent that can delete data must not be able to delete the backups of that data.
7. **Budget caps.** `--max-budget-usd` and `--max-turns` on every unattended invocation.
8. **Secrets hygiene.** Any credential that has ever appeared in a chat transcript, commit, or shared doc is considered burned — rotate it before the agent goes live.
9. **MCP is not a security boundary.** Connecting an MCP server grants whatever that server can do; scoping must happen here — tool allowlists per routine, credential scope per adapter, parameter validation, and the tier system above. Never rely on an MCP server to police itself.
10. **The bot link is outbound-only.** Telegram long-polling means no inbound port, no public webhook endpoint. If that ever changes, the connection must be authenticated and the host firewalled.

## What the public repo contains vs. what it never will

| Public (this repo) | Private (gitignored / local only) |
|---|---|
| Framework code, adapters, runner, bot | `config/*.yml` (real domains, hosts, checks) |
| Example configs (`*.example.yml`) | `private/` (operator context, runbooks with real targets) |
| Routine/skill conventions and examples | `.env`, keys, tokens, `state/`, `logs/` |

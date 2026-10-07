# Changelog

All notable changes to Agentic OS are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

Scope note: this repo is the **public framework**. Operator-specific config lives in
gitignored `config/*.yml`, `private/CONTEXT.md`, and `scripts/deploy.env` and is never
committed — where a change is environment-only, it is noted here but has no commit, and
hosts/domains are referred to generically. Production runs from `/opt/agentic-os` on a
VPS shared with another project; see `VPS_COTENANTS.md` on that host before any
VPS-level change.

---

## [2026-09-02] — Morning digest repaired; fleet config corrected

Working tree (uncommitted, deployed live): `runner/run_routine.py`, `routines/morning-digest.md`,
`adapters/ssl_dns.py`; env-only: `config/sites.yml`, `config/systems.yml`, `/etc/logrotate.d/agentic-os`.

- **morning-digest had failed every run since mid-July** with `error_max_turns`: the routine told
  the model to read `state/*.json` + `logs/audit.jsonl` and *write* `state/digest-latest.md`, but the
  runner passed `--allowedTools Read --max-turns 3`. Fix: for adapter-less routines the runner now
  inlines all `state/*.json` (minus backups/pending-alerts) and the last 24h/400 audit records into the
  prompt; new frontmatter key `digest_output` makes the runner write the model's `result` to that path;
  `--max-turns 6`; failed runs now record `total_cost_usd` from the raw JSON instead of null.
  First successful digest sent 2026-09-02 (about USD 0.45 on the CLI default model; consider mapping `strong`).
- `adapters/ssl_dns.py`: new per-site `dns_drift_expected: true` downgrades DNS drift to `ok` with a note
  (CDN-hosted sites rotate IPs; was ~96 false `degraded` results/day).
- `config/sites.yml` (env): wrong domain corrected (a plural typo pointed at a site the operator does
  not own); four migrated sites moved to `host: vps` with current notes; drift flag on two CDN sites.
  Form liveness checks were evaluated and NOT added: a well-formed test POST is accepted by the relay
  and sends a real email, so a 15-minute check would spam inboxes. Needs a relay health endpoint first.
- `config/systems.yml` (env): stale disk-usage comment corrected (12%, not 75%).
- Logrotate for `logs/*.jsonl` and `logs/*.log` (weekly, keep 8, compress, copytruncate).
- Known: `playwright_check` runs with zero targets (all sites `playwright: false`) while 646 MB of
  browsers sit in `~/.cache/ms-playwright`; working tree also carries uncommitted 2026-07-30 changes.

## [2026-07-30] — Site-health alerting: recovery hole closed (false outage on maricured.com)

Working tree at time of writing: `adapters/uptime.py`, `bot/alerts.mjs`,
`docs/SPEC-alert-delivery.md` modified; `scripts/validate_sites.py` new; `config/sites.yml`
changed on the VPS (gitignored). Deployed and verified in production before commit.

### Fixed — recovery alerts could go permanently silent

A real 10s uptime timeout on `maricured.com` paged correctly at `06:15:58Z`, but **no
all-clear ever arrived**, and the site went invisible to alerting for 13 hours.

Root cause was two reasonable-looking rules interacting badly:
- `config/sites.yml` had `expect_content: "Maricured"`, but the brand is **Maricure Nail
  Studio** — the domain has the D, the site does not. The string is not on the page, so
  the content check could never pass and the site sat at `degraded`, never `ok`.
- Per `docs/SPEC-alert-delivery.md` §3 (issue #17), `degraded` does not page and recovery
  was defined strictly as `-> ok`. A site recovering *into* `degraded` therefore fell
  between both rules: no alert, no recovery, and — critically — `evaluateEvent` returned
  early **without writing state**, so the key stayed pinned at `down` and every later
  transition was suppressed as "status unchanged". Log read `sent 0 messages` every run.

Fixes:
- **`bot/alerts.mjs`** — new `recoveryFor(event, prior)`. A recovery is now *either* a
  return to `ok` from any non-ok state, *or* a departure from `down` into anything less
  severe. Full → `RECOVERED`; partial → `PARTIAL RECOVERY: … no longer down (now
  degraded)`. Because a recovery always sends, it always writes state — that is what
  un-sticks the key. Paging set (`{down}`), dedup, and 30-min cooldown unchanged;
  `degraded` still never spams on its own, so DNS-drift suppression is preserved.
- **`config/sites.yml`** (VPS, gitignored) — `"Maricured"` → `"Maricure"`, with an inline
  comment on the domain/brand mismatch so it is not "corrected" back.
- **`docs/SPEC-alert-delivery.md`** — amendment marking #17 §3 superseded, with the
  revised predicate and added acceptance cases.

### Added — config errors are no longer indistinguishable from outages

The trigger was a typo that nothing could tell apart from a defacement.
- **`scripts/validate_sites.py`** (new) — fetches every site and validates its
  `expect_content` against live HTML, reporting case-mismatch and closest-prefix hints.
  Exit 1 if any site is misconfigured. Run after any `sites.yml` edit:
  `.venv/bin/python scripts/validate_sites.py`.
- **`adapters/uptime.py`** — additive `failure_kind` field on results
  (`content_mismatch`, `cross_domain_redirect`, `unreachable`, `None` when passing).
  No status values, thresholds, or timeouts changed.

### Verified
All 7 `expect_content` strings checked against live HTML — maricured was the only wrong
one, the other six genuinely match. 10-case truth table on the new recovery logic, all
pass. Live adapter run: **all 10 sites HTTP 200**. `nginx -t` OK, all `primewright-*`
units active, uptime timeout deliberately left at 10s. Rollback via
`.bak-20260730-192909` copies on the VPS + clean git `main`. **Proven in production:** the
19:30 cron tick emitted the first non-zero `sent 1 messages` in the log and delivered the
RECOVERED message.

### Known issues opened
- **`claude -p` fails ~88% of runs** — 30 failed / 4 ok in `logs/audit.jsonl`
  (`cost: null, outcome: failed`). Alert *delivery* is unaffected by design (#17's
  deterministic path is what sends), but the LLM classification step described in
  `routines/site-health.md` is mostly not running. Likely auth or a flag mismatch under
  cron. Highest-value open thread.
- **DNS "drift" on `akatsinc.com` + `fiveoakslawncare.com` is noise** — Hostinger's
  rotating shared load-balancer IPs (`147.79.x` snapshotted → `191.96.x`/`195.35.x`).
  Will flag forever; should compare provider/hostname rather than A/AAAA for
  `host: shared` sites.
- **Deliberately not changed** (would alter alerting sensitivity; wants its own pass):
  raising the 10s uptime timeout for `host: shared` sites, and requiring 2 consecutive
  failures before paging. The 06:15 page was arguably a false positive on a slow host.

### Note
This exact condition was recorded as a first-run finding on **2026-07-10** — *"maricured.com
content-check came back degraded (expected string not found — page changed or check needs
tuning)"* — and left for 20 days. A permanently-failing check is not cosmetic backlog; it
is a site that cannot report its own recovery.

---

## [2026-07-21]

### Security
- Real VPS host/key/user moved out of public scripts into gitignored `scripts/deploy.env`
  (loaded automatically); placeholders + `deploy.env.example` shipped in their place. (`9e2b7f1`)

---

## [2026-07-14]

### Fixed — issue #17: alerts never reached Telegram (`7258b07`, PR #18)
`site-health` detected down sites but nothing arrived. `bot/alerts-cli.mjs` (the queue
drainer) was never invoked, and `run_routine.py` queued *LLM output* that failed at
`budget_usd: 0.10` / max-turns. Alerting was redesigned to be **deterministic, driven off
adapter results, with the LLM out of the delivery path**:
- `runner/run_routine.py` flattens adapter results into normalized events and appends a
  pre-normalized `events` entry to `state/pending-alerts.json`, then invokes the sender.
- `bot/alerts-cli.mjs` returns `entry.events` as-is when present (backward compatible).
- `bot/alerts.mjs` — only `down` pages; first-seen `ok` and all `degraded` suppressed so
  DNS round-robin drift cannot spam.
- `budget_usd` raised `0.10 → 0.75` for `site-health` + `morning-digest`; the LLM summary
  is now non-critical.

Spec: `docs/SPEC-alert-delivery.md`. (Superseded in part on 2026-07-30 — see above.)

---

## [2026-07-11]

### Added
- Digest includes a Social section fed from PostDeck's `state/social.json` — schedule,
  unsubmitted count, failures, staleness. (`9d88db9`)

### Changed
- **Reframe:** grounded query + capture is the core purpose; social becomes its own app.
  Re-added CB-initiated capture inbox (agent as scribe — **no autonomous vault writes**).
  Added VPS split-trigger watch. (`555a783`)
- Phase 4 narrowed to personal-ops (social posting, site deploys, read-only vault explorer,
  cost); financial + business-ops explicitly excluded per operator decision. (`913ba44`)

---

## [2026-07-10] — Phases 0–1 built, deployed, LIVE (single day)

### Added
- Framework plan, architecture, security model, example configs and routines. (`9f35d24`)
- Knowledge-bridge design (vault projection + agent inbox); Phase 4 expanded. (`9ccacf6`)
- Spec upgrades from market research: tiered autonomy (0–4), common record schema, layer
  discipline, bounded-growth rule, memory provenance. (`5e24bb2`)
- **Phase 1 build:** `uptime` / `ssl_dns` / `forms` adapters, cron-driven runner with audit
  log, schedule installer, live routines, operations doc. (`cae0d65`)
- **Bot v1:** Telegram outbound (alerts with dedup/cooldown, digest sender, allowlisted
  poller) + Playwright smoke adapter. (`49ec6b5`)
- One-command VPS deploy scripts (`deploy-to-vps`, `push-secrets`) + centralized `.env`
  template. (`889846f`)
- README: live status + cost model. (`a2c0fc1`)

### Added — cost guardrails, built in rather than bolted on (`7335db4`)
LLM invoked only on status-change (delta-triggered) or for the daily digest; haiku for
routines, strong model only for digest/conversation; hard `--max-budget-usd` and
`--max-turns 3` per call; **enforced `daily_usd` cap** in the runner (checks keep running,
LLM calls stop, CB is alerted); `flock` on every cron entry so a hung run cannot stack.
Steady-state target: well under $1/day.

### Fixed — deploy-time bugs (useful precedent for VPS work)
- Ubuntu 24.04 blocks system-wide pip (PEP 668) → project venv. (`f61807a`)
- Cron runs with an empty environment → routines source `.env`; venv python used. (`bb81d58`)
- Empty crontab crashes `crontab -l` under `pipefail` → tolerated. (`941991c`)

### Security — `never_touch` protected paths that did not exist (`aa0712b`)
A cross-session PrimeWright audit found the co-tenancy guard listed
`/var/www/primewright.com` and `/var/www/app.primewright.com` — **neither exists**.
Corrected against the live filesystem to the real footprint: `/opt/primewright`, the three
nginx confs (sites-available *and* sites-enabled), `/var/backups/primewright`, plus new
non-path guards `never_touch_units: primewright-*` and `never_touch_databases: [primewright]`.
Shared contract doc: `/opt/VPS_COTENANTS.md`.

### Changed
- Phase 0 reframed: secrets step is **centralization**, not rotation (keys confirmed
  current). (`8a4dc5c`)
- GitHub username `dodgein2` → `CHolmesIV`; VPS co-tenancy safety rules added
  (reload-not-restart, disk floor). (`63bc1f0`)
- Host decision locked: the orchestrator runs on the VPS, not the HP Z2 Mini.

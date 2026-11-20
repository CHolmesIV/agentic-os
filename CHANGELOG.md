# Changelog

Notable changes to Agentic OS, newest first. Operator-specific config lives in
gitignored `config/*.yml` and `scripts/deploy.env` and is never committed.

## Unreleased

- VPS address now comes from `systems.vps_ip` in `config/systems.yml`. There is no
  built-in default; with it unset, the hosting adapter reports a clear error.
- Config examples, docs and routines use placeholder domains only.
- Model aliases in `runner/run_routine.py` updated; GitHub Actions CI added.

## Content standards and hosting checks

- `adapters/content_standards.py`: daily crawl of sitemap and homepage. `broken` pages
  (noindex, missing analytics id, dead internal link). `warn` never pages (house style,
  title and description length, missing canonical).
- Link crawl reads only page markup. Inline script templates such as `href="${url}"` were
  once reported as 404s on every cycle. Comments, `<script>`, `<style>`, `<template>` and
  `<noscript>` bodies are now stripped first.
- `adapters/hosting.py`: every `host: vps` site must resolve to the VPS, or it returns
  `off_vps`, which pages. Catches sites that were staged on the server but never cut over in DNS.
- `reports/traffic_report.py`: weekly first-party traffic report from nginx logs. Bots and
  scanner IPs are filtered; visitors are counted from a truncated hash.

## Spam-safe form monitoring, sustained latency and page weight

- `adapters/forms.py` never submits a form. It reads the local form relay's `GET /health`.
  A test POST every 15 minutes would have emailed a real lead each time.
- `adapters/latency.py`: `slow` only when the last-hour median is over budget and at least
  2x the 7-day baseline. One slow sample never pages.
- `adapters/page_weight.py`: daily homepage wire bytes. `heavy` above 3 MB total or 1 MB per asset.
- `ssl_dns.py`: per-site `dns_drift_expected` for CDN-hosted sites whose IPs rotate.

## Alert delivery redesign and recovery fix

Lessons worth keeping:

- Alerting is deterministic and driven by adapter results. The LLM is not in the delivery
  path, so a failed or over-budget model call can never swallow an alert.
- Recovery must be defined as "a return to ok, or a departure from down into anything less
  severe". When recovery was only `-> ok`, a site that recovered into `degraded` matched
  neither the paging rule nor the recovery rule. State was never written, the key stayed
  pinned at `down`, and every later transition was suppressed.
- A permanently failing check is not cosmetic. The root cause there was a wrong
  `expect_content` string, which left the site stuck at `degraded` and unable to report its
  own recovery. `scripts/validate_sites.py` checks every `expect_content` against live HTML
  and exits 1 on a mismatch. Run it after editing `sites.yml`.
- `adapters/uptime.py` adds a `failure_kind` field (`content_mismatch`,
  `cross_domain_redirect`, `unreachable`) so a config mistake can be told apart from an outage.
- Full spec: [docs/SPEC-alert-delivery.md](docs/SPEC-alert-delivery.md).

## Digest and runner fixes

- A routine that must read files and write output needs enough turns and tools. The digest
  failed on every run because the runner allowed only `Read` and 3 turns. The runner now
  inlines `state/*.json` and the recent audit log into the prompt, and a `digest_output`
  frontmatter key writes the result to disk.
- Per-day cost cap (`daily_usd`) is enforced by the runner. Checks keep running and you get
  an alert, but no further model calls happen that day.
- `flock` on every cron entry so a hung run cannot stack.

## Initial release

- Framework plan, architecture, security model (tiered autonomy 0 to 4), example configs and routines.
- Adapters: `uptime`, `ssl_dns`, `forms`, Playwright smoke check.
- Cron-driven runner with an audit log and schedule installer.
- Telegram bot: alerts with dedup and cooldown, digest sender, allowlisted poller.
- One-command VPS deploy and secrets scripts, centralized `.env` template.

Deploy lessons:

- Ubuntu 24.04 blocks system-wide pip (PEP 668). Use a project venv.
- Cron runs with an empty environment. Source `.env` and call the venv python.
- An empty crontab makes `crontab -l` fail under `pipefail`. Tolerate it.
- A co-tenancy guard (`never_touch`) is worthless if it lists paths that do not exist.
  Verify every entry against the live filesystem.

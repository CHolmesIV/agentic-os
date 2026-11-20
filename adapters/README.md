# Adapters

Plain scripts, one concern each, no LLM inside. The agent calls adapters; adapters touch the world.

## Contract

- **Config in**, every adapter reads `config/sites.yml` and/or `config/systems.yml` directly. No arguments carry secrets; secrets (when needed) come from env only.
- **JSON out**, exactly one JSON document to stdout per run:
  ```json
  {
    "adapter": "uptime",
    "ts": "2026-07-10T12:00:00+00:00",
    "results": [
      {"domain": "example.com", "status": "ok|degraded|down", "http_status": 200, "latency_ms": 123.4, "detail": "..."}
    ]
  }
  ```
  Adapters with a richer result shape (e.g. `forms.py`'s `known-degraded` status, `ssl_dns.py`'s DNS-drift detail) still emit this envelope, only `results[].status`'s value set and `detail` content vary.
- **Exit codes**, a down/degraded *site* is a result, not a failure: exit `0`. Non-zero exit means the *adapter* couldn't run at all (bad/missing config, no interpreter, etc.), the runner treats that as an alert in itself.
- **Read-only by default**, `uptime.py` and `ssl_dns.py` never write anything the site fleet would notice; `ssl_dns.py` writes only to local `state/dns/<domain>.json` for drift detection. `forms.py` never submits anything; it only reads the local form relay's `/health` endpoint.
- **Logged**, every invocation is expected to be appended to `logs/audit.jsonl` by the runner, not the adapter itself.

## Tiers (see SECURITY.md)

| Adapter | Tier | Why |
|---|---|---|
| `uptime.py` | 0 | Read-only HTTP GET |
| `ssl_dns.py` | 0 | Read-only TLS handshake + DNS lookups; local-only snapshot write |
| `forms.py` | 0 | Read-only GET to the local relay's health endpoint |
| `playwright_check.mjs` | 0 | Read-only browser smoke test |

Adapters never decide their own tier at runtime, the tier is a property of the *action*, declared in `config/systems.yml` / this table, and enforced by the runner and bot before an adapter with side effects is ever invoked without dry-run.

## Current adapters

- `uptime.py`, HTTP status, latency, expected-content match, redirect sanity (allows www↔apex, flags cross-domain), `check_host` override for sites still on old DNS.
- `ssl_dns.py`, TLS cert expiry (warn 21/7/1 days), DNS record snapshot (A/AAAA/CNAME/MX/TXT) + diff vs. last snapshot.
- `forms.py`, asks the local form relay's `/health` whether each relay-backed form can send mail; classifies `ok` / `known-degraded` / `down`. Never submits a form.
- `playwright_check.mjs`, page render, nav, console-error, screenshot smoke test (Node/Playwright).

## Adding a new adapter

Same contract: read the relevant config, do the one thing, print the JSON envelope, exit 0 unless the adapter itself broke. Add its tier to the table above.

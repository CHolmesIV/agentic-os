# Spec — Deterministic site-health alert delivery (issue #17)

## Problem
`site-health` detects down sites but alerts never reach Telegram. `bot/alerts-cli.mjs`
(the queue drainer) is never invoked; `run_routine.py` queues the LLM output, which
fails at `budget_usd: 0.10`/max-turns. See issue #17 for the full root cause.

## Design — alerting is deterministic, off adapter results, LLM not in the path

1. **`runner/run_routine.py`** — after merged_state is written, for any routine whose
   frontmatter has `alert: telegram`:
   - Flatten `current_docs` (adapter results) into normalized events:
     `{ domain, kind: <adapter name>, status, detail, ts }`, one per adapter result.
   - Append a pending entry `{ routine, task_id, ts, events: [...] }` to
     `state/pending-alerts.json` (NOTE: `events` key, pre-normalized — not the raw
     `llm_result`). The `llm_result` still goes into `state/<routine>.json` for the
     digest, but is no longer the alert source.
   - Invoke the sender: `subprocess.run(["node", "bot/alerts-cli.mjs"], check=False)`
     — same hand-off pattern as the existing `send-digest.mjs` call.

2. **`bot/alerts-cli.mjs`** — in `eventsFromPendingEntry`, if `entry.events` is a
   non-empty array, return it as-is (already normalized). Otherwise keep the existing
   `llm_result.results` / fallback behavior (backward compatible).

3. **`bot/alerts.mjs`** — fix the healthy-status bug in `evaluateEvent`. Only these
   statuses page: `down`. Rule:
   - `recovered` = `status === "ok" && prior && prior.status !== "ok"` (unchanged).
   - If NOT recovered AND status is not in the paging set (`{"down"}`) → `shouldSend:false`
     (`reason: "non-paging status"`). This suppresses first-seen `ok` AND all `degraded`
     (DNS round-robin drift, HTTP-only entries, etc.) so they never spam. Dedup + 30-min
     cooldown still apply to `down`.

4. **`routines/site-health.md` + `routines/morning-digest.md`** — raise `budget_usd`
   `0.10 → 0.75`. If a max-turns cap is expressed in frontmatter, raise it enough to
   let the summary complete (the LLM summary is now non-critical; delivery no longer
   depends on it).

## Out of scope for the code change (handled at deploy)
- `config/sites.yml` (gitignored, per-environment): mark migrated sites live over HTTPS,
  add `form` checks for di-hy/cholmesiv/lunula/sidibe, fix the akats Playwright target.

## Acceptance
- Synthetic `down` event → one Telegram message.
- Healthy (`ok`) and `degraded` events → no message.
- `down` → `ok` transition → one RECOVERED message.
- Repeated `down` within 30 min → no duplicate.
- `npm run check` passes; `python3 -m py_compile runner/run_routine.py` passes.
- No secrets committed.

---

## Amendment — 2026-07-30: recovery must fire on leaving `down`, not only on reaching `ok`

Issue #17's rule (§3 above) is **superseded**. As written it created a silent
hole, hit in production by `maricured.com` on 2026-07-30.

### What happened
1. `maricured.com` genuinely timed out (10s) at `06:15:58Z` → `down` → paged. Correct.
2. It recovered ~15 min later, but its `expect_content` in `config/sites.yml` was
   `"Maricured"` while the brand is **Maricure Nail Studio** — the string is not on the
   page, so the check could never pass. Status became `degraded`, never `ok`.
3. Per §3, `degraded` is not in the paging set and was not `ok`, so it was neither an
   alert nor a recovery. `evaluateEvent` returned early **without updating stored state**,
   so the key stayed pinned at `down` forever and every later transition was suppressed
   as "status unchanged". Result: `sent 0 messages` for 13 hours, no all-clear, and the
   site was invisible to alerting from that point on.

The bug is the interaction of two reasonable-looking rules: suppressing `degraded` to
kill DNS-drift spam (correct), and defining recovery as strictly `-> ok` (too narrow).
Any site that recovers *into* a degraded state falls between them and goes dark.

### Revised rule (implemented in `bot/alerts.mjs` as `recoveryFor(event, prior)`)
```
recovered = (prior.status === "down" && event.status !== "down")   // left down
         || (prior.status !== "ok"   && event.status === "ok")     // reached ok
```
- Full return to `ok` → `RECOVERED: … is back to ok.`
- Departure from `down` into a less-severe state → `PARTIAL RECOVERY: … is no longer
  down (now degraded).`
- Everything else is unchanged: only `down` pages, `degraded` still never spams on its
  own, dedup + 30-min cooldown still apply.

Because a recovery always sends, it always writes state — which is what un-sticks the
key. That is the actual fix; the message wording is secondary.

### Config errors must not look like outages
The trigger was a typo, and nothing could tell a typo from a defacement. Added:
- **`scripts/validate_sites.py`** — fetches each site and validates `expect_content`
  against live HTML, with case-mismatch and closest-prefix hints. Run after any
  `sites.yml` edit: `.venv/bin/python scripts/validate_sites.py`. Exit 1 if any site is
  misconfigured.
- **`failure_kind`** on uptime results (`content_mismatch`, `cross_domain_redirect`,
  `unreachable`, `None` when passing) — additive field, no status/threshold changes.

Note this exact condition was recorded as a first-run finding on **2026-07-10**
("expected string not found — page changed or check needs tuning") and left for 20 days.
A permanently-failing check is not cosmetic backlog; it is a site that cannot report its
own recovery.

### Added acceptance cases
- `down` → `degraded` → one PARTIAL RECOVERY message (was: silently nothing).
- `degraded` → `ok` → one RECOVERED message.
- `ok` → `degraded` → still no message (drift suppression preserved).
- `scripts/validate_sites.py` exits 0 against the live fleet.

### Still open
`claude -p` fails ~88% of runs (30 failed / 4 ok in the audit log; `cost: null`).
Alert *delivery* is unaffected — that is the whole point of #17's deterministic path —
but the LLM classification step described in `routines/site-health.md` is mostly not
running. Separate issue; likely auth or a flag mismatch under cron.

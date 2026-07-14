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

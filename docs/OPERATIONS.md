# Operations

## Manual runs

Run any routine by hand for testing or ad hoc checks:

```bash
python3 runner/run_routine.py routines/site-health.md
```

Useful flags:

- `--no-llm` — run adapters only, skip the `claude -p` call entirely.
- `--dry-llm` — build the exact `claude -p` command that *would* run and
  print it instead of executing it. Nothing is sent to the model.
- `--adapters-dir DIR` — use adapters from a different directory (handy for
  testing with fake adapters instead of the real ones).
- `--state-dir DIR`, `--logs-dir DIR` — same idea, for isolating test output
  from real `state/` and `logs/`.

Exit code is `0` unless the runner itself failed to execute (a routine that
comes back degraded/failed is a normal outcome and still exits `0`).

## Installing schedules

```bash
runner/install_schedule.sh              # install/update cron entries
runner/install_schedule.sh --uninstall  # remove agentic-os cron entries
```

The installer reads the `schedule:` frontmatter field from every
`routines/*.md` file (skipping `*.example.md`) and writes a marker-delimited
block into the current user's crontab. Re-running it replaces that block, so
it's safe to run after adding or editing routines. Cron is the primary,
supported target (Ubuntu 24.04 VPS). If `crontab` isn't available (e.g. some
macOS setups), it warns and exits without installing — there's no launchd
fallback baked into this script; that's an optional local-dev nicety you can
add yourself if you need it.

Each cron entry redirects stdout/stderr to `logs/cron-<routine>.log`.

## State & logs layout

```
state/
  <routine-name>.json     # latest merged result per routine
  pending-alerts.json     # queued telegram alerts, picked up by the bot
  digest-latest.md        # most recent morning-digest output
logs/
  audit.jsonl             # append-only Task/Run/Event records (JSON lines)
  cron-<routine>.log      # raw stdout/stderr from each cron invocation
```

Both directories are gitignored — they hold real operational data, not
framework code.

## Reading the audit log

Each line in `logs/audit.jsonl` is one JSON record: `Task` (a routine
trigger), `Run` (one execution — adapter or `claude -p` call), or `Event`
(alerts, notable happenings). Any field whose key matches
`token|key|password|secret` is redacted before it's written.

Quick looks:

```bash
tail -n 20 logs/audit.jsonl | jq .
grep '"outcome":"failed"' logs/audit.jsonl | jq .
jq -s 'map(select(.type=="Run" and .routine=="site-health"))' logs/audit.jsonl
```

## Troubleshooting

- **Routine never fires** — check `crontab -l` for the marker block, check
  `logs/cron-<routine>.log` for stderr, confirm `schedule:` in the routine's
  frontmatter is valid cron syntax.
- **State file never updates** — run the routine manually without `--dry-llm`
  and check for a traceback; state is only written on a real (non-dry) run.
- **`claude -p` never seems to run** — check `llm:` mode in frontmatter.
  `never` skips it always; `on-change` only invokes it when adapter output
  differs from the previous state (compared on per-domain `status` only,
  ignoring timestamps/latency).
- **Digest never reaches Telegram** — confirm `state/digest-latest.md` exists
  after a morning-digest run and that `bot/send-digest.mjs` exists; the
  runner warns to stderr (in the cron log) if either is missing, and doesn't
  hard-fail.
- **Alerts not showing up** — alerts are written to
  `state/pending-alerts.json`; the bot is responsible for reading and
  clearing that file. If alerts pile up unread, check the bot process, not
  the runner.

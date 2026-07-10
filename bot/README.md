# Telegram Bot

Outbound-only bridge between the orchestrator and Telegram: alerts, morning
digest, and (later) inbound operator commands. Long-polling only — no
inbound port, no public webhook (SECURITY.md #10).

## Setup

### 1. Create the bot with BotFather

1. Open Telegram, message **@BotFather**.
2. `/newbot`, follow the prompts (name + a unique username ending in `bot`).
3. BotFather returns a token like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
   This is `TELEGRAM_BOT_TOKEN`. Treat it as a secret — anyone with the
   token can send messages as your bot and read its updates.

### 2. Get your numeric Telegram user id

1. Message **@userinfobot** (or **@getidsbot**) — it replies with your
   numeric user id.
2. That number is what goes in `config/systems.yml` under
   `systems.telegram.allowed_user_ids: [your_id]` — this is the hard
   allowlist `telegram.mjs`'s `pollUpdates()` checks before acting on any
   inbound message. Anyone else is ignored and reported (see below).

### 3. Get your chat id

The simplest path: send your bot any message, then call
`https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser — the
response includes `message.chat.id`. For a personal bot talking only to
you, `chat.id` and your user id are usually the same number. Put it in
`TELEGRAM_CHAT_ID`.

### 4. Env vars

Copy `.env.example` to `.env` (gitignored) at the repo root and fill in:

```
TELEGRAM_BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
```

`bot/telegram.mjs` reads these from `process.env` — load them however you
run the process (`source .env`, a process manager's env file, systemd
`EnvironmentFile=`, etc.). Nothing in this repo auto-loads `.env`.

## Allowlist behavior

`config/systems.yml`:

```yaml
systems:
  telegram:
    allowed_user_ids: [123456789]
```

- **Not configured** — the key is missing, an empty list, or still the
  example placeholder `[0]`. In this state, `pollUpdates()` logs
  `allowlist not configured ... refusing to start` and returns
  immediately without opening a long-poll loop. `sendMessage()` still
  works (alerts/digest can go out to `TELEGRAM_CHAT_ID` even before the
  allowlist is set up).
- **Configured** — inbound messages from a `from.id` in the list are
  handed to the caller's `onMessage(update)` callback. Messages from any
  other id are dropped (no reply sent to that sender) and an alert is
  sent to the operator's own `TELEGRAM_CHAT_ID`: `Unauthorized Telegram
  sender ignored: user_id=... (@username) — text: ...`.

## Alerts: dedup + cooldown (`bot/alerts.mjs`)

Alerts are normalized to Event objects: `{domain, kind, status, detail,
ts}`. State persists in `state/bot-alerts.json` as
`{"<domain>:<kind>": {status, ts, detail}}`.

- **Dedup** — the same `domain:kind` key with an unchanged `status` never
  re-alerts.
- **Cooldown** — even a status *change* on the same key won't send again
  within 30 minutes of the last alert for that key, to avoid flapping
  spam.
- **RECOVERED is always sent** — a transition to `status: "ok"` after a
  prior non-ok alert bypasses the cooldown and sends immediately, quoting
  the prior alert's status/timestamp/detail so the recovery message has
  context without needing to scroll back.

## CLI mode (`bot/alerts-cli.mjs`)

```
node bot/alerts-cli.mjs
```

Reads `state/pending-alerts.json` (written by `runner/run_routine.py`
whenever a routine's frontmatter sets `alert: telegram` and an LLM result
was produced), converts each queued entry into one or more Events, runs
them through the dedup/cooldown logic above, sends via `telegram.mjs`, and
clears the pending-alerts file — but only if every send in the batch
succeeded. On partial failure the file is left in place so the next run
retries.

## Digest (`bot/send-digest.mjs`)

```
node bot/send-digest.mjs state/digest-latest.md
```

Sends the file's contents as a Telegram message, split into chunks of at
most 4000 characters (Telegram's message length limit is ~4096; 4000
leaves headroom). Called automatically by the runner after the
`morning-digest` routine.

---
name: morning-digest
schedule: "0 7 * * *"
model: sonnet
llm: always
adapters: []
budget_usd: 1.00
alert: telegram
digest_output: state/digest-latest.md
---

# Morning Digest

Read all `state/*.json` and produce one Telegram-sized briefing:

1. **Red flags first** — anything down, degraded, expiring (SSL < 21d), or a
   deadline within 72h. If nothing: "All green."
2. **Sites** — one line: N up, N degraded, N down; anything notable.
   Speed: mention a site's latency ONLY if its `latency` result is `slow`,
   and page weight ONLY if `page_weight` is `heavy` (name the largest
   asset). Do not narrate raw response times or compare them day to day —
   single samples are noise and those numbers mean nothing to the reader.
   Forms: one line — relay-backed forms ok, or which site's form is down.
   Content: one line from `content_standards` — any `broken` site first
   (noindex, missing analytics, dead link), then a count of sites with
   house-style `warn` items (em dashes, title 30-60 / description 70-160
   chars). Hosting: name any `off_vps` site.
3. **Deadlines & pipeline** — from configured business sources (bids due,
   client deliverables, filings).
4. **Yesterday's agent activity** — read the tail of `logs/audit.jsonl` for
   the last 24 hours: which routines ran, adapters invoked, any Task/Run
   records with `outcome: failed` or `awaiting_approval`, and total spend
   (sum of run costs) for the period. Call out anything still waiting on
   approval.
5. **Social** — if `state/social.json` exists, include a "Social" section:
   posts going out today/tomorrow per brand, count of unsubmitted approved
   posts, any failures (flag prominently), and note the data's
   `generated_at` age if older than 24h. If it is older than 7 days, replace
   the whole section with one line: "Social: feed stale since <date> —
   PostDeck isn't publishing state." Do not list its stale contents.
6. **One suggestion** — the single highest-leverage thing to fix or automate
   next, based on recurring noise in the logs. Known and by design — do not
   suggest these: site-health re-queues its result to pending-alerts.json
   every cycle (alerts.mjs dedups; required for cooldown correctness).

Tone: direct, operator-to-operator, no filler. Hard cap ~300 words.

Output ONLY the finished digest text, nothing else.

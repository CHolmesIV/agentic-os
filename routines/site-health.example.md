---
name: site-health
schedule: "*/15 * * * *"
model: haiku
llm: on-change
adapters: [uptime, ssl_dns, forms]
budget_usd: 0.10
alert: telegram
---

# Site Health Sweep

For every site in `config/sites.yml`, the adapters have produced fresh results
(uptime, SSL/DNS, form endpoints). You are invoked only because something
changed since the last run.

1. Compare current results to `state/site-health.json` (previous).
2. Classify each change: RECOVERED, DEGRADED (known/expected state, e.g. a
   relay intentionally returning 503 until configured), or DOWN.
3. DOWN or unexpected DEGRADED → emit an alert (site, what failed, since when,
   first debugging step). RECOVERED → emit an all-clear referencing the
   original alert.
4. Never propose or take a corrective action, this routine is read-only.
   Suggest the fix; the operator triggers it.

Write the full result set to `state/site-health.json`.

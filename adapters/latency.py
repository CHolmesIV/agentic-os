#!/usr/bin/env python3
"""Latency adapter — Tier 0, read-only.

The uptime adapter owns up/down. This one only answers "is a site slow,
consistently?" so a single slow sample never pages anyone.

Each cycle it takes one HTML-only GET per site and appends it to a 7-day
rolling history in state/history/latency.json (a subdirectory, so the digest
does not inline thousands of samples). A site is `slow` only when:

  - the median of the last 4 samples (~1 hour) is over the site's budget, AND
  - either there is no usable baseline yet, that median is at least 2x the
    7-day baseline median, or the baseline itself is over budget (chronic).

Budget: `latency_budget_ms` on the site in config/sites.yml, else 800 ms for
`host: vps` (measured from this box, so normally well under 100 ms) and
2000 ms for anything hosted elsewhere. Sites checked via `check_host` (raw IP
+ Host header) are skipped; their numbers say nothing about real visitors.

A failed request records no sample and reports `ok` — outages are the uptime
adapter's job, and double-paging on one event is noise.
"""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
HISTORY_PATH = REPO_ROOT / "state" / "history" / "latency.json"
TIMEOUT_SECONDS = 10
USER_AGENT = "agentic-os-latency/1.0"
WINDOW = 4                 # recent samples (15-min cadence -> ~1 hour)
MIN_BASELINE = 24          # ~6 hours of older samples before a baseline counts
RETAIN_SECONDS = 7 * 86400
DEFAULT_BUDGET_MS = {"vps": 800}
FALLBACK_BUDGET_MS = 2000


def load_sites() -> list[dict]:
    with SITES_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data.get("sites", [])


def load_history() -> dict:
    try:
        return json.loads(HISTORY_PATH.read_text())
    except Exception:  # noqa: BLE001 — missing or corrupt history just restarts the window
        return {}


def save_history(history: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(history))
    tmp.replace(HISTORY_PATH)


def sample(domain: str) -> float | None:
    start = time.monotonic()
    try:
        resp = requests.get(
            f"https://{domain}/",
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
            allow_redirects=True,
        )
        resp.content  # include body transfer, matching the uptime adapter
    except requests.exceptions.RequestException:
        return None
    if resp.status_code >= 400:
        return None
    return round((time.monotonic() - start) * 1000, 1)


def classify(samples: list[list[float]], budget: float) -> tuple[str, str]:
    values = [ms for _, ms in samples]
    if len(values) < WINDOW:
        return "ok", f"collecting samples ({len(values)}/{WINDOW}); budget {budget:.0f}ms"
    recent = statistics.median(values[-WINDOW:])
    older = values[:-WINDOW]
    baseline = statistics.median(older) if len(older) >= MIN_BASELINE else None
    base_txt = f"7d baseline {baseline:.0f}ms" if baseline is not None else "no baseline yet"
    summary = f"last-hour median {recent:.0f}ms, {base_txt}, budget {budget:.0f}ms"
    if recent <= budget:
        return "ok", summary
    if baseline is None or recent >= 2 * baseline or baseline > budget:
        return "slow", summary
    return "ok", summary + " (over budget but within 2x baseline)"


def main() -> int:
    try:
        sites = load_sites()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"adapter": "latency", "error": str(e)}), file=sys.stderr)
        return 1

    history = load_history()
    now = time.time()
    results = []
    for site in sites:
        domain = site.get("domain")
        checks = site.get("checks", {}) or {}
        if not domain or not checks.get("uptime", True) or site.get("check_host"):
            continue
        budget = float(site.get("latency_budget_ms")
                       or DEFAULT_BUDGET_MS.get(site.get("host"), FALLBACK_BUDGET_MS))
        samples = [s for s in history.get(domain, []) if now - s[0] <= RETAIN_SECONDS]
        ms = sample(domain)
        if ms is not None:
            samples.append([round(now), ms])
        history[domain] = samples
        status, detail = classify(samples, budget)
        if ms is None:
            detail = "no sample this cycle (request failed; uptime adapter reports outages); " + detail
        results.append({"domain": domain, "status": status, "latency_ms": ms, "detail": detail})

    # Drop sites removed from config.
    configured = {s.get("domain") for s in sites}
    history = {d: v for d, v in history.items() if d in configured}
    save_history(history)

    print(json.dumps({
        "adapter": "latency",
        "ts": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

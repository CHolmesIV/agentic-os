#!/usr/bin/env python3
"""Forms adapter — Tier 0 in --dry-run, Tier 2 for a real submission.

Reads config/sites.yml. For each site with a `checks.form` block, POSTs a
clearly-marked automated test payload (honeypot field left empty) to
`<domain><endpoint>` and classifies the result:
  - ok              : response status matches the site's happy-path expectation
  - known-degraded  : response status matches the configured expect_status
                       and that status is >= 400 (an intentionally-tolerated
                       failure mode, e.g. the akatsinc.com relay's 503)
  - down            : anything else

--dry-run is required for real network POSTs to be skipped in favor of a
local no-op simulation — this session only ever runs with --dry-run;
omitting it performs a real POST and should only be used deliberately.

Prints ONE JSON document to stdout: {adapter, ts, results:[...]}.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
TIMEOUT_SECONDS = 10
USER_AGENT = "agentic-os-forms-adapter/1.0 (automated-test)"

TEST_PAYLOAD = {
    "name": "Agentic OS Automated Test",
    "email": "automated-test@agentic-os.invalid",
    "message": "This is an automated form-health test submission from agentic-os/adapters/forms.py. "
    "No action needed.",
    "honeypot": "",  # must stay empty — a filled honeypot would look like a bot to the real relay
    "_automated_test": "true",
}


def load_sites() -> list[dict]:
    with SITES_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data.get("sites", [])


def check_form(site: dict, dry_run: bool) -> dict | None:
    domain = site.get("domain", "unknown")
    checks = site.get("checks", {}) or {}
    form = checks.get("form")
    if not form:
        return None  # no form configured for this site — not a result row

    endpoint = form.get("endpoint", "/submit")
    expect_status = form.get("expect_status", 200)
    url = f"https://{domain}{endpoint}"

    result = {
        "domain": domain,
        "status": "down",
        "http_status": None,
        "latency_ms": None,
        "detail": "",
    }

    if dry_run:
        result["status"] = "ok"
        result["detail"] = (
            f"DRY RUN — would POST test payload to {url}; "
            f"configured expect_status={expect_status}. No network call made."
        )
        return result

    import time

    start = time.monotonic()
    try:
        resp = requests.post(
            url,
            json=TEST_PAYLOAD,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        result["http_status"] = resp.status_code

        if resp.status_code == expect_status and expect_status >= 400:
            result["status"] = "known-degraded"
            result["detail"] = f"status {resp.status_code} matches configured known-degraded expect_status"
        elif 200 <= resp.status_code < 300:
            result["status"] = "ok"
            result["detail"] = f"status {resp.status_code}, form accepted the test submission"
        else:
            result["status"] = "down"
            result["detail"] = (
                f"status {resp.status_code} does not match expect_status={expect_status} "
                f"and is not a 2xx success"
            )
    except requests.exceptions.Timeout:
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        result["status"] = "down"
        result["detail"] = f"timeout after {TIMEOUT_SECONDS}s posting to {url}"
    except requests.exceptions.RequestException as e:
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        result["status"] = "down"
        result["detail"] = f"request failed: {e}"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Form-health adapter")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate only — no real network POST. Use this for all testing.",
    )
    args = parser.parse_args()

    try:
        sites = load_sites()
    except Exception as e:
        print(json.dumps({"adapter": "forms", "error": str(e)}), file=sys.stderr)
        return 1

    results = []
    for site in sites:
        r = check_form(site, dry_run=args.dry_run)
        if r is not None:
            results.append(r)

    doc = {
        "adapter": "forms",
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "results": results,
    }
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

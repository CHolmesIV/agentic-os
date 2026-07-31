#!/usr/bin/env python3
"""validate_sites.py — config sanity check for config/sites.yml. Read-only.

Catches the failure mode that hid the maricured.com outage recovery: an
`expect_content` string that does not appear on a site that is otherwise
perfectly healthy. That is a CONFIG ERROR, not an outage — but the uptime
adapter can only see "200 OK, string missing" and reports `degraded`
forever, which is neither ok (no recovery alert) nor down (no page). The
site silently drops out of alerting.

Run this after any edit to sites.yml, and periodically:

    /opt/agentic-os/.venv/bin/python /opt/agentic-os/scripts/validate_sites.py

Exit 0 = all expect_content strings verified against live HTML.
Exit 1 = at least one site is misconfigured (or unreachable to verify).

This script never writes anything and never sends alerts.
"""
import sys
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
TIMEOUT_SECONDS = 25  # generous: we are validating config, not measuring uptime
USER_AGENT = "agentic-os-validate/1.0"


def check(site: dict) -> tuple[str, str]:
    """Return (verdict, detail). verdict in {OK, MISCONFIGURED, UNVERIFIED, SKIP}."""
    domain = site.get("domain", "unknown")
    checks = site.get("checks", {}) or {}
    expect = checks.get("expect_content")

    if not checks.get("uptime", True):
        return "SKIP", "uptime check disabled"
    if not expect:
        return "SKIP", "no expect_content configured"

    check_host = site.get("check_host")
    scheme = site.get("check_host_scheme", "https")
    headers = {"User-Agent": USER_AGENT}
    if check_host:
        url = f"{scheme}://{check_host}/"
        headers["Host"] = domain
    else:
        url = f"https://{domain}/"

    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS, allow_redirects=True)
    except Exception as e:
        return "UNVERIFIED", f"could not fetch ({type(e).__name__}) — rerun when reachable"

    if not (200 <= resp.status_code < 400):
        return "UNVERIFIED", f"HTTP {resp.status_code} — not a healthy fetch, cannot validate"

    if expect in resp.text:
        return "OK", f"'{expect}' found"

    # Healthy page, string absent -> the config is wrong, not the site.
    hint = ""
    lowered = resp.text.lower()
    if expect.lower() in lowered:
        hint = " (present but DIFFERENT CASE — match is case-sensitive)"
    else:
        for n in range(len(expect) - 1, 2, -1):
            if expect[:n] in resp.text:
                hint = f" (closest prefix present on page: '{expect[:n]}')"
                break
    return "MISCONFIGURED", f"HTTP {resp.status_code} but '{expect}' NOT on page{hint}"


def main() -> int:
    data = yaml.safe_load(SITES_PATH.read_text()) or {}
    sites = data.get("sites", [])

    bad = 0
    unverified = 0
    for site in sites:
        verdict, detail = check(site)
        if verdict == "MISCONFIGURED":
            bad += 1
        elif verdict == "UNVERIFIED":
            unverified += 1
        print(f"{verdict:<14} {site.get('domain', 'unknown'):<24} {detail}")

    print()
    if bad:
        print(f"FAIL: {bad} site(s) misconfigured. These will sit in 'degraded' forever "
              f"and never emit a recovery alert. Fix expect_content in config/sites.yml.")
    else:
        print("PASS: every expect_content string was found on its live page.")
    if unverified:
        print(f"NOTE: {unverified} site(s) could not be verified right now (fetch failed).")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

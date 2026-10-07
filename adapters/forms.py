#!/usr/bin/env python3
"""Forms adapter — Tier 0, read-only. Never submits a form.

Asks the local form relay (127.0.0.1:8787) whether each relay-backed site can
send mail: GET /health?site=<domain>. The relay reports whether that site's
SMTP credentials are loaded and, at most every 6h, does a real SMTP login
(no message sent). nginx never forwards /health, and the relay refuses it
from anything that isn't a direct localhost call.

Replaces the old POST-a-test-payload design (2026-10-07): the relay accepts
a well-formed POST and emails it, so a recurring check would have sent real
mail every 15 minutes.

Sites opt in with `checks.form: {via: relay}` in config/sites.yml.

Status:
  - ok   : relay up, creds loaded, last SMTP login succeeded
  - down : relay unreachable, site unknown to the relay, creds missing, or
           SMTP login failing — real leads are being lost
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
RELAY_HEALTH = "http://127.0.0.1:8787/health"
TIMEOUT_SECONDS = 20  # first call per 6h includes an SMTP login


def load_sites() -> list[dict]:
    with SITES_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data.get("sites", [])


def check(domain: str) -> dict:
    result = {"domain": domain, "status": "down", "detail": ""}
    try:
        resp = requests.get(RELAY_HEALTH, params={"site": domain}, timeout=TIMEOUT_SECONDS)
        h = resp.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        result["detail"] = f"form relay unreachable on 127.0.0.1:8787 ({e.__class__.__name__}) — check `systemctl status form-relay`"
        return result
    if not h.get("known"):
        result["detail"] = "site missing from /opt/form-relay/sites.json"
    elif not h.get("configured"):
        result["detail"] = "relay has no SMTP credentials for this site — submissions return 503"
    elif h.get("smtp") != "ok":
        result["detail"] = f"SMTP login failing: {h.get('smtpError') or 'unknown error'} (checked {h.get('checkedAt')})"
    else:
        result["status"] = "ok"
        result["detail"] = f"relay ready, SMTP login ok (checked {h.get('checkedAt')}), spam check: {h.get('jsCheck')}"
    return result


def main() -> int:
    try:
        sites = load_sites()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"adapter": "forms", "error": str(e)}), file=sys.stderr)
        return 1

    results = [check(s["domain"]) for s in sites
               if s.get("domain") and ((s.get("checks") or {}).get("form") or {}).get("via") == "relay"]

    print(json.dumps({
        "adapter": "forms",
        "ts": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

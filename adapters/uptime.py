#!/usr/bin/env python3
"""Uptime adapter — Tier 0, read-only.

Reads config/sites.yml, GETs each site (or its check_host override), and
reports status/latency/content-match/redirect-sanity. Prints ONE JSON
document to stdout. A down/degraded site is a *result*, not an adapter
failure — exit code is only non-zero when the adapter itself couldn't run
(bad config, no network stack, etc).
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
TIMEOUT_SECONDS = 10
USER_AGENT = "agentic-os-uptime/1.0"


def load_sites() -> list[dict]:
    with SITES_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data.get("sites", [])


def _same_site_redirect(original_domain: str, final_url: str) -> bool:
    """Allow www<->apex redirects on the same registrable domain; flag anything else."""
    final_host = urlparse(final_url).hostname or ""
    final_host = final_host.lower()
    original = original_domain.lower()

    def strip_www(h: str) -> str:
        return h[4:] if h.startswith("www.") else h

    return strip_www(final_host) == strip_www(original)


def check_site(site: dict) -> dict:
    domain = site.get("domain", "unknown")
    checks = site.get("checks", {}) or {}
    check_host = site.get("check_host")
    scheme = site.get("check_host_scheme", "https")

    result = {
        "domain": domain,
        "status": "down",
        "http_status": None,
        "latency_ms": None,
        "detail": "",
    }

    if not checks.get("uptime", True):
        result["status"] = "ok"
        result["detail"] = "uptime check disabled for this site"
        return result

    headers = {"User-Agent": USER_AGENT}
    if check_host:
        url = f"{scheme}://{check_host}/"
        headers["Host"] = domain
        target_desc = f"{url} (Host: {domain})"
    else:
        url = f"https://{domain}/"
        target_desc = url

    start = time.monotonic()
    try:
        resp = requests.get(
            url,
            headers=headers,
            timeout=TIMEOUT_SECONDS,
            allow_redirects=True,
        )
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        result["latency_ms"] = latency_ms
        result["http_status"] = resp.status_code

        detail_bits = []

        # Redirect sanity — only meaningful when we followed real DNS (no check_host override,
        # since check_host targets a raw IP and redirect Location headers won't match it).
        if not check_host and resp.history:
            final_url = resp.url
            if not _same_site_redirect(domain, final_url):
                detail_bits.append(
                    f"cross-domain redirect: {domain} -> {urlparse(final_url).hostname}"
                )

        expect_content = checks.get("expect_content")
        content_ok = True
        if expect_content:
            content_ok = expect_content in resp.text
            if not content_ok:
                detail_bits.append(f"expected content '{expect_content}' not found")

        if 200 <= resp.status_code < 400 and content_ok and not any(
            "cross-domain" in b for b in detail_bits
        ):
            result["status"] = "ok"
        elif 400 <= resp.status_code < 600:
            result["status"] = "degraded" if resp.status_code < 500 else "down"
        else:
            result["status"] = "degraded"

        result["detail"] = "; ".join(detail_bits) if detail_bits else f"checked {target_desc}"

    except requests.exceptions.Timeout:
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        result["status"] = "down"
        result["detail"] = f"timeout after {TIMEOUT_SECONDS}s requesting {target_desc}"
    except requests.exceptions.RequestException as e:
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        result["status"] = "down"
        result["detail"] = f"request failed: {e}"

    return result


def main() -> int:
    try:
        sites = load_sites()
    except Exception as e:
        print(json.dumps({"adapter": "uptime", "error": str(e)}), file=sys.stderr)
        return 1

    results = [check_site(site) for site in sites]

    doc = {
        "adapter": "uptime",
        "ts": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""SSL + DNS adapter — Tier 0, read-only.

Per site in config/sites.yml:
  - TLS cert expiry (skipped for HTTP-only check_host entries with no TLS).
  - DNS record snapshot (A/AAAA/CNAME/MX/TXT) via dnspython, falling back to
    `dig` if dnspython errors. Snapshot written to state/dns/<domain>.json,
    diffed against the previous snapshot to catch hijacks/typos.

Prints ONE JSON document to stdout: {adapter, ts, results:[...]}.
Non-zero exit only on adapter failure (e.g. can't read config).
"""
import json
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

try:
    import dns.resolver

    HAVE_DNSPYTHON = True
except ImportError:
    HAVE_DNSPYTHON = False

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
DNS_STATE_DIR = REPO_ROOT / "state" / "dns"

RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "TXT"]
CERT_TIMEOUT = 10
EXPIRY_THRESHOLDS_DAYS = (21, 7, 1)


def load_sites() -> list[dict]:
    with SITES_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    return data.get("sites", [])


def check_cert_expiry(domain: str) -> dict:
    """Return {days_left, expires_at, error}."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=CERT_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter")
        expires_at = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
        days_left = (expires_at - datetime.now(timezone.utc)).days
        return {"days_left": days_left, "expires_at": expires_at.isoformat(), "error": None}
    except Exception as e:
        return {"days_left": None, "expires_at": None, "error": str(e)}


def _dig_lookup(domain: str, rtype: str) -> list[str]:
    try:
        out = subprocess.run(
            ["dig", "+short", rtype, domain],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        return sorted(lines)
    except Exception:
        return []


def lookup_dns(domain: str) -> dict[str, list[str]]:
    records: dict[str, list[str]] = {}
    for rtype in RECORD_TYPES:
        values: list[str] = []
        if HAVE_DNSPYTHON:
            try:
                answer = dns.resolver.resolve(domain, rtype, lifetime=10)
                values = sorted(str(r).rstrip(".") for r in answer)
            except Exception:
                values = _dig_lookup(domain, rtype)
        else:
            values = _dig_lookup(domain, rtype)
        records[rtype] = values
    return records


def snapshot_and_diff(domain: str, current: dict[str, list[str]]) -> list[str]:
    DNS_STATE_DIR.mkdir(parents=True, exist_ok=True)
    snap_path = DNS_STATE_DIR / f"{domain}.json"

    drift: list[str] = []
    previous = None
    if snap_path.exists():
        try:
            previous = json.loads(snap_path.read_text()).get("records")
        except Exception:
            previous = None

    if previous is not None:
        for rtype in RECORD_TYPES:
            old_vals = set(previous.get(rtype, []))
            new_vals = set(current.get(rtype, []))
            if old_vals != new_vals:
                drift.append(f"{rtype}: {sorted(old_vals)} -> {sorted(new_vals)}")

    snap_path.write_text(
        json.dumps(
            {"domain": domain, "ts": datetime.now(timezone.utc).isoformat(), "records": current},
            indent=2,
        )
    )
    return drift


def check_site(site: dict) -> dict:
    domain = site.get("domain", "unknown")
    checks = site.get("checks", {}) or {}
    check_host_scheme = site.get("check_host_scheme")

    result = {
        "domain": domain,
        "status": "ok",
        "http_status": None,
        "latency_ms": None,
        "detail": "",
    }
    detail_bits = []

    # SSL check
    if checks.get("ssl") and check_host_scheme != "http":
        cert = check_cert_expiry(domain)
        if cert["error"]:
            result["status"] = "down"
            detail_bits.append(f"cert check failed: {cert['error']}")
        else:
            days_left = cert["days_left"]
            detail_bits.append(f"cert expires in {days_left}d ({cert['expires_at']})")
            if days_left <= EXPIRY_THRESHOLDS_DAYS[2]:
                result["status"] = "down"
                detail_bits.append("CRITICAL: cert expires within 1 day")
            elif days_left <= EXPIRY_THRESHOLDS_DAYS[1]:
                result["status"] = "degraded"
                detail_bits.append("WARNING: cert expires within 7 days")
            elif days_left <= EXPIRY_THRESHOLDS_DAYS[0]:
                if result["status"] == "ok":
                    result["status"] = "degraded"
                detail_bits.append("NOTICE: cert expires within 21 days")
    elif check_host_scheme == "http":
        detail_bits.append("HTTP-only entry (check_host_scheme=http) — SSL check skipped")

    # DNS snapshot + diff
    dns_records = lookup_dns(domain)
    drift = snapshot_and_diff(domain, dns_records)
    if drift:
        if result["status"] == "ok":
            result["status"] = "degraded"
        detail_bits.append("DNS drift detected: " + " | ".join(drift))
    else:
        detail_bits.append("DNS unchanged since last snapshot")

    result["detail"] = "; ".join(detail_bits)
    return result


def main() -> int:
    try:
        sites = load_sites()
    except Exception as e:
        print(json.dumps({"adapter": "ssl_dns", "error": str(e)}), file=sys.stderr)
        return 1

    results = [check_site(site) for site in sites]

    doc = {
        "adapter": "ssl_dns",
        "ts": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

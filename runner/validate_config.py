#!/usr/bin/env python3
"""Validate config/sites.yml and config/systems.yml.

Usage: python3 runner/validate_config.py
Exit 0 only when both files parse and pass all checks.
"""
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = REPO_ROOT / "config" / "sites.yml"
SYSTEMS_PATH = REPO_ROOT / "config" / "systems.yml"

VALID_HOSTS = {"vps", "shared", "external"}
VALID_SCHEMES = {"http", "https"}


def load_yaml(path: Path, errors: list[str]) -> dict | None:
    if not path.exists():
        errors.append(f"{path}: file does not exist")
        return None
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        errors.append(f"{path}: YAML parse error: {e}")
        return None
    if data is None:
        errors.append(f"{path}: file is empty")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: top-level document must be a mapping")
        return None
    return data


def require(d: dict, key: str, typ, path: str, errors: list[str]):
    if key not in d:
        errors.append(f"{path}: missing required field '{key}'")
        return None
    val = d[key]
    if not isinstance(val, typ):
        errors.append(
            f"{path}.{key}: expected type {typ if isinstance(typ, type) else typ}, got {type(val).__name__}"
        )
        return None
    return val


def validate_sites(data: dict, errors: list[str]) -> None:
    sites = require(data, "sites", list, "sites.yml", errors)
    if sites is None:
        return
    seen_domains: set[str] = set()
    for i, site in enumerate(sites):
        path = f"sites.yml:sites[{i}]"
        if not isinstance(site, dict):
            errors.append(f"{path}: must be a mapping")
            continue

        domain = require(site, "domain", str, path, errors)
        if domain:
            if domain in seen_domains:
                errors.append(f"{path}: duplicate domain '{domain}'")
            seen_domains.add(domain)
            path = f"sites.yml:sites[{i}] ({domain})"

        host = require(site, "host", str, path, errors)
        if host is not None and host not in VALID_HOSTS:
            errors.append(f"{path}.host: '{host}' not in {sorted(VALID_HOSTS)}")

        checks = require(site, "checks", dict, path, errors)
        if isinstance(checks, dict):
            for bool_field in ("uptime", "ssl"):
                if bool_field in checks and not isinstance(checks[bool_field], bool):
                    errors.append(f"{path}.checks.{bool_field}: must be boolean")
            if "expect_content" in checks and not isinstance(checks["expect_content"], str):
                errors.append(f"{path}.checks.expect_content: must be a string")
            if "playwright" in checks and not isinstance(checks["playwright"], bool):
                errors.append(f"{path}.checks.playwright: must be boolean")
            form = checks.get("form")
            if form is not None:
                if not isinstance(form, dict):
                    errors.append(f"{path}.checks.form: must be a mapping")
                else:
                    if "endpoint" not in form or not isinstance(form.get("endpoint"), str):
                        errors.append(f"{path}.checks.form.endpoint: required string")
                    if "expect_status" not in form or not isinstance(
                        form.get("expect_status"), int
                    ):
                        errors.append(f"{path}.checks.form.expect_status: required int")

        if "check_host" in site and not isinstance(site["check_host"], str):
            errors.append(f"{path}.check_host: must be a string")
        if "check_host_scheme" in site:
            scheme = site["check_host_scheme"]
            if scheme not in VALID_SCHEMES:
                errors.append(f"{path}.check_host_scheme: '{scheme}' not in {sorted(VALID_SCHEMES)}")

        deploy = site.get("deploy")
        if deploy is not None and not isinstance(deploy, dict):
            errors.append(f"{path}.deploy: must be a mapping")

        if "dns_provider" in site and not isinstance(site["dns_provider"], str):
            errors.append(f"{path}.dns_provider: must be a string")


def validate_systems(data: dict, errors: list[str]) -> None:
    systems = require(data, "systems", dict, "systems.yml", errors)
    if systems is None:
        return

    vps = systems.get("vps")
    if vps is None:
        errors.append("systems.yml:systems.vps: missing")
    elif not isinstance(vps, dict):
        errors.append("systems.yml:systems.vps: must be a mapping")
    else:
        path = "systems.yml:systems.vps"
        for field in ("ssh_host", "ssh_user", "ssh_key"):
            require(vps, field, str, path, errors)
        writable = require(vps, "writable_paths", list, path, errors)
        if writable is not None:
            for j, p in enumerate(writable):
                if not isinstance(p, str):
                    errors.append(f"{path}.writable_paths[{j}]: must be a string")
        never_touch = vps.get("never_touch")
        if never_touch is not None and not isinstance(never_touch, list):
            errors.append(f"{path}.never_touch: must be a list")
        services = vps.get("managed_services")
        if services is not None:
            if not isinstance(services, list):
                errors.append(f"{path}.managed_services: must be a list")
            else:
                for j, svc in enumerate(services):
                    svc_path = f"{path}.managed_services[{j}]"
                    if not isinstance(svc, dict):
                        errors.append(f"{svc_path}: must be a mapping")
                        continue
                    require(svc, "name", str, svc_path, errors)
                    tier = require(svc, "tier", int, svc_path, errors)
                    if isinstance(tier, int) and not (0 <= tier <= 4):
                        errors.append(f"{svc_path}.tier: {tier} out of range 0-4")
        disk_ceiling = vps.get("disk_ceiling_pct")
        if disk_ceiling is not None and not isinstance(disk_ceiling, (int, float)):
            errors.append(f"{path}.disk_ceiling_pct: must be numeric")

    dns = systems.get("dns")
    if dns is None:
        errors.append("systems.yml:systems.dns: missing")
    elif not isinstance(dns, dict):
        errors.append("systems.yml:systems.dns: must be a mapping")
    else:
        path = "systems.yml:systems.dns"
        require(dns, "provider", str, path, errors)
        snap = dns.get("snapshot_before_write")
        if not isinstance(snap, bool):
            errors.append(f"{path}.snapshot_before_write: must be boolean")
        tier = require(dns, "tier", int, path, errors)
        if isinstance(tier, int) and not (0 <= tier <= 4):
            errors.append(f"{path}.tier: {tier} out of range 0-4")

    telegram = systems.get("telegram")
    if telegram is None:
        errors.append("systems.yml:systems.telegram: missing")
    elif not isinstance(telegram, dict):
        errors.append("systems.yml:systems.telegram: must be a mapping")
    else:
        path = "systems.yml:systems.telegram"
        ids = require(telegram, "allowed_user_ids", list, path, errors)
        if ids is not None:
            for j, uid in enumerate(ids):
                if not isinstance(uid, int):
                    errors.append(f"{path}.allowed_user_ids[{j}]: must be an int")

    budgets = systems.get("budgets")
    if budgets is None:
        errors.append("systems.yml:systems.budgets: missing")
    elif not isinstance(budgets, dict):
        errors.append("systems.yml:systems.budgets: must be a mapping")
    else:
        path = "systems.yml:systems.budgets"
        for field in ("per_routine_usd", "daily_usd"):
            val = require(budgets, field, (int, float), path, errors)
            if isinstance(val, (int, float)) and val <= 0:
                errors.append(f"{path}.{field}: must be positive")


def main() -> int:
    errors: list[str] = []

    sites_data = load_yaml(SITES_PATH, errors)
    if sites_data is not None:
        validate_sites(sites_data, errors)

    systems_data = load_yaml(SYSTEMS_PATH, errors)
    if systems_data is not None:
        validate_systems(systems_data, errors)

    if errors:
        print(f"validate_config: {len(errors)} error(s) found:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("validate_config: OK — sites.yml and systems.yml are clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

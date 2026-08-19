from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from mihomo_api import auto_group_state, group_delay, request

APP_DIR_NAME = "io.github.clash-verge-rev.clash-verge-rev"
MANAGED_MARKER = "MCP_CLASH_GUARDIAN_REGION_PRIORITY_V1"
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = REPO_ROOT / "runtime" / "backups" / "clash-verge-region-priority"


def clash_verge_root(config: dict[str, Any]) -> Path:
    configured = str(config.get("clash_verge_config_dir") or "").strip()
    if configured:
        root = Path(os.path.expandvars(configured)).expanduser()
    else:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA is unavailable; cannot locate Clash Verge Rev configuration")
        root = Path(appdata) / APP_DIR_NAME
    if not root.exists():
        raise RuntimeError(f"Clash Verge Rev config directory not found: {root}")
    return root


def _profiles_metadata(root: Path) -> tuple[Path, dict[str, Any]]:
    path = root / "profiles.yaml"
    if not path.exists():
        raise RuntimeError(f"Clash Verge profiles.yaml not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Clash Verge profiles.yaml did not parse as an object")
    return path, payload


def _active_script_path(root: Path) -> tuple[Path, Path, str]:
    profiles_path, metadata = _profiles_metadata(root)
    current = str(metadata.get("current") or "").strip()
    items = metadata.get("items") or []
    if not current or not isinstance(items, list):
        raise RuntimeError("Clash Verge current profile metadata is missing")

    active = next((item for item in items if isinstance(item, dict) and str(item.get("uid")) == current), None)
    if active is None:
        raise RuntimeError(f"Active Clash Verge profile not found in profiles.yaml: {current}")
    script_uid = str((active.get("option") or {}).get("script") or "").strip()
    if not script_uid:
        raise RuntimeError("Active Clash Verge profile has no subscription extension script attached")

    script_item = next(
        (item for item in items if isinstance(item, dict) and str(item.get("uid")) == script_uid),
        None,
    )
    if script_item is None:
        raise RuntimeError(f"Clash Verge extension script metadata not found: {script_uid}")
    script_file = str(script_item.get("file") or "").strip()
    if not script_file:
        raise RuntimeError(f"Clash Verge extension script file missing for: {script_uid}")

    script_path = root / "profiles" / script_file
    if not script_path.exists():
        raise RuntimeError(f"Clash Verge extension script file not found: {script_path}")
    return profiles_path, script_path, current


def _default_noop_script(text: str) -> bool:
    cleaned = re.sub(r"//.*?$|/\*.*?\*/", "", text, flags=re.MULTILINE | re.DOTALL)
    compact = re.sub(r"\s+", "", cleaned)
    return bool(re.fullmatch(r"functionmain\(config,profileName\)\{returnconfig;\};?", compact))


def _policy_values(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "autoGroup": str(config.get("auto_group_name") or "自动选择"),
        "primaryGroup": str(config.get("region_priority_primary_group") or "新加坡自动"),
        "fallbackGroup": str(config.get("region_priority_fallback_group") or "台湾兜底"),
        "primaryRegex": str(config.get("region_priority_primary_regex") or "新加坡|singapore"),
        "fallbackRegex": str(config.get("region_priority_fallback_regex") or "台湾|台灣|taiwan"),
        "testUrl": str(config.get("region_priority_test_url") or "https://cp.cloudflare.com/generate_204"),
        "urltestInterval": int(config.get("region_priority_urltest_interval_seconds", 300)),
        "urltestTolerance": int(config.get("region_priority_urltest_tolerance_ms", 30)),
        "fallbackInterval": int(config.get("region_priority_fallback_interval_seconds", 60)),
        "timeout": int(config.get("region_priority_health_timeout_ms", 5000)),
        "maxFailedTimes": int(config.get("region_priority_max_failed_times", 2)),
        "expectedStatus": int(config.get("mihomo_healthcheck_expected_status", 204)),
        "invalidPatterns": [str(value) for value in config.get("invalid_node_patterns", []) or []],
    }


def render_managed_script(config: dict[str, Any]) -> str:
    policy_json = json.dumps(_policy_values(config), ensure_ascii=False, separators=(",", ":"))
    return f"""// {MANAGED_MARKER}\n// Managed by mcp-clash-guardian. Machine-specific secrets are never embedded here.\nconst POLICY = {policy_json};\n\nfunction main(config, profileName) {{\n  const groups = Array.isArray(config[\"proxy-groups\"]) ? config[\"proxy-groups\"] : [];\n  const proxyNames = (Array.isArray(config.proxies) ? config.proxies : [])\n    .map((item) => item && item.name)\n    .filter((name) => typeof name === \"string\" && name.length > 0);\n  const invalid = (name) => {{\n    const lower = name.toLowerCase();\n    return POLICY.invalidPatterns.some((value) => lower.includes(String(value).toLowerCase()));\n  }};\n  const primaryRe = new RegExp(POLICY.primaryRegex, \"i\");\n  const fallbackRe = new RegExp(POLICY.fallbackRegex, \"i\");\n  const primary = proxyNames.filter((name) => !invalid(name) && primaryRe.test(name));\n  const fallback = proxyNames.filter((name) => !invalid(name) && fallbackRe.test(name));\n\n  // Fail open: never destroy a working subscription if the naming convention changes.\n  if (primary.length === 0) return config;\n\n  const existingAuto = groups.find((group) => group && group.name === POLICY.autoGroup) || {{}};\n  const primaryGroup = {{\n    name: POLICY.primaryGroup,\n    type: \"url-test\",\n    proxies: primary,\n    url: POLICY.testUrl,\n    interval: POLICY.urltestInterval,\n    tolerance: POLICY.urltestTolerance,\n    lazy: false,\n    timeout: POLICY.timeout,\n    \"max-failed-times\": POLICY.maxFailedTimes,\n    \"expected-status\": POLICY.expectedStatus,\n  }};\n  const fallbackGroup = {{\n    name: POLICY.fallbackGroup,\n    type: \"url-test\",\n    proxies: fallback,\n    url: POLICY.testUrl,\n    interval: POLICY.urltestInterval,\n    tolerance: POLICY.urltestTolerance,\n    lazy: false,\n    timeout: POLICY.timeout,\n    \"max-failed-times\": POLICY.maxFailedTimes,\n    \"expected-status\": POLICY.expectedStatus,\n  }};\n  const autoGroup = {{\n    ...existingAuto,\n    name: POLICY.autoGroup,\n    type: \"fallback\",\n    proxies: fallback.length > 0\n      ? [POLICY.primaryGroup, POLICY.fallbackGroup]\n      : [POLICY.primaryGroup],\n    url: POLICY.testUrl,\n    interval: POLICY.fallbackInterval,\n    lazy: false,\n    timeout: POLICY.timeout,\n    \"max-failed-times\": POLICY.maxFailedTimes,\n    \"expected-status\": POLICY.expectedStatus,\n  }};\n  for (const key of [\"tolerance\", \"use\", \"filter\", \"exclude-filter\", \"include-all\", \"include-all-proxies\", \"include-all-providers\"]) {{\n    delete autoGroup[key];\n  }}\n\n  const managedNames = new Set([POLICY.primaryGroup, POLICY.fallbackGroup, POLICY.autoGroup]);\n  const nextGroups = [];\n  let inserted = false;\n  for (const group of groups) {{\n    if (!group || managedNames.has(group.name)) {{\n      if (group && group.name === POLICY.autoGroup && !inserted) {{\n        nextGroups.push(primaryGroup);\n        if (fallback.length > 0) nextGroups.push(fallbackGroup);\n        nextGroups.push(autoGroup);\n        inserted = true;\n      }}\n      continue;\n    }}\n    nextGroups.push(group);\n  }}\n  if (!inserted) {{\n    nextGroups.unshift(primaryGroup, ...(fallback.length > 0 ? [fallbackGroup] : []), autoGroup);\n  }}\n  config[\"proxy-groups\"] = nextGroups;\n  return config;\n}}\n"""


def _backup_files(root: Path, script_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_ROOT / stamp
    backup.mkdir(parents=True, exist_ok=False)
    candidates = [root / "profiles.yaml", script_path, root / "clash-verge.yaml"]
    for source in candidates:
        if source.exists():
            shutil.copy2(source, backup / source.name)
    return backup


def sync_persistent_script(config: dict[str, Any]) -> dict[str, Any]:
    root = clash_verge_root(config)
    profiles_path, script_path, profile_uid = _active_script_path(root)
    current = script_path.read_text(encoding="utf-8-sig")
    desired = render_managed_script(config)
    if current == desired:
        return {
            "changed": False,
            "root": str(root),
            "profile_uid": profile_uid,
            "script_path": str(script_path),
            "backup": None,
        }
    if MANAGED_MARKER not in current and not _default_noop_script(current):
        raise RuntimeError(
            "Refusing to overwrite an existing custom Clash Verge extension script. "
            f"Review it manually first: {script_path}"
        )

    backup = _backup_files(root, script_path)
    temp = script_path.with_suffix(script_path.suffix + ".mcp-clash-guardian.tmp")
    temp.write_text(desired, encoding="utf-8")
    temp.replace(script_path)
    return {
        "changed": True,
        "root": str(root),
        "profile_uid": profile_uid,
        "profiles_path": str(profiles_path),
        "script_path": str(script_path),
        "backup": str(backup),
    }


def _valid_proxy_names(runtime: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in runtime.get("proxies") or []:
        if isinstance(item, dict) and item.get("name"):
            result.append(str(item["name"]))
    return result


def _matches_region(name: str, regex: re.Pattern[str], invalid_patterns: list[str]) -> bool:
    lowered = name.lower()
    if any(value.lower() in lowered for value in invalid_patterns if value):
        return False
    return bool(regex.search(name))


def transform_runtime_config(config: dict[str, Any], runtime: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    values = _policy_values(config)
    names = _valid_proxy_names(runtime)
    primary_re = re.compile(values["primaryRegex"], re.IGNORECASE)
    fallback_re = re.compile(values["fallbackRegex"], re.IGNORECASE)
    invalid_patterns = list(values["invalidPatterns"])
    primary = [name for name in names if _matches_region(name, primary_re, invalid_patterns)]
    fallback = [name for name in names if _matches_region(name, fallback_re, invalid_patterns)]
    if not primary:
        raise RuntimeError("Region-priority policy found no primary/Singapore proxies; refusing to mutate runtime config")

    groups = runtime.get("proxy-groups") or []
    if not isinstance(groups, list):
        raise RuntimeError("Runtime proxy-groups is not a list")
    existing_auto = next(
        (item for item in groups if isinstance(item, dict) and str(item.get("name")) == values["autoGroup"]),
        {},
    )
    primary_group = {
        "name": values["primaryGroup"],
        "type": "url-test",
        "proxies": primary,
        "url": values["testUrl"],
        "interval": values["urltestInterval"],
        "tolerance": values["urltestTolerance"],
        "lazy": False,
        "timeout": values["timeout"],
        "max-failed-times": values["maxFailedTimes"],
        "expected-status": values["expectedStatus"],
    }
    fallback_group = {
        "name": values["fallbackGroup"],
        "type": "url-test",
        "proxies": fallback,
        "url": values["testUrl"],
        "interval": values["urltestInterval"],
        "tolerance": values["urltestTolerance"],
        "lazy": False,
        "timeout": values["timeout"],
        "max-failed-times": values["maxFailedTimes"],
        "expected-status": values["expectedStatus"],
    }
    auto_group = dict(existing_auto) if isinstance(existing_auto, dict) else {}
    auto_group.update(
        {
            "name": values["autoGroup"],
            "type": "fallback",
            "proxies": [values["primaryGroup"]] + ([values["fallbackGroup"]] if fallback else []),
            "url": values["testUrl"],
            "interval": values["fallbackInterval"],
            "lazy": False,
            "timeout": values["timeout"],
            "max-failed-times": values["maxFailedTimes"],
            "expected-status": values["expectedStatus"],
        }
    )
    for key in (
        "tolerance",
        "use",
        "filter",
        "exclude-filter",
        "include-all",
        "include-all-proxies",
        "include-all-providers",
    ):
        auto_group.pop(key, None)

    managed = {values["primaryGroup"], values["fallbackGroup"], values["autoGroup"]}
    next_groups: list[dict[str, Any]] = []
    inserted = False
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name") or "")
        if group_name in managed:
            if group_name == values["autoGroup"] and not inserted:
                next_groups.append(primary_group)
                if fallback:
                    next_groups.append(fallback_group)
                next_groups.append(auto_group)
                inserted = True
            continue
        next_groups.append(group)
    if not inserted:
        next_groups = [primary_group] + ([fallback_group] if fallback else []) + [auto_group] + next_groups

    runtime["proxy-groups"] = next_groups
    return runtime, {
        "primary_count": len(primary),
        "fallback_count": len(fallback),
        "primary_nodes": primary,
        "fallback_nodes": fallback,
    }


def runtime_policy_active(config: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    pipe = str(config.get("mihomo_pipe") or r"\\.\pipe\verge-mihomo")
    preferred = str(config.get("auto_group_name") or "自动选择")
    try:
        group_name, group, path = auto_group_state(pipe=pipe, preferred=preferred)
    except Exception as exc:
        return False, {"error": str(exc)}
    primary = str(config.get("region_priority_primary_group") or "新加坡自动")
    fallback = str(config.get("region_priority_fallback_group") or "台湾兜底")
    members = [str(value) for value in group.get("all") or []]
    active = bool(group.get("type") == "Fallback" and members and members[0] == primary)
    if primary in members and fallback in members and members.index(fallback) < members.index(primary):
        active = False
    return active, {
        "group_name": group_name,
        "group_type": group.get("type"),
        "members": members,
        "selection_path": path,
    }


def hot_reload_runtime(config: dict[str, Any]) -> dict[str, Any]:
    root = clash_verge_root(config)
    generated = root / "clash-verge.yaml"
    if not generated.exists():
        raise RuntimeError(f"Clash Verge generated runtime config not found: {generated}")
    runtime = yaml.safe_load(generated.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(runtime, dict):
        raise RuntimeError("Clash Verge generated runtime config did not parse as an object")
    transformed, details = transform_runtime_config(config, runtime)
    payload = yaml.safe_dump(transformed, allow_unicode=True, sort_keys=False, width=4096)

    pipe = str(config.get("mihomo_pipe") or r"\\.\pipe\verge-mihomo")
    body = json.dumps({"payload": payload}, ensure_ascii=False).encode("utf-8")
    response = request("/configs?force=true", method="PUT", body=body, pipe=pipe)
    if response.status not in {200, 204}:
        raise RuntimeError(f"Mihomo runtime reload HTTP {response.status}: {response.body[:500]!r}")

    primary_group = str(config.get("region_priority_primary_group") or "新加坡自动")
    auto_group = str(config.get("auto_group_name") or "自动选择")
    test_url = str(config.get("region_priority_test_url") or "https://cp.cloudflare.com/generate_204")
    timeout = int(config.get("region_priority_health_timeout_ms", 5000))
    expected = int(config.get("mihomo_healthcheck_expected_status", 204))
    group_delay(primary_group, test_url, timeout, expected, pipe)
    group_delay(auto_group, test_url, timeout, expected, pipe)

    active, runtime_state = runtime_policy_active(config)
    if not active:
        raise RuntimeError(f"Region-priority runtime verification failed: {runtime_state}")
    return {**details, **runtime_state}


def apply_region_priority(config: dict[str, Any], force_runtime: bool = False) -> dict[str, Any]:
    if not bool(config.get("region_priority_enabled", True)):
        return {"enabled": False, "changed": False, "runtime_reloaded": False}

    persistent = sync_persistent_script(config)
    active_before, before_state = runtime_policy_active(config)
    needs_reload = force_runtime or bool(persistent.get("changed")) or not active_before
    runtime_state = before_state
    if needs_reload:
        runtime_state = hot_reload_runtime(config)
    active_after, verified = runtime_policy_active(config)
    if not active_after:
        raise RuntimeError(f"Region-priority policy is not active after apply: {verified}")
    return {
        "enabled": True,
        "changed": bool(persistent.get("changed")),
        "runtime_reloaded": needs_reload,
        "persistent": persistent,
        "runtime": runtime_state,
        "verified": verified,
    }

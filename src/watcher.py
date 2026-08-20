from __future__ import annotations

import argparse
import json
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pythoncom
import win32com.client

from check_mcp import measure_mcp
from cloudflared_follow import detect_follow_reason, restart_service_with_timeout
from config_loader import LOG_PATH, STATUS_PATH, load_config
from mihomo_api import auto_group_state, connections, delete_connection, group_delay, version

_wmi = None
_last_reselect_monotonic = 0.0


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def wmi():
    global _wmi
    if _wmi is None:
        _wmi = win32com.client.GetObject("winmgmts:")
    return _wmi


def rotate_log(config: dict) -> None:
    if not LOG_PATH.exists():
        return
    max_bytes = int(config.get("log_max_bytes", 1048576))
    keep = max(1, int(config.get("log_keep_files", 2)))
    if LOG_PATH.stat().st_size < max_bytes:
        return
    for index in range(keep - 1, 0, -1):
        src = Path(f"{LOG_PATH}.{index}")
        dst = Path(f"{LOG_PATH}.{index + 1}")
        if src.exists():
            src.replace(dst)
    LOG_PATH.replace(Path(f"{LOG_PATH}.1"))


def log_event(event: str, level: str = "INFO", **data: Any) -> None:
    try:
        config = load_config()
        rotate_log(config)
        record = {"timestamp": now_text(), "level": level, "event": event, "data": data}
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def mihomo_pid(config: dict) -> int | None:
    name = str(config.get("mihomo_process_name", "verge-mihomo.exe")).replace("'", "''")
    rows = wmi().ExecQuery(f"SELECT ProcessId FROM Win32_Process WHERE Name='{name}'")
    for row in rows:
        return int(row.ProcessId)
    return None


def tun_up(config: dict) -> bool:
    adapter = str(config.get("tun_adapter_name", "Meta")).replace("'", "''")
    rows = wmi().ExecQuery(
        f"SELECT NetConnectionStatus FROM Win32_NetworkAdapter WHERE NetConnectionID='{adapter}'"
    )
    for row in rows:
        return int(row.NetConnectionStatus or 0) == 2
    return False


class WindowsServiceController:
    def _run(self, command: list[str], timeout: int = 5) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def state(self, name: str) -> tuple[str, int]:
        safe = name.replace("'", "''")
        rows = wmi().ExecQuery(f"SELECT State,ProcessId FROM Win32_Service WHERE Name='{safe}'")
        for row in rows:
            return str(row.State or "Unknown"), int(row.ProcessId or 0)
        raise RuntimeError(f"Windows service not found: {name}")

    def stop(self, name: str) -> None:
        self._run(["sc.exe", "stop", name])

    def start(self, name: str) -> None:
        self._run(["sc.exe", "start", name])

    def kill(self, pid: int) -> None:
        self._run(["taskkill.exe", "/PID", str(pid), "/T", "/F"])

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def follow_api_state(config: dict) -> dict[str, Any]:
    pipe = str(config.get("mihomo_pipe") or r"\\.\pipe\verge-mihomo")
    preferred = str(config.get("follow_group_name") or "飞鸟云")
    try:
        meta = version(pipe)
        group_name, group, path = auto_group_state(pipe, preferred)
        if group_name != preferred:
            raise RuntimeError(f"Follow group not found: {preferred}")
        selected_leaf = path[-1] if path else group.get("now")
        return {
            "available": True,
            "version": meta.get("version"),
            "group_name": group_name,
            "group_type": group.get("type"),
            "selected_group": group.get("now"),
            "selected_node": selected_leaf,
            "selection_path": path,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def wait_for_stable_follow_node(config: dict, candidate: str) -> tuple[dict[str, Any], str | None, bool]:
    seconds = max(1, int(config.get("follow_node_debounce_seconds", 8)))
    max_rounds = max(1, int(config.get("follow_node_debounce_max_rounds", 2)))
    current_candidate: str | None = candidate
    api: dict[str, Any] = follow_api_state(config)
    for round_no in range(1, max_rounds + 1):
        log_event(
            "cloudflared_follow_node_wait",
            candidate_node=current_candidate,
            round=round_no,
            max_rounds=max_rounds,
            seconds=seconds,
        )
        time.sleep(seconds)
        api = follow_api_state(config)
        observed = api.get("selected_node") if api.get("available") else None
        if observed == current_candidate:
            return api, observed, True
        current_candidate = observed
        if not current_candidate:
            return api, current_candidate, False
    return api, current_candidate, False


def restart_cloudflared_follow(config: dict, reason: str) -> dict[str, object]:
    service_name = str(config.get("cloudflared_service_name") or "Cloudflared")
    log_event("cloudflared_service_restart_start", reason=reason, service=service_name)
    result = restart_service_with_timeout(
        WindowsServiceController(),
        service_name,
        stop_timeout_seconds=float(config.get("cloudflared_stop_timeout_seconds", 10)),
        kill_timeout_seconds=float(config.get("cloudflared_kill_timeout_seconds", 5)),
        start_timeout_seconds=float(config.get("cloudflared_start_timeout_seconds", 15)),
        poll_seconds=float(config.get("cloudflared_service_poll_seconds", 0.5)),
    )
    settle_seconds = max(0, int(config.get("cloudflared_follow_settle_seconds", 5)))
    if settle_seconds:
        time.sleep(settle_seconds)
    log_event("cloudflared_service_restart_end", reason=reason, service=service_name, **result)
    return result


def api_state(config: dict) -> dict[str, Any]:
    pipe = str(config.get("mihomo_pipe") or r"\\.\pipe\verge-mihomo")
    preferred = str(config.get("auto_group_name") or "") or None
    try:
        meta = version(pipe)
        group_name, group, path = auto_group_state(pipe, preferred)
        selected_leaf = path[-1] if path else group.get("now")
        return {
            "available": True,
            "version": meta.get("version"),
            "group_name": group_name,
            "group_type": group.get("type"),
            "selected_group": group.get("now"),
            "selected_node": selected_leaf,
            "selection_path": path,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def invalid_auto_node(config: dict, node: str | None) -> bool:
    if not node:
        return False
    lowered = node.lower()
    patterns = [str(value).lower() for value in config.get("invalid_node_patterns", []) or []]
    return any(pattern and pattern in lowered for pattern in patterns)


def log_mcp_check(reason: str, stage: str, measurement: dict, **extra: Any) -> None:
    log_event(
        "mcp_check",
        reason=reason,
        stage=stage,
        ok=measurement.get("ok"),
        health_class=measurement.get("health_class"),
        hot_median_ms=measurement.get("hot_median_ms"),
        hot_under_threshold=measurement.get("hot_under_threshold"),
        hot_statuses=measurement.get("hot_statuses") or measurement.get("statuses"),
        hot_error_signatures=measurement.get("hot_error_signatures") or measurement.get("error_signatures"),
        cf_ray=measurement.get("cf_ray"),
        **extra,
    )


def confirm_unhealthy(config: dict, reason: str, measurement: dict) -> dict:
    attempts = max(0, int(config.get("failure_confirm_attempts", 2)))
    if attempts == 0 or measurement.get("ok"):
        return measurement
    status_ok = bool(measurement.get("status_ok"))
    interval_key = "latency_failure_confirm_interval_seconds" if status_ok else "http_failure_confirm_interval_seconds"
    interval = max(1, int(config.get(interval_key, 3 if status_ok else 2)))
    failure_type = "latency" if status_ok else "http"
    for attempt in range(1, attempts + 1):
        log_event(
            "mcp_failure_confirm_wait",
            reason=reason,
            failure_type=failure_type,
            attempt=attempt,
            max_attempts=attempts,
            seconds=interval,
        )
        time.sleep(interval)
        measurement = measure_mcp(config)
        log_mcp_check(reason, "failure_confirm", measurement, attempt=attempt, failure_type=failure_type)
        if measurement.get("ok"):
            return measurement
    return measurement


def argotunnel_connections(config: dict) -> list[dict[str, Any]]:
    pipe = str(config.get("mihomo_pipe") or r"\\.\pipe\verge-mihomo")
    port = str(config.get("argotunnel_port", 7844))
    suffix = str(config.get("argotunnel_host_suffix", "argotunnel.com")).lower()
    payload = connections(pipe)
    result: list[dict[str, Any]] = []
    for item in payload.get("connections", []) or []:
        metadata = item.get("metadata") or {}
        host = str(metadata.get("host") or "").lower()
        geo = metadata.get("destinationGeoIP") or []
        if isinstance(geo, str):
            geo = [geo]
        is_cloudflare = any(str(value).lower() == "cloudflare" for value in geo)
        if (
            str(metadata.get("network") or "").lower() == "udp"
            and str(metadata.get("destinationPort") or "") == port
            and (host.endswith(suffix) or is_cloudflare)
        ):
            result.append(item)
    return result


def argotunnel_chain_nodes(config: dict) -> list[str]:
    nodes: list[str] = []
    for item in argotunnel_connections(config):
        chains = item.get("chains") or []
        if chains:
            nodes.append(str(chains[0]))
    return nodes


def argotunnel_chain_mismatch(config: dict, selected_node: str | None) -> tuple[bool, list[str]]:
    nodes = argotunnel_chain_nodes(config)
    if not selected_node or not nodes:
        return False, nodes
    return all(node != selected_node for node in nodes), nodes


def rolling_refresh_argotunnel(config: dict, reason: str) -> dict[str, Any]:
    pipe = str(config.get("mihomo_pipe") or r"\\.\pipe\verge-mihomo")
    before = argotunnel_connections(config)
    before_ids = [str(item.get("id")) for item in before if item.get("id")]
    min_connections = max(2, int(config.get("rolling_refresh_min_connections", 2)))
    poll_seconds = max(1, int(config.get("rolling_refresh_poll_seconds", 1)))
    rebuild_timeout = max(poll_seconds, int(config.get("rolling_refresh_rebuild_timeout_seconds", 8)))
    log_event(
        "argotunnel_rolling_refresh_start",
        reason=reason,
        count=len(before_ids),
        ids=before_ids,
        min_connections=min_connections,
    )

    if len(before_ids) < min_connections:
        log_event(
            "argotunnel_rolling_refresh_skipped",
            "WARN",
            reason=reason,
            count=len(before_ids),
            message="insufficient HA connections; refusing to drop the last tunnel path",
        )
        return {
            "before_count": len(before_ids),
            "after_count": len(before_ids),
            "before_ids": before_ids,
            "after_ids": before_ids,
            "refreshed": 0,
            "skipped": True,
        }

    target_count = len(before_ids)
    refreshed = 0
    stopped_early = False
    for step, connection_id in enumerate(before_ids, start=1):
        live = argotunnel_connections(config)
        live_ids = [str(item.get("id")) for item in live if item.get("id")]
        if connection_id not in live_ids:
            log_event(
                "argotunnel_rolling_step_skipped",
                reason=reason,
                step=step,
                connection_id=connection_id,
                message="connection already replaced by cloudflared",
            )
            continue
        if len(live_ids) < min_connections:
            stopped_early = True
            log_event(
                "argotunnel_rolling_refresh_guard",
                "WARN",
                reason=reason,
                step=step,
                live_count=len(live_ids),
                message="HA count below rolling-refresh safety floor",
            )
            break

        ids_before_step = set(live_ids)
        delete_connection(connection_id, pipe=pipe)
        deadline = time.monotonic() + rebuild_timeout
        rebuilt = False
        after_step: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            time.sleep(poll_seconds)
            after_step = argotunnel_connections(config)
            after_step_ids = [str(item.get("id")) for item in after_step if item.get("id")]
            replacement_seen = any(value not in ids_before_step for value in after_step_ids)
            if len(after_step_ids) >= target_count and replacement_seen:
                rebuilt = True
                break

        after_step_ids = [str(item.get("id")) for item in after_step if item.get("id")]
        log_event(
            "argotunnel_rolling_step",
            reason=reason,
            step=step,
            connection_id=connection_id,
            rebuilt=rebuilt,
            after_count=len(after_step_ids),
            after_ids=after_step_ids,
        )
        if not rebuilt:
            stopped_early = True
            log_event(
                "argotunnel_rolling_refresh_guard",
                "WARN",
                reason=reason,
                step=step,
                live_count=len(after_step_ids),
                message="replacement did not rebuild before timeout; stop instead of dropping more HA paths",
            )
            break
        refreshed += 1

    after = argotunnel_connections(config)
    after_ids = [str(item.get("id")) for item in after if item.get("id")]
    log_event(
        "argotunnel_rolling_refresh_end",
        reason=reason,
        before_count=len(before_ids),
        after_count=len(after_ids),
        before_ids=before_ids,
        after_ids=after_ids,
        refreshed=refreshed,
        stopped_early=stopped_early,
        chains=[item.get("chains") for item in after],
    )
    return {
        "before_count": len(before_ids),
        "after_count": len(after_ids),
        "before_ids": before_ids,
        "after_ids": after_ids,
        "refreshed": refreshed,
        "stopped_early": stopped_early,
    }


def write_status(
    config: dict,
    phase: str,
    trigger: str,
    measurement: dict | None,
    api: dict | None,
    last_error: str = "",
) -> None:
    try:
        argotunnel_count = len(argotunnel_connections(config)) if (api or {}).get("available") else None
    except Exception:
        argotunnel_count = None
    status = {
        "timestamp": now_text(),
        "strategy_version": config.get("strategy_version", "unknown"),
        "machine_name": config.get("machine_name"),
        "phase": phase,
        "trigger": trigger,
        "enabled": bool(config.get("enabled", True)),
        "public_host": config.get("public_host"),
        "tun_adapter": config.get("tun_adapter_name"),
        "tun_up": tun_up(config),
        "mihomo_pid": mihomo_pid(config),
        "mihomo_api": api or {},
        "selected_node": (api or {}).get("selected_node"),
        "argotunnel_connection_count": argotunnel_count,
        "mcp_ok": (measurement or {}).get("ok"),
        "mcp_health_class": (measurement or {}).get("health_class"),
        "hot_median_ms": (measurement or {}).get("hot_median_ms"),
        "hot_under_threshold": (measurement or {}).get("hot_under_threshold"),
        "http_status_ok": (measurement or {}).get("status_ok"),
        "cf_ray": (measurement or {}).get("cf_ray"),
        "last_error": last_error,
    }
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def trigger_mihomo_reselect(config: dict, api: dict) -> tuple[dict[str, Any], bool]:
    global _last_reselect_monotonic
    if not api.get("available") or not api.get("group_name"):
        raise RuntimeError("Mihomo API/automatic policy group unavailable")
    cooldown = max(0, int(config.get("mihomo_reselect_cooldown_seconds", 180)))
    elapsed = time.monotonic() - _last_reselect_monotonic if _last_reselect_monotonic else None
    if elapsed is not None and elapsed < cooldown:
        log_event(
            "mihomo_group_healthcheck_skipped",
            reason="cooldown",
            cooldown_seconds=cooldown,
            remaining_seconds=round(cooldown - elapsed, 1),
            selected_node=api.get("selected_node"),
        )
        return api, False

    pipe = str(config.get("mihomo_pipe") or r"\\.\pipe\verge-mihomo")
    path = [str(value) for value in (api.get("selection_path") or []) if value]
    group_name = str(api["group_name"])
    groups_to_check = list(reversed(path[:-1])) if len(path) >= 2 else [group_name]
    if group_name not in groups_to_check:
        groups_to_check.append(group_name)

    tested: dict[str, int] = {}
    log_event(
        "mihomo_group_healthcheck_start",
        group_name=group_name,
        selection_path=path,
        groups=groups_to_check,
        selected_node=api.get("selected_node"),
    )
    for target_group in groups_to_check:
        result = group_delay(
            group_name=target_group,
            test_url=str(config.get("mihomo_healthcheck_url", "https://cp.cloudflare.com/generate_204")),
            timeout_ms=int(config.get("mihomo_healthcheck_timeout_ms", 5000)),
            expected_status=int(config.get("mihomo_healthcheck_expected_status", 204)),
            pipe=pipe,
        )
        tested[target_group] = len(result)

    _last_reselect_monotonic = time.monotonic()
    time.sleep(int(config.get("mihomo_reselect_settle_seconds", 5)))
    after = api_state(config)
    log_event(
        "mihomo_group_healthcheck_end",
        group_name=group_name,
        before_node=api.get("selected_node"),
        after_node=after.get("selected_node"),
        before_path=path,
        after_path=after.get("selection_path"),
        tested=tested,
    )
    return after, True


def recover(config: dict, reason: str) -> tuple[dict, dict, str]:
    api = api_state(config)
    selected_node = api.get("selected_node")
    selected_invalid = invalid_auto_node(config, selected_node)
    write_status(config, "recovering", reason, None, api)
    log_event(
        "recovery_start",
        reason=reason,
        selected_node=selected_node,
        selected_node_invalid=selected_invalid,
    )
    try:
        measurement = measure_mcp(config)
        log_mcp_check(reason, "initial", measurement)
        if measurement.get("ok"):
            api = api_state(config)
            write_status(config, "monitoring", reason, measurement, api)
            log_event("recovery_end", reason=reason, action="healthy_no_change", selected_node=api.get("selected_node"))
            return measurement, api, "healthy_no_change"

        measurement = confirm_unhealthy(config, reason, measurement)
        if measurement.get("ok"):
            api = api_state(config)
            write_status(config, "monitoring", reason, measurement, api)
            log_event("recovery_end", reason=reason, action="transient_recovered", selected_node=api.get("selected_node"))
            return measurement, api, "transient_recovered"

        if measurement.get("health_class") == "latency_degraded":
            observe_seconds = max(1, int(config.get("latency_observe_retry_seconds", 10)))
            observe_attempts = max(1, int(config.get("latency_observe_retry_attempts", 3)))
            for attempt in range(1, observe_attempts + 1):
                log_event(
                    "latency_observe_wait",
                    reason=reason,
                    attempt=attempt,
                    max_attempts=observe_attempts,
                    seconds=observe_seconds,
                    hot_median_ms=measurement.get("hot_median_ms"),
                )
                time.sleep(observe_seconds)
                measurement = measure_mcp(config)
                log_mcp_check(reason, "latency_observe", measurement, attempt=attempt)
                if measurement.get("ok"):
                    api = api_state(config)
                    write_status(config, "monitoring", reason, measurement, api)
                    log_event("recovery_end", reason=reason, action="latency_observe_recovered")
                    return measurement, api, "latency_observe_recovered"
                if measurement.get("health_class") != "latency_degraded":
                    break
            if measurement.get("health_class") == "latency_degraded":
                api = api_state(config)
                write_status(config, "observing", reason, measurement, api)
                log_event(
                    "recovery_end",
                    reason=reason,
                    action="latency_degraded_observe_only",
                    hot_median_ms=measurement.get("hot_median_ms"),
                )
                return measurement, api, "latency_degraded_observe_only"

        api = api_state(config)
        selected_node = api.get("selected_node")
        selected_invalid = invalid_auto_node(config, selected_node)
        if selected_invalid:
            log_event(
                "invalid_auto_node_observed",
                "WARN",
                reason=reason,
                selected_node=selected_node,
                action="do_not_migrate_argotunnel_to_invalid_node",
            )
            if api.get("available"):
                before_node = selected_node
                api, reselected = trigger_mihomo_reselect(config, api)
                after_node = api.get("selected_node")
                if reselected:
                    log_event(
                        "invalid_auto_node_reselect_result",
                        reason=reason,
                        before_node=before_node,
                        after_node=after_node,
                        after_node_invalid=invalid_auto_node(config, after_node),
                    )
                    measurement = measure_mcp(config)
                    log_mcp_check(reason, "after_invalid_node_reselect", measurement)
                    if measurement.get("ok"):
                        write_status(config, "monitoring", reason, measurement, api)
                        return measurement, api, "invalid_node_reselect_recovered"
                    if after_node != before_node and not invalid_auto_node(config, after_node):
                        rolling_refresh_argotunnel(config, f"{reason}:valid_node_after_reselect")
                        measurement = measure_mcp(config)
                        log_mcp_check(reason, "after_valid_node_rolling_refresh", measurement)
                        if measurement.get("ok"):
                            write_status(config, "monitoring", reason, measurement, api)
                            return measurement, api, "valid_node_rolling_refresh"
        else:
            mismatch, chain_nodes = argotunnel_chain_mismatch(config, selected_node)
            failure_class = str(measurement.get("health_class") or "unknown")
            should_refresh_first = mismatch or failure_class in {"http_failure", "transport_failure"}
            if should_refresh_first:
                log_event(
                    "argotunnel_recovery_decision",
                    reason=reason,
                    action="rolling_refresh",
                    failure_class=failure_class,
                    selected_node=selected_node,
                    chain_nodes=chain_nodes,
                    chain_mismatch=mismatch,
                )
                rolling_refresh_argotunnel(config, f"{reason}:confirmed_unhealthy")
                measurement = measure_mcp(config)
                log_mcp_check(reason, "after_rolling_refresh", measurement)
                if measurement.get("ok"):
                    api = api_state(config)
                    write_status(config, "monitoring", reason, measurement, api)
                    return measurement, api, "rolling_refresh"
            else:
                log_event(
                    "argotunnel_recovery_decision",
                    reason=reason,
                    action="skip_same_path_refresh",
                    failure_class=failure_class,
                    selected_node=selected_node,
                    chain_nodes=chain_nodes,
                    chain_mismatch=mismatch,
                )

            api = api_state(config)
            if api.get("available"):
                before_node = api.get("selected_node")
                api, reselected = trigger_mihomo_reselect(config, api)
                after_node = api.get("selected_node")
                if reselected and after_node != before_node and not invalid_auto_node(config, after_node):
                    rolling_refresh_argotunnel(config, f"{reason}:node_changed_after_reselect")
                elif reselected and invalid_auto_node(config, after_node):
                    log_event(
                        "invalid_auto_node_observed",
                        "WARN",
                        reason=reason,
                        selected_node=after_node,
                        action="skip_argotunnel_refresh_after_reselect",
                    )
                measurement = measure_mcp(config)
                log_mcp_check(
                    reason,
                    "after_mihomo_reselect" if reselected else "after_mihomo_reselect_cooldown",
                    measurement,
                    node_changed=after_node != before_node,
                )
                if measurement.get("ok"):
                    write_status(config, "monitoring", reason, measurement, api)
                    return measurement, api, "mihomo_reselect" if reselected else "reselect_cooldown_recovered"

        settle_seconds = max(1, int(config.get("final_settle_retry_seconds", 10)))
        settle_attempts = max(0, int(config.get("final_settle_retry_attempts", 6)))
        for attempt in range(1, settle_attempts + 1):
            log_event(
                "final_settle_retry_wait",
                reason=reason,
                attempt=attempt,
                max_attempts=settle_attempts,
                seconds=settle_seconds,
            )
            time.sleep(settle_seconds)
            measurement = measure_mcp(config)
            log_mcp_check(reason, "final_settle_retry", measurement, attempt=attempt)
            if measurement.get("ok"):
                api = api_state(config)
                write_status(config, "monitoring", reason, measurement, api)
                return measurement, api, "final_settle_retry"

        error = "MCP remained unhealthy after confirmation, safe rolling refresh, Mihomo cooldown/reselect, and settle retries"
        write_status(config, "degraded", reason, measurement, api, error)
        log_event("recovery_degraded", "WARN", reason=reason, selected_node=api.get("selected_node"), measurement=measurement)
        return measurement, api, "degraded"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        write_status(config, "error", reason, None, api, error)
        log_event("recovery_exception", "ERROR", reason=reason, error=error, trace=traceback.format_exc(limit=6))
        return {"ok": False, "reason": error}, api, "error"


def wait_for_stable_auto_node(config: dict, old_node: str | None, candidate: str) -> tuple[dict, str | None, bool]:
    seconds = max(1, int(config.get("auto_node_debounce_seconds", 10)))
    max_rounds = max(1, int(config.get("auto_node_debounce_max_rounds", 3)))
    current_candidate: str | None = candidate
    api: dict[str, Any] = api_state(config)
    for round_no in range(1, max_rounds + 1):
        log_event(
            "auto_node_debounce_wait",
            old_node=old_node,
            candidate_node=current_candidate,
            round=round_no,
            max_rounds=max_rounds,
            seconds=seconds,
        )
        time.sleep(seconds)
        api = api_state(config)
        observed = api.get("selected_node") if api.get("available") else None
        if observed == current_candidate:
            log_event(
                "auto_node_debounce_stable",
                old_node=old_node,
                selected_node=observed,
                invalid_node=invalid_auto_node(config, observed),
                round=round_no,
            )
            return api, observed, True
        log_event(
            "auto_node_debounce_superseded",
            old_node=old_node,
            previous_candidate=current_candidate,
            new_candidate=observed,
            round=round_no,
        )
        current_candidate = observed
        if not current_candidate:
            return api, current_candidate, False
    return api, current_candidate, False


def run_cloudflared_follow_watch() -> int:
    pythoncom.CoInitialize()
    config = load_config()
    log_event(
        "watcher_start",
        strategy_version=config.get("strategy_version"),
        machine=config.get("machine_name"),
        watcher_mode="cloudflared-follow",
    )

    last_tun = tun_up(config)
    last_pid = mihomo_pid(config)
    api = follow_api_state(config) if last_pid else {"available": False, "error": "mihomo process missing"}
    last_node = api.get("selected_node") if api.get("available") else None
    write_status(config, "monitoring" if last_tun and last_pid else "waiting", "watcher_start", None, api)

    while True:
        try:
            config = load_config()
            poll_seconds = max(1, int(config.get("poll_seconds", 2)))
            if not bool(config.get("enabled", True)):
                api = follow_api_state(config) if mihomo_pid(config) else {"available": False, "error": "mihomo process missing"}
                write_status(config, "disabled", "config", None, api)
                time.sleep(poll_seconds)
                continue

            current_tun = tun_up(config)
            current_pid = mihomo_pid(config)
            api = follow_api_state(config) if current_pid else {"available": False, "error": "mihomo process missing"}
            current_node = api.get("selected_node") if api.get("available") else None

            reason = detect_follow_reason(
                last_tun=last_tun,
                current_tun=current_tun,
                last_pid=last_pid,
                current_pid=current_pid,
                last_node=last_node,
                current_node=current_node,
            )

            if reason == "manual_node_changed" and current_node:
                api, current_node, stable = wait_for_stable_follow_node(config, str(current_node))
                current_tun = tun_up(config)
                current_pid = mihomo_pid(config)
                if not stable or current_node == last_node or not current_tun or not current_pid:
                    log_event(
                        "cloudflared_follow_event_ignored",
                        reason=reason,
                        old_node=last_node,
                        observed_node=current_node,
                        stable=stable,
                        tun_up=current_tun,
                    )
                    reason = None
            elif reason in {"tun_up", "mihomo_pid_changed"}:
                time.sleep(max(0, int(config.get("stabilize_seconds", 3))))
                current_tun = tun_up(config)
                current_pid = mihomo_pid(config)
                api = follow_api_state(config) if current_pid else {"available": False, "error": "mihomo process missing"}
                current_node = api.get("selected_node") if api.get("available") else current_node
                if not current_tun or not current_pid:
                    reason = None

            if reason:
                log_event(
                    "cloudflared_follow_event",
                    reason=reason,
                    old_node=last_node,
                    new_node=current_node,
                    old_pid=last_pid,
                    new_pid=current_pid,
                )
                restart_cloudflared_follow(config, reason)
                api = follow_api_state(config) if current_pid else api
                write_status(config, "monitoring", reason, None, api)
            else:
                write_status(config, "monitoring" if current_tun and current_pid else "waiting", "idle", None, api)

            last_tun = current_tun
            if current_pid:
                last_pid = current_pid
            if current_node:
                last_node = current_node
            time.sleep(poll_seconds)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log_event("cloudflared_follow_exception", "ERROR", error=error, trace=traceback.format_exc(limit=6))
            try:
                write_status(config, "error", "cloudflared_follow", None, api if isinstance(api, dict) else {}, error)
            except Exception:
                pass
            time.sleep(5)


def run_once() -> int:
    config = load_config()
    if str(config.get("watcher_mode") or "guardian") == "cloudflared-follow":
        api = follow_api_state(config)
        result = restart_cloudflared_follow(config, "manual_run")
        write_status(config, "monitoring", "manual_run", None, api)
        print(json.dumps({"action": "cloudflared_service_restart", "result": result}, ensure_ascii=True, indent=2))
        return 0
    measurement, _, action = recover(config, "manual_run")
    print(json.dumps({"action": action, "measurement": measurement}, ensure_ascii=True, indent=2))
    return 0 if measurement.get("ok") else 2


def run_watch() -> int:
    config = load_config()
    if str(config.get("watcher_mode") or "guardian") == "cloudflared-follow":
        return run_cloudflared_follow_watch()
    pythoncom.CoInitialize()
    log_event("watcher_start", strategy_version=config.get("strategy_version"), machine=config.get("machine_name"))

    last_tun = tun_up(config)
    last_pid = mihomo_pid(config)
    api = api_state(config)
    last_node = api.get("selected_node") if api.get("available") else None
    last_health = time.monotonic()
    last_measurement: dict | None = None

    if last_tun and last_pid:
        last_measurement, api, _ = recover(config, "watcher_start")
        last_node = api.get("selected_node") if api.get("available") else last_node
        last_health = time.monotonic()
    else:
        write_status(config, "waiting", "watcher_start", None, api)

    while True:
        try:
            config = load_config()
            poll_seconds = max(1, int(config.get("poll_seconds", 2)))
            if not bool(config.get("enabled", True)):
                api = api_state(config)
                write_status(config, "disabled", "config", last_measurement, api)
                time.sleep(poll_seconds)
                continue

            preserve_last_node = False
            current_tun = tun_up(config)
            current_pid = mihomo_pid(config)
            api = api_state(config) if current_pid else {"available": False, "error": "mihomo process missing"}
            current_node = api.get("selected_node") if api.get("available") else None

            reason: str | None = None
            if current_tun and not last_tun:
                reason = "tun_up"
            elif current_pid and last_pid and current_pid != last_pid:
                reason = "mihomo_pid_changed"
            elif current_node and last_node and current_node != last_node:
                reason = "auto_node_changed"
            else:
                health_interval = int(config.get("health_check_seconds", 600))
                if (last_measurement or {}).get("health_class") == "latency_degraded":
                    health_interval = min(health_interval, int(config.get("degraded_health_check_seconds", 60)))
                if current_tun and current_pid and (time.monotonic() - last_health) >= health_interval:
                    reason = "periodic_health_check"

            if reason:
                if reason == "auto_node_changed":
                    api, debounced_node, stable = wait_for_stable_auto_node(config, last_node, str(current_node))
                    current_node = debounced_node
                    if not stable or current_node == last_node:
                        log_event(
                            "auto_node_change_ignored",
                            old_node=last_node,
                            observed_node=current_node,
                            stable=stable,
                            reason="unstable_or_returned_to_previous_node",
                        )
                        reason = None
                        current_node = last_node
                elif reason in {"tun_up", "mihomo_pid_changed"}:
                    time.sleep(int(config.get("stabilize_seconds", 3)))

            if reason:
                node_before_recovery = current_node
                log_event(
                    "watch_event",
                    reason=reason,
                    old_node=last_node,
                    new_node=current_node,
                    old_pid=last_pid,
                    new_pid=current_pid,
                    invalid_node=invalid_auto_node(config, current_node),
                )
                last_measurement, api, _ = recover(config, reason)
                last_health = time.monotonic()
                current_tun = tun_up(config)
                current_pid = mihomo_pid(config)
                api = api_state(config) if current_pid else api
                current_node = api.get("selected_node") if api.get("available") else current_node
                if current_node and node_before_recovery and current_node != node_before_recovery:
                    preserve_last_node = True
                    log_event(
                        "node_changed_during_recovery",
                        reason=reason,
                        before_node=node_before_recovery,
                        after_node=current_node,
                        action="defer_to_next_loop",
                    )
            else:
                phase = "monitoring" if current_tun and current_pid else "waiting"
                write_status(config, phase, "idle", last_measurement, api)

            last_tun = current_tun
            if current_pid:
                last_pid = current_pid
            if current_node and not preserve_last_node:
                last_node = current_node
            time.sleep(poll_seconds)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log_event("watcher_loop_exception", "ERROR", error=error, trace=traceback.format_exc(limit=6))
            try:
                write_status(config, "error", "watcher_loop", last_measurement, api if isinstance(api, dict) else {}, error)
            except Exception:
                pass
            time.sleep(5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        return run_once()
    return run_watch()


if __name__ == "__main__":
    raise SystemExit(main())

"""Layered, read-only diagnosis of the whole MCP path.

The path is five layers, and each one touches its neighbour only through a
localhost port or a public hostname. That is what makes them independently
verifiable, and it is why this file asks exactly one question per layer:

    L1 exit      Mihomo is up, TUN is up, traffic leaves through the node we think
    L2 tunnel    Cloudflared holds HA connections to the Cloudflare edge
    L3 origin    the MCP server answers on localhost
    L4 edge      the public hostname answers end to end
    L5 guardian  the watcher is installed, running, and reporting the truth

A failure low in the list explains every failure above it, so the first FAIL is
normally the only thing worth fixing. Nothing here mutates state, so it is safe
to run at any time, including on a half-built machine.
"""
from __future__ import annotations

import http.client
import json
import shlex
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from cloudflared_follow import argotunnel_leaf_nodes, select_argotunnel_connections
from config_loader import LOCAL_CONFIG_PATH, STATUS_PATH
from console import print_line

OK = "ok"
WARN = "warn"
FAIL = "fail"

RANK = {OK: 0, WARN: 1, FAIL: 2}
MARK = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}

STATUS_NETWORKS = ("udp", "tcp")


def layer(
    layer_id: str,
    title: str,
    state: str,
    detail: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    fix: str = "",
) -> dict[str, Any]:
    return {
        "id": layer_id,
        "title": title,
        "state": state,
        "detail": detail or {},
        "notes": notes or [],
        "fix": fix,
    }


def _wmi():
    import win32com.client

    return win32com.client.GetObject("winmgmts:")


def _invalid_node(config: dict, node: str | None) -> bool:
    if not node:
        return False
    lowered = node.lower()
    patterns = [str(value).lower() for value in config.get("invalid_node_patterns", []) or []]
    return any(pattern and pattern in lowered for pattern in patterns)


def _https_trace(host: str, timeout: float = 10.0, samples: int = 4) -> dict[str, Any]:
    """Ask the Cloudflare edge who it thinks we are.

    `/cdn-cgi/trace` is answered by the POP itself, so it measures client-to-edge
    only and never involves the tunnel. The gap between this and the L4 number is
    the tunnel return leg.
    """
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    times: list[float] = []
    body = b""
    try:
        for _ in range(max(1, samples)):
            started = time.perf_counter()
            conn.request("GET", "/cdn-cgi/trace", headers={"Connection": "keep-alive"})
            response = conn.getresponse()
            body = response.read()
            times.append(round((time.perf_counter() - started) * 1000.0, 1))
    finally:
        conn.close()
    fields: dict[str, Any] = {}
    for line in body.decode("utf-8", "ignore").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    hot = times[1:] or times
    return {
        "colo": fields.get("colo"),
        "loc": fields.get("loc"),
        "egress_ip": fields.get("ip"),
        "cold_ms": times[0] if times else None,
        "hot_median_ms": round(statistics.median(hot), 1) if hot else None,
    }


def check_exit(config: dict) -> dict[str, Any]:
    title = "exit      Mihomo / TUN / egress node"
    try:
        from mihomo_api import GROUP_TYPES, connections, proxies, selection_path, version
    except Exception as exc:
        return layer(
            "L1",
            title,
            FAIL,
            {"import_error": repr(exc)},
            ["pywin32 is not importable, so nothing can read Mihomo"],
            "python control.py install   (or: pip install -r requirements.txt)",
        )

    detail: dict[str, Any] = {}
    notes: list[str] = []
    pipe = str(config.get("mihomo_pipe") or r"\\.\pipe\verge-mihomo")
    detail["pipe"] = pipe

    try:
        detail["mihomo_version"] = version(pipe).get("version")
    except Exception as exc:
        return layer(
            "L1",
            title,
            FAIL,
            {"pipe": pipe, "error": repr(exc)},
            ["Mihomo control pipe is unreachable; Clash Verge is not running"],
            "start Clash Verge (scheduled task 'Clash Verge (Admin)')",
        )

    adapter = str(config.get("tun_adapter_name", "Meta")).replace("'", "''")
    tun = False
    try:
        rows = _wmi().ExecQuery(
            f"SELECT NetConnectionStatus FROM Win32_NetworkAdapter WHERE NetConnectionID='{adapter}'"
        )
        for row in rows:
            tun = int(row.NetConnectionStatus or 0) == 2
            break
    except Exception as exc:
        notes.append(f"TUN adapter query failed: {exc!r}")
    detail["tun_adapter"] = config.get("tun_adapter_name", "Meta")
    detail["tun_up"] = tun

    state = OK
    fix = ""
    if not tun:
        state = FAIL
        notes.append("TUN adapter is down, so Cloudflared will not egress through the proxy")
        fix = "enable TUN mode in Clash Verge"

    try:
        payload = proxies(pipe)
        items = payload.get("proxies") or {}
        groups = {n: i for n, i in items.items() if isinstance(i, dict) and i.get("type") in GROUP_TYPES}
        detail["group_count"] = len(groups)
        follow_group = str(config.get("follow_group_name") or "")
        detail["follow_group"] = follow_group
        detail["follow_group_present"] = follow_group in groups
        if follow_group and follow_group in groups:
            path = selection_path(follow_group, pipe=pipe, payload=payload)
            detail["selection_path"] = path
            detail["group_node"] = path[-1] if path else None
        elif follow_group:
            notes.append(
                f"configured follow group {follow_group!r} is not in this profile "
                f"({len(groups)} groups); the watcher falls back to the tunnel chains"
            )
            state = max(state, WARN, key=lambda value: RANK[value])
        garbage = sorted(n for n, i in groups.items() if _invalid_node(config, str(i.get("now") or "")))
        if garbage:
            detail["groups_on_pseudo_node"] = garbage
            notes.append(
                "these groups currently select a subscription placeholder, not a real node: "
                + ", ".join(garbage)
            )
            state = max(state, WARN, key=lambda value: RANK[value])
    except Exception as exc:
        notes.append(f"reading proxy groups failed: {exc!r}")
        state = max(state, WARN, key=lambda value: RANK[value])

    try:
        tunnel_items = select_argotunnel_connections(
            connections(pipe),
            port=config.get("argotunnel_port", 7844),
            host_suffix=config.get("argotunnel_host_suffix", "argotunnel.com"),
            networks=STATUS_NETWORKS,
        )
        chain_nodes = argotunnel_leaf_nodes(tunnel_items)
        detail["tunnel_chain_nodes"] = chain_nodes
        distinct = sorted(set(chain_nodes))
        if len(distinct) > 1:
            notes.append("the tunnel is straddling more than one exit node: " + ", ".join(distinct))
            state = max(state, WARN, key=lambda value: RANK[value])
        elif distinct and _invalid_node(config, distinct[0]):
            notes.append(f"the tunnel is pinned to a placeholder entry {distinct[0]!r}")
            state = FAIL
            fix = fix or "select a real node in Clash Verge, then: python control.py run"
        group_node = detail.get("group_node")
        if group_node and distinct and group_node not in distinct:
            notes.append(
                f"policy group selects {group_node!r} but the tunnel still egresses through "
                + ", ".join(distinct)
            )
            state = max(state, WARN, key=lambda value: RANK[value])
    except Exception as exc:
        notes.append(f"reading Mihomo connections failed: {exc!r}")
        state = max(state, WARN, key=lambda value: RANK[value])

    host = str(config.get("public_host") or "")
    if host:
        try:
            trace = _https_trace(host)
            detail["edge"] = trace
        except Exception as exc:
            notes.append(f"edge trace to {host} failed: {exc!r}")
            state = FAIL
            fix = fix or "check that the proxy has internet access"
    else:
        notes.append("public_host is not configured, so the egress identity was not measured")
        state = max(state, WARN, key=lambda value: RANK[value])

    return layer("L1", title, state, detail, notes, fix)


def _service_info(name: str) -> dict[str, Any]:
    safe = name.replace("'", "''")
    rows = _wmi().ExecQuery(
        f"SELECT State,ProcessId,PathName,StartMode FROM Win32_Service WHERE Name='{safe}'"
    )
    for row in rows:
        raw = str(row.PathName or "")
        argv = [token.strip('"') for token in shlex.split(raw, posix=False)]
        info: dict[str, Any] = {
            "state": str(row.State or ""),
            "start_mode": str(row.StartMode or ""),
            "pid": int(row.ProcessId or 0),
            "exe": argv[0] if argv else "",
        }

        def flag_value(flag: str) -> str | None:
            for index, token in enumerate(argv):
                if token == flag and index + 1 < len(argv):
                    return argv[index + 1]
                if token.startswith(flag + "="):
                    return token.split("=", 1)[1]
            return None

        info["metrics_flag"] = flag_value("--metrics")
        token_file = flag_value("--token-file")
        if token_file:
            # A path is safe to print and is exactly what a rebuild has to carry.
            info["credential"] = "token-file"
            info["token_file"] = token_file
        elif any(token == "--token" or token.startswith("--token=") for token in argv):
            info["credential"] = "inline-token"
        elif flag_value("--config"):
            info["credential"] = "config-file"
            info["config_file"] = flag_value("--config")
        else:
            info["credential"] = "unknown"
        return info
    raise RuntimeError(f"Windows service not found: {name}")


def _listening_endpoint(pid: int) -> str | None:
    try:
        text = subprocess.run(
            ["netstat.exe", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[4] == str(pid):
            if parts[1].startswith("127.0.0.1:"):
                return parts[1]
    return None


def _http_json(endpoint: str, path: str, timeout: float = 8.0) -> Any:
    host, _, port = endpoint.partition(":")
    conn = http.client.HTTPConnection(host, int(port or 80), timeout=timeout)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return json.loads(response.read().decode("utf-8"))
    finally:
        conn.close()


def check_tunnel(config: dict) -> dict[str, Any]:
    title = "tunnel    Cloudflared connector"
    service_name = str(config.get("cloudflared_service_name") or "Cloudflared")
    detail: dict[str, Any] = {"service": service_name}
    notes: list[str] = []

    try:
        info = _service_info(service_name)
    except Exception as exc:
        return layer(
            "L2",
            title,
            FAIL,
            {"service": service_name, "error": repr(exc)},
            [f"Windows service {service_name!r} is not installed"],
            "cloudflared.exe service install <TUNNEL_TOKEN>",
        )
    detail.update(info)

    if info["state"].lower() != "running":
        return layer(
            "L2",
            title,
            FAIL,
            detail,
            [f"service state is {info['state']!r}"],
            f"sc.exe start {service_name}",
        )

    endpoint = str(config.get("cloudflared_metrics") or "") or info.get("metrics_flag")
    endpoint_source = "config/flag"
    if not endpoint and info.get("pid"):
        endpoint = _listening_endpoint(int(info["pid"]))
        endpoint_source = "netstat scan"
    detail["metrics"] = endpoint
    detail["metrics_source"] = endpoint_source if endpoint else None

    state = OK
    notes_fix = ""
    if not endpoint:
        return layer(
            "L2",
            title,
            WARN,
            detail,
            ["could not locate the Cloudflared metrics endpoint, so HA health is unknown"],
            "reinstall the service with --metrics 127.0.0.1:20241, or set cloudflared_metrics in config/local.json",
        )
    if endpoint_source == "netstat scan":
        notes.append(
            "metrics port was discovered by scanning; it is random per start. "
            "Pin it so diagnosis stays deterministic."
        )
        notes_fix = "reinstall the service with --metrics 127.0.0.1:20241"
        state = WARN

    expected = int(config.get("expected_ready_connections", 4))
    try:
        ready = _http_json(endpoint, "/ready")
        detail["ready"] = ready
        count = int(ready.get("readyConnections") or 0)
        detail["ready_connections"] = count
        detail["expected_ready_connections"] = expected
        if count == 0:
            state = FAIL
            notes.append("Cloudflared holds no ready connections; the public hostname cannot work")
            notes_fix = f"sc.exe stop {service_name} then sc.exe start {service_name}"
        elif count < expected:
            state = max(state, WARN, key=lambda value: RANK[value])
            notes.append(
                f"{count} of {expected} HA connections are up; the tunnel works but has less redundancy"
            )
    except Exception as exc:
        state = max(state, WARN, key=lambda value: RANK[value])
        notes.append(f"metrics /ready failed: {exc!r}")

    try:
        diag = _http_json(endpoint, "/diag/tunnel")
        edges = [c.get("edgeAddress") for c in diag.get("connections") or []]
        detail["edge_addresses"] = edges
        detail["tunnel_id"] = diag.get("tunnelID")
    except Exception:
        pass

    return layer("L2", title, state, detail, notes, notes_fix)


def _profile_credential(config: dict) -> str | None:
    path = str(config.get("profile_path") or "")
    if not path:
        return None
    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return str(profile.get("token") or "") or None


def check_origin(config: dict) -> dict[str, Any]:
    title = "origin    MCP server on localhost"
    base = str(config.get("origin_base_url") or "http://127.0.0.1:28766")
    parts = urlsplit(base)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    detail: dict[str, Any] = {"origin": base}
    notes: list[str] = []

    credential = _profile_credential(config)
    detail["authenticated"] = bool(credential)
    if credential:
        query_key = "codexpro" + "_" + "token"
        path = f"/mcp?{query_key}={quote(credential, safe='')}"
        accepted = {int(config.get("expected_http_status", 400))}
    else:
        path = "/mcp"
        accepted = {400, 401, 403}
        notes.append("no profile credential available, so only liveness was checked")

    conn_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    try:
        started = time.perf_counter()
        conn = conn_class(host, port, timeout=float(config.get("request_timeout_seconds", 15)))
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            response.read()
            status = int(response.status)
        finally:
            conn.close()
        detail["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    except Exception as exc:
        return layer(
            "L3",
            title,
            FAIL,
            detail | {"error": repr(exc)},
            [f"nothing is answering on {base}"],
            "start the MCP server, then re-run",
        )

    detail["status"] = status
    detail["accepted_status"] = sorted(accepted)
    if status not in accepted:
        return layer(
            "L3",
            title,
            FAIL,
            detail,
            [f"origin answered HTTP {status}, expected one of {sorted(accepted)}"],
            "check the MCP server log",
        )
    return layer("L3", title, OK, detail, notes)


def check_edge(config: dict) -> dict[str, Any]:
    title = "edge      public hostname, end to end"
    host = str(config.get("public_host") or "")
    if not host:
        return layer(
            "L4",
            title,
            FAIL,
            {},
            ["public_host is not set in config/local.json"],
            "copy config/local.example.json to config/local.json and fill it in",
        )
    try:
        from check_mcp import measure_mcp

        measurement = measure_mcp(config)
    except Exception as exc:
        return layer(
            "L4",
            title,
            FAIL,
            {"public_host": host, "error": repr(exc)},
            ["the end-to-end probe could not run"],
            "check profile_path in config/local.json",
        )

    ray = measurement.get("cf_ray") or ""
    detail = {
        "public_host": host,
        "health_class": measurement.get("health_class"),
        "hot_median_ms": measurement.get("hot_median_ms"),
        "hot_statuses": measurement.get("hot_statuses"),
        "cold_ms": (measurement.get("times_ms") or [None])[0],
        "threshold_ms": measurement.get("threshold_ms"),
        "cf_ray": ray,
        "pop": ray.rsplit("-", 1)[-1] if "-" in ray else None,
    }
    health_class = str(measurement.get("health_class") or "unknown")
    if health_class in {"http_failure", "transport_failure"}:
        return layer(
            "L4",
            title,
            FAIL,
            detail,
            [f"end-to-end request failed: {measurement.get('reason') or health_class}"],
            "fix the first failing layer below this one",
        )
    if health_class in {"latency_degraded", "latency_severe"}:
        return layer(
            "L4",
            title,
            WARN,
            detail,
            [f"reachable but slow ({health_class})"],
            "compare with the L1 edge RTT to see whether it is the exit node or the backbone",
        )
    return layer("L4", title, OK, detail)


def check_guardian(config: dict) -> dict[str, Any]:
    title = "guardian  watcher task and reported state"
    detail: dict[str, Any] = {}
    notes: list[str] = []
    state = OK
    fix = ""

    missing = [
        key
        for key in ("public_host", "profile_path", "python_exe", "watcher_task_name")
        if not str(config.get(key) or "").strip()
    ]
    detail["local_config"] = str(LOCAL_CONFIG_PATH)
    detail["local_config_exists"] = LOCAL_CONFIG_PATH.exists()
    if missing:
        detail["missing_local_keys"] = missing
        return layer(
            "L5",
            title,
            FAIL,
            detail,
            ["config/local.json is incomplete: " + ", ".join(missing)],
            "copy config/local.example.json to config/local.json and fill it in",
        )

    for key in ("python_exe", "profile_path"):
        path = Path(str(config[key]))
        detail[key] = str(path)
        if not path.exists():
            notes.append(f"{key} does not exist on this machine: {path}")
            state = FAIL
            fix = "fix the path in config/local.json"

    task_name = str(config["watcher_task_name"])
    detail["watcher_task"] = task_name
    try:
        import win32com.client

        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        task = service.GetFolder("\\").GetTask(task_name)
        task_state = int(task.State)
        detail["watcher_task_state"] = {0: "Unknown", 1: "Disabled", 2: "Queued", 3: "Ready", 4: "Running"}.get(
            task_state, str(task_state)
        )
        if task_state != 4:
            notes.append(f"watcher task is {detail['watcher_task_state']}, not Running")
            state = max(state, WARN, key=lambda value: RANK[value])
            fix = fix or "python control.py start"
    except Exception:
        detail["watcher_task_state"] = "NotInstalled"
        notes.append(f"scheduled task {task_name!r} is not installed")
        state = FAIL
        fix = fix or "python control.py install"

    try:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8-sig"))
        written = datetime.fromisoformat(str(status.get("timestamp")))
        age = round((datetime.now() - written).total_seconds(), 1)
        detail["status_age_seconds"] = age
        detail["phase"] = status.get("phase")
        detail["strategy_version"] = status.get("strategy_version")
        detail["selected_node"] = status.get("selected_node")
        detail["follow_source"] = status.get("follow_source")
        stale = float(config.get("status_stale_seconds", 120))
        if age > stale:
            notes.append(f"runtime/status.json is {age}s old; the watcher loop may be stuck")
            state = max(state, WARN, key=lambda value: RANK[value])
            fix = fix or "python control.py logs --tail 40"
        if status.get("phase") in {"degraded", "error"}:
            notes.append(f"watcher reports phase={status.get('phase')!r}: {status.get('last_error')}")
            state = max(state, WARN, key=lambda value: RANK[value])
    except Exception as exc:
        notes.append(f"runtime/status.json unreadable: {exc!r}")
        state = max(state, WARN, key=lambda value: RANK[value])

    return layer("L5", title, state, detail, notes, fix)


def run_doctor(config: dict) -> dict[str, Any]:
    layers = [
        check_exit(config),
        check_tunnel(config),
        check_origin(config),
        check_edge(config),
        check_guardian(config),
    ]
    verdict = OK
    for item in layers:
        if RANK[item["state"]] > RANK[verdict]:
            verdict = item["state"]
    first_failure = next((item["id"] for item in layers if item["state"] == FAIL), None)
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "machine_name": config.get("machine_name"),
        "strategy_version": config.get("strategy_version"),
        "verdict": verdict,
        "first_failure": first_failure,
        "layers": layers,
    }


def _format_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) if value else "(none)"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def print_report(report: dict[str, Any]) -> None:
    print_line(f"MCP path doctor  {report['timestamp']}  machine={report.get('machine_name')}")
    print_line("")
    for item in report["layers"]:
        print_line(f"{MARK[item['state']]} {item['id']}  {item['title']}")
        for key, value in item["detail"].items():
            print_line(f"         {key:<28} {_format_value(value)}")
        for note in item["notes"]:
            print_line(f"         -> {note}")
        if item["fix"]:
            print_line(f"         fix: {item['fix']}")
        print_line("")
    if report["verdict"] == OK:
        print_line("VERDICT: ok")
    elif report["first_failure"]:
        print_line(f"VERDICT: {report['verdict']} - fix {report['first_failure']} first; layers above it inherit its failure")
    else:
        print_line(f"VERDICT: {report['verdict']}")


if __name__ == "__main__":
    from config_loader import load_config

    print_report(run_doctor(load_config()))

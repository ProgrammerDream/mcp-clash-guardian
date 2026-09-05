"""Inspecting the Cloudflared Windows service and its connector health.

Shared by the watcher and the doctor so there is one definition of "is the
tunnel actually up". `readyConnections` from Cloudflared's own metrics endpoint
is the authoritative answer: the Windows service reporting `Running` only means
the process started, not that it registered with the Cloudflare edge, and that
gap is exactly how a bad exit node turns into a silent outage.
"""
from __future__ import annotations

import http.client
import json
import shlex
import subprocess
import time
from typing import Any


def _wmi():
    import win32com.client

    return win32com.client.GetObject("winmgmts:")


def service_info(name: str) -> dict[str, Any]:
    """Service state plus the flags we care about, never the credential itself."""
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
        info["protocol_flag"] = flag_value("--protocol")
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


def listening_endpoint(pid: int) -> str | None:
    """Find a loopback listener owned by a pid.

    Cloudflared binds a random metrics port unless --metrics pins one, so
    without that flag this scan is the only way to reach /ready.
    """
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


def metrics_endpoint(config: dict, info: dict[str, Any] | None = None) -> tuple[str | None, str]:
    """Return (endpoint, how_we_found_it)."""
    configured = str(config.get("cloudflared_metrics") or "")
    if configured:
        return configured, "config"
    service_name = str(config.get("cloudflared_service_name") or "Cloudflared")
    if info is None:
        try:
            info = service_info(service_name)
        except Exception:
            return None, "unavailable"
    flag = info.get("metrics_flag")
    if flag:
        return str(flag), "flag"
    pid = int(info.get("pid") or 0)
    if pid:
        found = listening_endpoint(pid)
        if found:
            return found, "netstat scan"
    return None, "unavailable"


def http_json(endpoint: str, path: str, timeout: float = 8.0) -> Any:
    host, _, port = endpoint.partition(":")
    conn = http.client.HTTPConnection(host, int(port or 80), timeout=timeout)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return json.loads(response.read().decode("utf-8"))
    finally:
        conn.close()


def read_ready(endpoint: str, timeout: float = 8.0) -> int:
    payload = http_json(endpoint, "/ready", timeout=timeout)
    return int((payload or {}).get("readyConnections") or 0)


def wait_for_ready(config: dict, timeout_seconds: float, poll_seconds: float = 3.0) -> tuple[int, str | None]:
    """Poll until the connector reports at least one ready connection.

    Returns (ready_connections, endpoint). A zero result after the timeout means
    Cloudflared is running but cannot reach the Cloudflare edge over the current
    exit path.
    """
    service_name = str(config.get("cloudflared_service_name") or "Cloudflared")
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    endpoint: str | None = None
    ready = 0
    while True:
        if endpoint is None:
            try:
                info = service_info(service_name)
            except Exception:
                info = None
            if info is not None:
                endpoint, _ = metrics_endpoint(config, info)
        if endpoint:
            try:
                ready = read_ready(endpoint)
                if ready > 0:
                    return ready, endpoint
            except Exception:
                ready = 0
        if time.monotonic() >= deadline:
            return ready, endpoint
        time.sleep(max(0.5, poll_seconds))

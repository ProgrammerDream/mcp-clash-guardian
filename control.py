from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
CONFIG_DIR = ROOT / "config"
LOCAL_CONFIG_PATH = CONFIG_DIR / "local.json"
RUNTIME_DIR = ROOT / "runtime"
STATUS_PATH = RUNTIME_DIR / "status.json"
LOG_PATH = RUNTIME_DIR / "automation.jsonl"
WATCHER_PATH = SRC_DIR / "watcher.py"
REQUIREMENTS_PATH = ROOT / "requirements.txt"

sys.path.insert(0, str(SRC_DIR))
from config_loader import load_config  # noqa: E402

TASK_STATE = {
    0: "Unknown",
    1: "Disabled",
    2: "Queued",
    3: "Ready",
    4: "Running",
}


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_local(values: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG_PATH.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def set_local_value(name: str, value: Any) -> None:
    local = read_json(LOCAL_CONFIG_PATH) or {}
    local[name] = value
    write_local(local)


def append_control_log(event: str, data: dict[str, Any] | None = None) -> None:
    from datetime import datetime

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "level": "INFO",
        "event": event,
        "data": data or {},
    }
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def hidden_creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def bootstrap_runtime_python(config: dict[str, Any]) -> int | None:
    """Re-run control.py with the machine-configured Python when PATH points elsewhere."""
    configured = Path(str(config["python_exe"])).expanduser()
    if not configured.exists():
        raise RuntimeError(f"Configured Python not found: {configured}")

    current = Path(sys.executable)
    current_norm = os.path.normcase(os.path.abspath(str(current)))
    configured_norm = os.path.normcase(os.path.abspath(str(configured)))
    if current_norm == configured_norm:
        return None

    result = subprocess.run(
        [str(configured), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=ROOT,
    )
    return int(result.returncode)


def install_requirements(config: dict[str, Any]) -> None:
    python_exe = Path(str(config["python_exe"]))
    if not python_exe.exists():
        raise RuntimeError(f"Python not found: {python_exe}")
    if not REQUIREMENTS_PATH.exists():
        return
    subprocess.run(
        [str(python_exe), "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH), "--disable-pip-version-check"],
        cwd=ROOT,
        check=True,
        creationflags=hidden_creation_flags(),
    )


def task_service():
    import win32com.client

    service = win32com.client.Dispatch("Schedule.Service")
    service.Connect()
    return service


def task_root():
    return task_service().GetFolder("\\")


def current_user_id(task_name: str | None = None) -> str:
    if task_name:
        existing = get_task(task_name)
        if existing is not None:
            try:
                saved = str(existing.Definition.Principal.UserId or "").strip()
                if saved:
                    return saved
            except Exception:
                pass
    return os.environ.get("USERNAME", "").strip() or getpass.getuser()


def get_task(task_name: str):
    try:
        return task_root().GetTask(task_name)
    except Exception:
        return None


def stop_task(task_name: str) -> None:
    task = get_task(task_name)
    if task is None:
        return
    try:
        for instance in task.GetInstances(0):
            try:
                instance.Stop()
            except Exception:
                pass
    except Exception:
        try:
            task.Stop(0)
        except Exception:
            pass


def start_task(task_name: str) -> None:
    task = get_task(task_name)
    if task is None:
        raise RuntimeError(f"Watcher task not installed: {task_name}")
    task.Run("")


def delete_task(task_name: str) -> None:
    stop_task(task_name)
    root = task_root()
    try:
        root.DeleteTask(task_name, 0)
    except Exception:
        pass


def install_task(config: dict[str, Any]) -> None:
    # Task Scheduler COM constants.
    TASK_TRIGGER_LOGON = 9
    TASK_ACTION_EXEC = 0
    TASK_CREATE_OR_UPDATE = 6
    TASK_LOGON_INTERACTIVE_TOKEN = 3
    TASK_RUNLEVEL_LUA = 0
    TASK_INSTANCES_IGNORE_NEW = 2

    task_name = str(config["watcher_task_name"])
    python_exe = Path(str(config["python_exe"]))
    pythonw_exe = python_exe.with_name("pythonw.exe")
    watcher_exe = pythonw_exe if pythonw_exe.exists() else python_exe
    if not watcher_exe.exists():
        raise RuntimeError(f"Python not found: {watcher_exe}")
    if not WATCHER_PATH.exists():
        raise RuntimeError(f"Watcher not found: {WATCHER_PATH}")

    root = task_root()
    definition = task_service().NewTask(0)
    definition.RegistrationInfo.Description = (
        "MCP Clash Guardian: observe Mihomo/TUN, verify real MCP health, "
        "and recover Cloudflared paths only after confirmed failure."
    )

    settings = definition.Settings
    settings.Enabled = True
    settings.StartWhenAvailable = True
    settings.AllowDemandStart = True
    settings.DisallowStartIfOnBatteries = False
    settings.StopIfGoingOnBatteries = False
    settings.ExecutionTimeLimit = "PT0S"
    settings.MultipleInstances = TASK_INSTANCES_IGNORE_NEW
    settings.RestartCount = 3
    settings.RestartInterval = "PT1M"

    user_id = current_user_id(task_name)
    principal = definition.Principal
    principal.UserId = user_id
    principal.LogonType = TASK_LOGON_INTERACTIVE_TOKEN
    principal.RunLevel = TASK_RUNLEVEL_LUA

    trigger = definition.Triggers.Create(TASK_TRIGGER_LOGON)
    trigger.Enabled = True
    trigger.UserId = user_id

    action = definition.Actions.Create(TASK_ACTION_EXEC)
    action.Path = str(watcher_exe)
    action.Arguments = f'"{WATCHER_PATH}"'
    action.WorkingDirectory = str(ROOT)

    stop_task(task_name)
    root.RegisterTaskDefinition(
        task_name,
        definition,
        TASK_CREATE_OR_UPDATE,
        user_id,
        None,
        TASK_LOGON_INTERACTIVE_TOKEN,
    )
    set_local_value("enabled", True)
    start_task(task_name)
    append_control_log(
        "automation_installed",
        {"task": task_name, "run_level": "Limited", "watcher_exe": str(watcher_exe)},
    )


def process_running(name: str) -> bool:
    import win32com.client

    wmi = win32com.client.GetObject("winmgmts:")
    safe = name.replace("'", "''")
    rows = wmi.ExecQuery(f"SELECT ProcessId FROM Win32_Process WHERE Name='{safe}'")
    return any(True for _ in rows)


def status_dict(config: dict[str, Any]) -> dict[str, Any]:
    task = get_task(str(config["watcher_task_name"]))
    status = read_json(STATUS_PATH) or {}
    task_state = "NotInstalled" if task is None else TASK_STATE.get(int(task.State), str(task.State))
    return {
        "machine_name": config.get("machine_name"),
        "strategy_version": status.get("strategy_version", config.get("strategy_version")),
        "enabled": bool(config.get("enabled", True)),
        "watcher_task": config.get("watcher_task_name"),
        "watcher_state": task_state,
        "v2rayn_process": "Running" if process_running("v2rayN.exe") else "Stopped",
        "singbox_process": "Running" if process_running("sing-box.exe") else "Stopped",
        "phase": status.get("phase", "unknown"),
        "trigger": status.get("trigger"),
        "tun_up": status.get("tun_up"),
        "mihomo_pid": status.get("mihomo_pid"),
        "mihomo_api": (status.get("mihomo_api") or {}).get("available"),
        "selected_node": status.get("selected_node"),
        "argotunnel_connections": status.get("argotunnel_connection_count"),
        "mcp_ok": status.get("mcp_ok"),
        "mcp_health_class": status.get("mcp_health_class"),
        "hot_median_ms": status.get("hot_median_ms"),
        "cf_ray": status.get("cf_ray"),
        "last_error": status.get("last_error"),
        "local_config": str(LOCAL_CONFIG_PATH),
        "status_file": str(STATUS_PATH),
        "log_file": str(LOG_PATH),
    }


def print_status(config: dict[str, Any]) -> None:
    data = status_dict(config)
    width = max(len(key) for key in data)
    for key, value in data.items():
        print(f"{key:<{width}} : {value}")


def show_logs(tail: int) -> None:
    if not LOG_PATH.exists():
        print("NO_LOG_YET")
        return
    with LOG_PATH.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in deque(handle, maxlen=max(1, tail)):
            print(line.rstrip())


def run_once(config: dict[str, Any]) -> None:
    python_exe = str(config["python_exe"])
    append_control_log("manual_run_started")
    result = subprocess.run(
        [python_exe, str(WATCHER_PATH), "--once"],
        cwd=ROOT,
        creationflags=hidden_creation_flags(),
    )
    append_control_log("manual_run_finished", {"exit_code": result.returncode})
    if result.returncode != 0:
        raise RuntimeError(f"Manual recovery failed: exit={result.returncode}")
    print_status(load_config())


def git_pull_ff_only() -> None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "pull", "--ff-only"],
        cwd=ROOT,
        creationflags=hidden_creation_flags(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"git pull --ff-only failed: exit={result.returncode}")


def update(config: dict[str, Any]) -> None:
    task_name = str(config["watcher_task_name"])
    stop_task(task_name)
    try:
        git_pull_ff_only()
        refreshed = load_config()
        install_requirements(refreshed)
        install_task(refreshed)
        append_control_log(
            "automation_updated",
            {"strategy_version": refreshed.get("strategy_version")},
        )
        time.sleep(3)
        print_status(load_config())
    except Exception:
        try:
            start_task(task_name)
        except Exception:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Clash Guardian control plane")
    parser.add_argument(
        "action",
        choices=["status", "logs", "start", "stop", "run", "install", "uninstall", "rollback", "update"],
        nargs="?",
        default="status",
    )
    parser.add_argument("--tail", type=int, default=30)
    args = parser.parse_args()

    config = load_config()
    bootstrap_exit = bootstrap_runtime_python(config)
    if bootstrap_exit is not None:
        return bootstrap_exit

    task_name = str(config["watcher_task_name"])

    if args.action == "status":
        print_status(config)
    elif args.action == "logs":
        show_logs(args.tail)
    elif args.action == "start":
        set_local_value("enabled", True)
        start_task(task_name)
        append_control_log("automation_started")
        print("AUTOMATION_STARTED")
    elif args.action == "stop":
        set_local_value("enabled", False)
        stop_task(task_name)
        append_control_log("automation_stopped")
        print("AUTOMATION_STOPPED")
    elif args.action == "run":
        run_once(config)
    elif args.action == "install":
        install_requirements(config)
        install_task(load_config())
        print(f"AUTOMATION_INSTALLED={task_name}")
    elif args.action in {"uninstall", "rollback"}:
        delete_task(task_name)
        set_local_value("enabled", False)
        append_control_log(f"automation_{args.action}", {"task": task_name})
        if args.action == "rollback":
            print("ROLLBACK_OK: watcher removed; Clash Verge/Mihomo configuration was not modified.")
        else:
            print(f"AUTOMATION_UNINSTALLED={task_name}")
    elif args.action == "update":
        update(config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

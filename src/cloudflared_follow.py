from __future__ import annotations

import time
from typing import Protocol


class ServiceController(Protocol):
    def state(self, name: str) -> tuple[str, int]: ...

    def stop(self, name: str) -> None: ...

    def start(self, name: str) -> None: ...

    def kill(self, pid: int) -> None: ...

    def sleep(self, seconds: float) -> None: ...


def detect_follow_reason(
    *,
    last_tun: bool,
    current_tun: bool,
    last_pid: int | None,
    current_pid: int | None,
    last_node: str | None,
    current_node: str | None,
) -> str | None:
    if not current_tun:
        return None
    if current_tun and not last_tun:
        return "tun_up"
    if last_pid and current_pid and last_pid != current_pid:
        return "mihomo_pid_changed"
    if last_node and current_node and last_node != current_node:
        return "manual_node_changed"
    return None


def restart_service_with_timeout(
    controller: ServiceController,
    name: str,
    *,
    stop_timeout_seconds: float = 10,
    kill_timeout_seconds: float = 5,
    start_timeout_seconds: float = 15,
    poll_seconds: float = 0.5,
) -> dict[str, object]:
    forced_kill = False
    state, pid = controller.state(name)

    if state.lower() != "stopped":
        controller.stop(name)
        deadline = time.monotonic() + max(0.0, stop_timeout_seconds)
        while True:
            state, pid = controller.state(name)
            if state.lower() == "stopped":
                break
            if time.monotonic() >= deadline:
                if pid:
                    controller.kill(pid)
                    forced_kill = True
                    kill_deadline = time.monotonic() + max(0.0, kill_timeout_seconds)
                    while True:
                        state, pid = controller.state(name)
                        if state.lower() == "stopped":
                            break
                        if time.monotonic() >= kill_deadline:
                            break
                        controller.sleep(max(0.0, poll_seconds))
                break
            controller.sleep(max(0.0, poll_seconds))

    if state.lower() != "stopped":
        raise RuntimeError(f"Service {name} did not stop; state={state!r} pid={pid}")

    controller.start(name)
    deadline = time.monotonic() + max(0.0, start_timeout_seconds)
    while True:
        state, pid = controller.state(name)
        if state.lower() == "running":
            return {"state": state, "pid": pid, "forced_kill": forced_kill}
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Service {name} did not start; state={state!r} pid={pid}")
        controller.sleep(max(0.0, poll_seconds))

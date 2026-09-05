from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

DEFAULT_ARGOTUNNEL_NETWORKS = ("udp",)


def select_argotunnel_connections(
    payload: dict[str, Any] | None,
    *,
    port: str | int = 7844,
    host_suffix: str = "argotunnel.com",
    networks: Iterable[str] = DEFAULT_ARGOTUNNEL_NETWORKS,
) -> list[dict[str, Any]]:
    """Pick the Mihomo connections that carry the Cloudflared tunnel.

    Pure so both the watcher and the doctor share one definition of "this is a
    tunnel connection" instead of drifting apart.
    """
    accepted = {str(value).lower() for value in networks}
    wanted_port = str(port)
    suffix = str(host_suffix).lower()
    result: list[dict[str, Any]] = []
    for item in (payload or {}).get("connections", []) or []:
        metadata = item.get("metadata") or {}
        host = str(metadata.get("host") or "").lower()
        geo = metadata.get("destinationGeoIP") or []
        if isinstance(geo, str):
            geo = [geo]
        is_cloudflare = any(str(value).lower() == "cloudflare" for value in geo)
        if (
            str(metadata.get("network") or "").lower() in accepted
            and str(metadata.get("destinationPort") or "") == wanted_port
            and (host.endswith(suffix) or is_cloudflare)
        ):
            result.append(item)
    return result


def argotunnel_leaf_nodes(items: Iterable[dict[str, Any]]) -> list[str]:
    """Leaf proxy of every tunnel connection, in Mihomo's chain order.

    Mihomo reports `chains` outermost-last, so `chains[0]` is the proxy that
    actually egresses. This is ground truth: it is where the tunnel is, not
    where a policy group says it should be.
    """
    nodes: list[str] = []
    for item in items:
        chains = item.get("chains") or []
        if chains:
            nodes.append(str(chains[0]))
    return nodes


def resolve_follow_target(
    *,
    group_node: str | None,
    chain_nodes: Sequence[str],
) -> tuple[str | None, str]:
    """Decide which node Cloudflared should be following, and say where it came from.

    The configured policy group is the intent, but subscriptions rename and drop
    groups, so a missing group must not blind the watcher. The tunnel's own
    chains are the fallback because they cannot be stale by construction.
    """
    if group_node:
        return group_node, "group"
    unique = {node for node in chain_nodes if node}
    if len(unique) == 1:
        return next(iter(unique)), "argotunnel_chain"
    if unique:
        return None, "argotunnel_chain_mixed"
    return None, "none"


def plan_tunnel_recovery(
    *,
    ready_connections: int,
    previous_node: str | None,
    current_node: str | None,
    rollback_enabled: bool,
    can_reselect: bool,
) -> str:
    """Decide what to do after Cloudflared was restarted for a node change.

    A restarted service that reports `Running` proves only that the process
    started. If it never registers a connection with the Cloudflare edge, the
    public hostname returns 1033 and the outage is total and silent, so a node
    that cannot carry the tunnel has to be undone rather than left in place.

    Returns one of: healthy, rollback, degraded.
    """
    if ready_connections > 0:
        return "healthy"
    if not rollback_enabled or not can_reselect:
        return "degraded"
    if not previous_node or previous_node == current_node:
        return "degraded"
    return "rollback"


def stabilize_tun_state(
    *,
    last_stable: bool,
    observed: bool,
    down_streak: int,
    required_down: int,
) -> tuple[bool, int]:
    """Debounce the TUN adapter's falling edge.

    `tun_up` reads WMI, which momentarily reports no adapter row while Windows
    re-enumerates. A single false reading used to flip the state down and the
    next poll flipped it back up, and that rising edge restarted Cloudflared for
    nothing. Only a sustained absence counts as down; up is always immediate.
    """
    if observed:
        return True, 0
    streak = down_streak + 1
    if streak >= max(1, required_down):
        return False, streak
    return last_stable, streak


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

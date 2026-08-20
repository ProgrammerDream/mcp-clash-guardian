from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloudflared_follow import detect_follow_reason, restart_service_with_timeout


class DetectFollowReasonTests(unittest.TestCase):
    def test_tun_rising_edge_requests_restart(self) -> None:
        self.assertEqual(
            detect_follow_reason(
                last_tun=False,
                current_tun=True,
                last_pid=100,
                current_pid=100,
                last_node="新加坡01aws",
                current_node="新加坡01aws",
            ),
            "tun_up",
        )

    def test_manual_node_change_requests_restart(self) -> None:
        self.assertEqual(
            detect_follow_reason(
                last_tun=True,
                current_tun=True,
                last_pid=100,
                current_pid=100,
                last_node="新加坡01aws",
                current_node="新加坡08aws",
            ),
            "manual_node_changed",
        )

    def test_mihomo_restart_requests_restart_while_tun_is_up(self) -> None:
        self.assertEqual(
            detect_follow_reason(
                last_tun=True,
                current_tun=True,
                last_pid=100,
                current_pid=200,
                last_node="新加坡01aws",
                current_node="新加坡01aws",
            ),
            "mihomo_pid_changed",
        )

    def test_node_change_while_tun_down_does_not_restart(self) -> None:
        self.assertIsNone(
            detect_follow_reason(
                last_tun=False,
                current_tun=False,
                last_pid=100,
                current_pid=100,
                last_node="新加坡01aws",
                current_node="新加坡08aws",
            )
        )


class FakeServiceController:
    def __init__(self, states: list[tuple[str, int]]) -> None:
        self.states = list(states)
        self.actions: list[tuple[str, object]] = []

    def state(self, name: str) -> tuple[str, int]:
        self.actions.append(("state", name))
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def stop(self, name: str) -> None:
        self.actions.append(("stop", name))

    def start(self, name: str) -> None:
        self.actions.append(("start", name))
        self.states = [("Running", 222)]

    def kill(self, pid: int) -> None:
        self.actions.append(("kill", pid))
        self.states = [("Stopped", 0)]

    def sleep(self, seconds: float) -> None:
        self.actions.append(("sleep", seconds))


class RestartServiceTests(unittest.TestCase):
    def test_hung_service_is_killed_then_started(self) -> None:
        controller = FakeServiceController([("Stop Pending", 111)])

        result = restart_service_with_timeout(
            controller,
            "Cloudflared",
            stop_timeout_seconds=0,
            start_timeout_seconds=1,
            poll_seconds=0,
        )

        self.assertTrue(result["forced_kill"])
        self.assertIn(("kill", 111), controller.actions)
        self.assertIn(("start", "Cloudflared"), controller.actions)
        self.assertEqual(result["state"], "Running")

    def test_waits_for_service_manager_to_report_stopped_after_kill(self) -> None:
        controller = FakeServiceController([("Stop Pending", 111)])

        def delayed_kill(pid: int) -> None:
            controller.actions.append(("kill", pid))
            controller.states = [("Stop Pending", pid), ("Stopped", 0)]

        controller.kill = delayed_kill  # type: ignore[method-assign]

        result = restart_service_with_timeout(
            controller,
            "Cloudflared",
            stop_timeout_seconds=0,
            kill_timeout_seconds=1,
            start_timeout_seconds=1,
            poll_seconds=0,
        )

        self.assertTrue(result["forced_kill"])
        self.assertIn(("start", "Cloudflared"), controller.actions)
        self.assertEqual(result["state"], "Running")


if __name__ == "__main__":
    unittest.main()

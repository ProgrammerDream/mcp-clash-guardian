from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cloudflared_follow import (
    argotunnel_leaf_nodes,
    plan_tunnel_recovery,
    resolve_follow_target,
    select_argotunnel_connections,
    stabilize_tun_state,
)


def connection(node: str, *, network: str = "udp", port: str = "7844") -> dict:
    return {
        "id": f"{node}-{network}-{port}",
        "chains": [node, "group"],
        "metadata": {
            "network": network,
            "host": "region1.v2.argotunnel.com",
            "destinationPort": port,
            "destinationGeoIP": ["cloudflare"],
        },
    }


class SelectArgotunnelConnectionTests(unittest.TestCase):
    def test_only_tunnel_transport_on_the_tunnel_port_matches(self) -> None:
        payload = {
            "connections": [
                connection("JP-A"),
                connection("JP-A", network="tcp"),
                connection("JP-A", port="443"),
            ]
        }

        self.assertEqual(1, len(select_argotunnel_connections(payload)))
        self.assertEqual(2, len(select_argotunnel_connections(payload, networks=("udp", "tcp"))))

    def test_leaf_node_is_the_innermost_chain_entry(self) -> None:
        items = select_argotunnel_connections({"connections": [connection("JP-A")]})

        self.assertEqual(["JP-A"], argotunnel_leaf_nodes(items))

    def test_missing_payload_is_not_an_error(self) -> None:
        self.assertEqual([], select_argotunnel_connections(None))


class ResolveFollowTargetTests(unittest.TestCase):
    def test_policy_group_is_the_intent_and_wins(self) -> None:
        self.assertEqual(
            ("JP-A", "group"),
            resolve_follow_target(group_node="JP-A", chain_nodes=["SG-B", "SG-B"]),
        )

    def test_missing_group_falls_back_to_the_live_tunnel_chains(self) -> None:
        self.assertEqual(
            ("SG-B", "argotunnel_chain"),
            resolve_follow_target(group_node=None, chain_nodes=["SG-B", "SG-B"]),
        )

    def test_a_straddled_tunnel_yields_no_target_rather_than_a_guess(self) -> None:
        self.assertEqual(
            (None, "argotunnel_chain_mixed"),
            resolve_follow_target(group_node=None, chain_nodes=["SG-B", "JP-A"]),
        )

    def test_no_group_and_no_tunnel_is_reported_as_unresolved(self) -> None:
        self.assertEqual((None, "none"), resolve_follow_target(group_node=None, chain_nodes=[]))


class StabilizeTunStateTests(unittest.TestCase):
    def test_up_is_accepted_immediately(self) -> None:
        self.assertEqual(
            (True, 0),
            stabilize_tun_state(last_stable=False, observed=True, down_streak=5, required_down=2),
        )

    def test_a_single_missing_wmi_reading_does_not_take_tun_down(self) -> None:
        state, streak = stabilize_tun_state(
            last_stable=True, observed=False, down_streak=0, required_down=2
        )

        self.assertTrue(state)
        self.assertEqual(1, streak)

    def test_a_sustained_absence_does_take_tun_down(self) -> None:
        state, streak = stabilize_tun_state(
            last_stable=True, observed=False, down_streak=1, required_down=2
        )

        self.assertFalse(state)
        self.assertEqual(2, streak)

    def test_a_flap_never_produces_a_rising_edge(self) -> None:
        # down, up, down, up at required_down=2 must stay up the whole way, so
        # detect_follow_reason never sees the False -> True transition that used
        # to restart Cloudflared for nothing.
        state, streak = True, 0
        for observed in (False, True, False, True):
            state, streak = stabilize_tun_state(
                last_stable=state, observed=observed, down_streak=streak, required_down=2
            )
            self.assertTrue(state)


class PlanTunnelRecoveryTests(unittest.TestCase):
    def plan(self, **overrides) -> str:
        kwargs = {
            "ready_connections": 0,
            "previous_node": "SG-OK",
            "current_node": "TW-BAD",
            "rollback_enabled": True,
            "can_reselect": True,
        }
        kwargs.update(overrides)
        return plan_tunnel_recovery(**kwargs)

    def test_a_registered_connection_is_all_that_healthy_means(self) -> None:
        self.assertEqual("healthy", self.plan(ready_connections=1))

    def test_a_node_that_cannot_carry_the_tunnel_is_rolled_back(self) -> None:
        self.assertEqual("rollback", self.plan())

    def test_no_known_good_node_leaves_the_selection_alone(self) -> None:
        self.assertEqual("degraded", self.plan(previous_node=None))

    def test_a_node_already_rolled_back_once_is_not_fought_over(self) -> None:
        # can_reselect goes false for a node the watcher already undid, so a
        # deliberate second attempt by the operator is reported, not reverted.
        self.assertEqual("degraded", self.plan(can_reselect=False))

    def test_rollback_can_be_switched_off_entirely(self) -> None:
        self.assertEqual("degraded", self.plan(rollback_enabled=False))

    def test_restarting_on_the_same_node_has_nothing_to_roll_back_to(self) -> None:
        self.assertEqual("degraded", self.plan(previous_node="SG-OK", current_node="SG-OK"))


class FollowPhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        import watcher

        self.follow_phase = watcher.follow_phase

    def test_a_blind_watcher_reports_degraded_not_monitoring(self) -> None:
        self.assertEqual(
            "degraded",
            self.follow_phase(tun=True, pid=100, api={"available": False, "error": "boom"}),
        )

    def test_tun_or_mihomo_missing_reports_waiting(self) -> None:
        self.assertEqual("waiting", self.follow_phase(tun=False, pid=100, api={"available": True}))
        self.assertEqual("waiting", self.follow_phase(tun=True, pid=None, api={"available": True}))

    def test_resolved_target_reports_monitoring(self) -> None:
        self.assertEqual(
            "monitoring",
            self.follow_phase(tun=True, pid=100, api={"available": True, "selected_node": "JP-A"}),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from control import should_apply_region_policy


class RegionPolicyGateTests(unittest.TestCase):
    def test_follow_mode_never_applies_region_policy_even_if_stale_flag_is_true(self) -> None:
        config = {
            "watcher_mode": "cloudflared-follow",
            "region_priority_enabled": True,
        }
        self.assertFalse(should_apply_region_policy(config))

    def test_guardian_mode_can_apply_region_policy_when_explicitly_enabled(self) -> None:
        config = {
            "watcher_mode": "guardian",
            "region_priority_enabled": True,
        }
        self.assertTrue(should_apply_region_policy(config))


if __name__ == "__main__":
    unittest.main()

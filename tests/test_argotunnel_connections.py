from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import watcher


def connection(connection_id: str, network: str, *, port: str = "7844") -> dict:
    return {
        "id": connection_id,
        "chains": ["test-node"],
        "metadata": {
            "network": network,
            "host": "region.argotunnel.com",
            "destinationPort": port,
            "destinationGeoIP": ["cloudflare"],
        },
    }


class ArgotunnelConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "mihomo_pipe": r"\\.\pipe\test-mihomo",
            "argotunnel_port": 7844,
            "argotunnel_host_suffix": "argotunnel.com",
        }
        self.payload = {
            "connections": [
                connection("quic", "udp"),
                connection("http2", "tcp"),
                connection("other-port", "tcp", port="443"),
            ]
        }

    def test_recovery_connection_scope_remains_udp_only(self) -> None:
        with patch.object(watcher, "connections", return_value=self.payload):
            matches = watcher.argotunnel_connections(self.config)

        self.assertEqual(["quic"], [item["id"] for item in matches])

    def test_status_counts_tcp_and_udp_7844_connections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "status.json"
            with (
                patch.object(watcher, "connections", return_value=self.payload),
                patch.object(watcher, "STATUS_PATH", status_path),
                patch.object(watcher, "tun_up", return_value=True),
                patch.object(watcher, "mihomo_pid", return_value=1234),
            ):
                watcher.write_status(
                    self.config,
                    "monitoring",
                    "test",
                    None,
                    {"available": True},
                )

            status = json.loads(status_path.read_text(encoding="utf-8"))

        self.assertEqual(2, status["argotunnel_connection_count"])


if __name__ == "__main__":
    unittest.main()

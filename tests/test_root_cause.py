import tempfile
import time
import unittest
from pathlib import Path

import server


class RootCauseIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        server.DATA_DIR = Path(self.tmp.name)
        server.DB_PATH = server.DATA_DIR / "test.sqlite3"
        server.init_db()
        self.base = int(time.time()) - 1000

    def tearDown(self):
        self.tmp.cleanup()

    def status(self, ts, pi=3, worker=True, web=True, latency=20.0):
        ports = {
            str(port): {"reachable": index < pi, "latency_ms": 1.0}
            for index, port in enumerate((31401, 31402, 31403))
        }
        return {
            "checked_ts": ts,
            "pi_node": {"reachable_ports": pi, "ports": ports},
            "livingpanda": {
                "data_worker": {
                    "reachable": worker,
                    "latency_ms": latency if worker else None,
                },
                "local_web": {
                    "reachable": web,
                    "latency_ms": 2.0 if web else None,
                },
            },
            "solo_host": {
                "memory_current_mb": 10.0,
                "uptime_seconds": None,
            },
        }

    def evidence(
        self,
        runtime,
        internet=True,
        reconnect=0,
        ws=0,
        rpc=0,
        stable=0,
        pc_fresh=True,
        boot_at="boot-A",
        wifi_state="connected",
    ):
        return {
            "bridge_ok": True,
            "runtime_uptime_seconds": float(runtime),
            "bridge_runtime_uptime_seconds": float(runtime),
            "internet_ok": internet,
            "internet_latency_ms": 10.0 if internet else None,
            "pc_intel_freshness_seconds": 20.0 if pc_fresh else 7200.0,
            "pc_internet_ok": internet,
            "pc_connect_latency_ms": 15.0 if internet else None,
            "pc_boot_at": boot_at,
            "pc_uptime_seconds": float(runtime) + 500.0,
            "pc_wifi_state": wifi_state,
            "pc_wifi_signal": 80.0 if wifi_state == "connected" else 0.0,
            "cloud_log_file": "device-test.log",
            "cloud_log_size_bytes": 1000,
            "cloud_ws_1006_count": ws,
            "cloud_reconnect_count": reconnect,
            "cloud_rpc_timeout_count": rpc,
            "cloud_stable_count": stable,
            "cloud_mcp_connected_count": 1,
            "payload": {},
        }

    def warm(self, runtime_start=10000):
        for i in range(15):
            ts = self.base + i * 10
            server.record_sample(
                self.status(ts, latency=20.0 + (i % 3)),
                self.evidence(runtime_start + i * 10),
            )

    def newest_incident(self):
        rows = server.incidents(10)
        self.assertTrue(rows)
        return rows[0]

    def test_docker_restart_is_high_confidence_root_cause(self):
        self.warm(10000)
        server.record_sample(
            self.status(self.base + 150, pi=0, worker=False, web=False),
            self.evidence(25, internet=True),
        )
        server.record_sample(
            self.status(self.base + 160),
            self.evidence(35, internet=True),
        )

        incident = self.newest_incident()
        root = incident["root_cause"]
        self.assertEqual(root["cause"], "docker_restart")
        self.assertEqual(root["confidence"], "high")
        self.assertTrue(
            any("runtime uptime reset" in item for item in root["evidence"])
        )

    def test_windows_restart_beats_docker_restart_when_boot_changes(self):
        self.warm(15000)
        server.record_sample(
            self.status(self.base + 150, pi=0, worker=False, web=False),
            self.evidence(20, internet=True, boot_at="boot-B"),
        )
        server.record_sample(
            self.status(self.base + 160),
            self.evidence(30, internet=True, boot_at="boot-B"),
        )

        incident = self.newest_incident()
        root = incident["root_cause"]
        self.assertEqual(root["cause"], "host_restart")
        self.assertEqual(root["confidence"], "high")
        self.assertTrue(
            any("host boot timestamp changed" in item for item in root["evidence"])
        )

    def test_network_interruption_uses_cloud_reconnect_evidence(self):
        self.warm(20000)
        server.record_sample(
            self.status(self.base + 150, pi=0, worker=False, web=False),
            self.evidence(
                20150,
                internet=False,
                reconnect=1,
                ws=1,
            ),
        )
        server.record_sample(
            self.status(self.base + 160),
            self.evidence(
                20160,
                internet=True,
                reconnect=2,
                ws=2,
                stable=1,
            ),
        )

        incident = self.newest_incident()
        root = incident["root_cause"]
        self.assertEqual(root["cause"], "network_interruption")
        self.assertEqual(root["confidence"], "high")
        self.assertTrue(
            any("Cloud DEV reconnect counter" in item for item in root["evidence"])
        )
        self.assertTrue(
            any("internet probe failed" in item for item in root["evidence"])
        )

    def test_isolated_worker_failure_points_to_worker_service(self):
        self.warm(30000)
        server.record_sample(
            self.status(self.base + 150, pi=3, worker=False, web=True),
            self.evidence(30150, internet=True),
        )
        server.record_sample(
            self.status(self.base + 160),
            self.evidence(30160, internet=True),
        )

        incident = self.newest_incident()
        root = incident["root_cause"]
        self.assertEqual(root["cause"], "livingpanda_worker")
        self.assertEqual(root["confidence"], "high")
        self.assertTrue(
            any("LivingPanda worker failure" in item for item in root["evidence"])
        )


if __name__ == "__main__":
    unittest.main()

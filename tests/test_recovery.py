import tempfile
import time
import unittest
from pathlib import Path

import server


class RecoveryIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        server.DATA_DIR = Path(self.tmp.name)
        server.DB_PATH = server.DATA_DIR / "test.sqlite3"
        self.old_window = server.RECOVERY_STABILITY_SECONDS
        server.RECOVERY_STABILITY_SECONDS = 30
        server.init_db()
        self.base = int(time.time()) - 1000

    def tearDown(self):
        server.RECOVERY_STABILITY_SECONDS = self.old_window
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
        boot_at="boot-A",
    ):
        return {
            "bridge_ok": True,
            "runtime_uptime_seconds": float(runtime),
            "bridge_runtime_uptime_seconds": float(runtime),
            "internet_ok": internet,
            "internet_latency_ms": 10.0 if internet else None,
            "pc_intel_freshness_seconds": 20.0,
            "pc_internet_ok": internet,
            "pc_connect_latency_ms": 15.0 if internet else None,
            "pc_boot_at": boot_at,
            "pc_uptime_seconds": float(runtime) + 500.0,
            "pc_wifi_state": "connected" if internet else "disconnected",
            "pc_wifi_signal": 80.0 if internet else 0.0,
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

    def closed_with_recovery(self):
        rows = server.incidents(20)
        return [x for x in rows if x["status"] == "closed" and x["recovery"]]

    def test_staggered_recovery_is_timed_and_verified_stable(self):
        self.warm(10000)

        server.record_sample(
            self.status(self.base + 150, pi=0, worker=False, web=False),
            self.evidence(25, internet=True),
        )
        server.record_sample(
            self.status(self.base + 160, pi=0, worker=True, web=True),
            self.evidence(35, internet=True),
        )
        server.record_sample(
            self.status(self.base + 170),
            self.evidence(45, internet=True),
        )

        incident = self.closed_with_recovery()[0]
        self.assertEqual(incident["root_cause"]["cause"], "docker_restart")
        self.assertEqual(incident["recovery"]["status"], "pending_stability")
        self.assertTrue(
            incident["recovery"]["action"].startswith("No intervention required")
        )
        self.assertEqual(
            incident["recovery"]["components"]["worker_recovery_seconds"], 10
        )
        self.assertEqual(
            incident["recovery"]["components"]["local_web_recovery_seconds"], 10
        )
        self.assertEqual(
            incident["recovery"]["components"]["pi_recovery_seconds"], 20
        )
        self.assertEqual(
            incident["recovery"]["components"]["incident_recovery_seconds"], 20
        )

        for offset, runtime in ((180, 55), (190, 65), (200, 75)):
            server.record_sample(
                self.status(self.base + offset),
                self.evidence(runtime, internet=True),
            )

        incident = self.closed_with_recovery()[0]
        self.assertEqual(incident["recovery"]["status"], "verified_stable")
        self.assertTrue(
            incident["recovery"]["outcome"]["no_related_failure"]
        )
        self.assertEqual(
            incident["recovery"]["outcome"]["stable_window_seconds"], 30
        )

    def test_related_failure_marks_recovery_recurred_and_escalates(self):
        self.warm(20000)

        server.record_sample(
            self.status(self.base + 150, pi=3, worker=False, web=True),
            self.evidence(20150, internet=True),
        )
        server.record_sample(
            self.status(self.base + 160),
            self.evidence(20160, internet=True),
        )

        first = self.closed_with_recovery()[0]
        self.assertEqual(first["root_cause"]["cause"], "livingpanda_worker")
        self.assertEqual(first["recovery"]["status"], "pending_stability")

        server.record_sample(
            self.status(self.base + 170, pi=3, worker=False, web=True),
            self.evidence(20170, internet=True),
        )

        closed = self.closed_with_recovery()
        first = next(x for x in closed if x["id"] == first["id"])
        self.assertEqual(first["recovery"]["status"], "recurred")
        self.assertEqual(first["recovery"]["mode"], "inspect")
        self.assertIn("Repeated failure detected", first["recovery"]["action"])
        self.assertTrue(
            first["recovery"]["outcome"]["operator_action_required"]
        )
        self.assertEqual(
            first["recovery"]["outcome"]["seconds_after_recovery"], 10
        )

    def test_recommendation_never_executes_a_repair(self):
        self.warm(30000)

        server.record_sample(
            self.status(self.base + 150, pi=0, worker=False, web=False),
            self.evidence(25, internet=True),
        )
        server.record_sample(
            self.status(self.base + 160),
            self.evidence(35, internet=True),
        )

        incident = self.closed_with_recovery()[0]
        recovery = incident["recovery"]
        self.assertIn(recovery["mode"], ("observe", "inspect"))
        self.assertFalse(
            recovery["outcome"].get("operator_action_required", False)
        )
        blocked_terms = (
            "docker restart ",
            "shutdown ",
            "powershell ",
            "cmd.exe",
            "commander control",
        )
        action = recovery["action"].lower()
        self.assertFalse(any(term in action for term in blocked_terms))


if __name__ == "__main__":
    unittest.main()

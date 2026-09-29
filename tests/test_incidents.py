import tempfile
import time
import unittest
from pathlib import Path

import server


class IncidentIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        server.DATA_DIR = Path(self.tmp.name)
        server.DB_PATH = server.DATA_DIR / "test.sqlite3"
        server.init_db()
        self.base = int(time.time()) - 600

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
            "solo_host": {"memory_current_mb": 10.0},
        }

    def warm_baseline(self):
        for i in range(15):
            server.record_sample(
                self.status(self.base + i * 10, latency=20.0 + (i % 3))
            )

    def test_pi_incident_duration_and_recovery(self):
        self.warm_baseline()
        for offset in (150, 160, 170, 180):
            server.record_sample(self.status(self.base + offset, pi=2, latency=21.0))
        server.record_sample(self.status(self.base + 190, pi=3, latency=20.0))

        rows = server.incidents(10)
        self.assertEqual(len(rows), 1)
        incident = rows[0]
        self.assertEqual(incident["category"], "pi_connectivity")
        self.assertEqual(incident["status"], "closed")
        self.assertEqual(incident["duration_seconds"], 40)
        self.assertIn("recovered automatically", incident["summary"])

    def test_worker_and_pi_faults_correlate_into_one_incident(self):
        self.warm_baseline()
        server.record_sample(self.status(self.base + 150, worker=False, web=True))
        server.record_sample(
            self.status(self.base + 160, pi=0, worker=False, web=False)
        )
        server.record_sample(self.status(self.base + 170, latency=22.0))

        rows = server.incidents(10)
        self.assertEqual(len(rows), 1)
        incident = rows[0]
        self.assertEqual(incident["category"], "host_network")
        self.assertEqual(incident["duration_seconds"], 20)
        self.assertEqual(
            set(incident["details"]["correlated_categories"]),
            {"host_network", "livingpanda_worker"},
        )

    def test_adaptive_latency_baseline_opens_and_closes_incident(self):
        self.warm_baseline()
        baseline = server.baseline_status()
        self.assertTrue(baseline["ready"])
        self.assertGreaterEqual(baseline["sample_count"], 12)
        self.assertEqual(baseline["anomaly_threshold_ms"], 150.0)

        server.record_sample(self.status(self.base + 150, latency=220.0))
        server.record_sample(self.status(self.base + 160, latency=21.0))

        rows = server.incidents(10)
        self.assertEqual(len(rows), 1)
        incident = rows[0]
        self.assertEqual(incident["category"], "worker_latency")
        self.assertEqual(incident["status"], "closed")
        self.assertEqual(incident["duration_seconds"], 10)


if __name__ == "__main__":
    unittest.main()

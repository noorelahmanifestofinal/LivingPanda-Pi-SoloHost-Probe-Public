import json
import tempfile
import unittest
from pathlib import Path

import playbooks as playbook_learning
import server


class PlaybookLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        server.DATA_DIR = Path(self.tmp.name)
        server.DB_PATH = server.DATA_DIR / "test.sqlite3"
        self.old_minimum = server.MIN_PLAYBOOK_OBSERVATIONS
        server.MIN_PLAYBOOK_OBSERVATIONS = 3
        server.init_db()
        self.base = 1_790_000_000

    def tearDown(self):
        server.MIN_PLAYBOOK_OBSERVATIONS = self.old_minimum
        self.tmp.cleanup()

    def add_resolved(
        self,
        incident_id,
        outcome,
        action="Observe Docker recovery.",
        cause="docker_restart",

        category="host_network",
        confidence="high",
        evidence_count=2,
        recovery_seconds=10,
        stability_seconds=30,
    ):
        opened = self.base + incident_id * 100
        closed = opened + recovery_seconds
        verified = closed + stability_seconds
        evidence = {
            "evidence": [f"evidence-{i}" for i in range(evidence_count)],
            "limitations": [],
            "candidates": [],
        }
        components = {"incident_recovery_seconds": recovery_seconds}
        recovery_outcome = {"result": outcome}
        if outcome == "verified_stable":
            recovery_outcome["stable_window_seconds"] = stability_seconds
            recovery_outcome["no_related_failure"] = True
        else:
            recovery_outcome["seconds_after_recovery"] = stability_seconds
            recovery_outcome["recurrence_incident_id"] = incident_id + 1000

        with server.db() as conn:
            conn.execute(
                """

                INSERT INTO incidents(
                    id, opened_ts, closed_ts, category, status, severity,
                    title, summary, close_reason, details_json,
                    root_cause, root_cause_confidence, root_cause_score,
                    root_cause_evidence_json, recovery_action,
                    recovery_action_mode, recovery_status,
                    recovery_started_ts, recovery_verified_ts,
                    recovery_window_seconds, component_recovery_json,
                    recovery_outcome_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    incident_id, opened, closed, category, "closed", "warning",
                    "test", "test", "recovered", "{}",
                    cause, confidence, 6.0,
                    json.dumps(evidence), action, "observe", outcome,
                    closed, verified, 30, json.dumps(components),
                    json.dumps(recovery_outcome),
                ),
            )
            playbook_learning.refresh_playbooks(conn, verified + 1)

    def all_playbooks(self):
        with server.db() as conn:

            return playbook_learning.list_playbooks(
                conn,
                server.MIN_PLAYBOOK_OBSERVATIONS,
                limit=100,
            )

    def best(self, cause="docker_restart", category="host_network"):
        with server.db() as conn:
            return playbook_learning.best_playbook(
                conn,
                cause,
                category,
                server.MIN_PLAYBOOK_OBSERVATIONS,
            )

    def test_sparse_data_is_not_called_learned(self):
        self.add_resolved(1, "verified_stable")
        self.add_resolved(2, "verified_stable")

        items = self.all_playbooks()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["observation_count"], 2)
        self.assertFalse(items[0]["learned"])
        best = self.best()
        self.assertFalse(best["learned"])
        self.assertEqual(best["sample_count"], 2)
        self.assertIn("Need at least 3", best["reason"])

    def test_stable_outcomes_create_learned_playbook(self):

        self.add_resolved(1, "verified_stable", recovery_seconds=10)
        self.add_resolved(2, "verified_stable", recovery_seconds=20)
        self.add_resolved(3, "verified_stable", recovery_seconds=30)

        item = self.all_playbooks()[0]
        self.assertTrue(item["learned"])
        self.assertEqual(item["verified_stable_count"], 3)
        self.assertEqual(item["recurrence_count"], 0)
        self.assertEqual(item["success_rate"], 1.0)
        self.assertEqual(item["median_recovery_seconds"], 20.0)
        self.assertEqual(item["p95_recovery_seconds"], 29.0)
        self.assertEqual(item["evidence_quality"], "high")
        self.assertEqual(item["confidence_distribution"], {"high": 3})

        best = self.best()
        self.assertTrue(best["learned"])
        self.assertEqual(best["sample_count"], 3)
        self.assertIn("3/3 stable", best["ranking_reason"])
        self.assertIn("does not prove", best["causation_note"])

    def test_recurrence_is_counted_without_claiming_causation(self):
        self.add_resolved(1, "verified_stable", stability_seconds=30)
        self.add_resolved(2, "verified_stable", stability_seconds=30)
        self.add_resolved(3, "recurred", stability_seconds=5)

        item = self.all_playbooks()[0]
        self.assertTrue(item["learned"])
        self.assertEqual(item["verified_stable_count"], 2)
        self.assertEqual(item["recurrence_count"], 1)
        self.assertEqual(item["success_rate"], 0.6667)
        self.assertEqual(item["median_stability_seconds"], 30.0)
        self.assertIn("association", item["causation_note"])

    def test_conflicting_playbooks_are_ranked_explainably(self):
        action_a = "Observe Docker recovery."
        action_b = "Inspect Docker health after repeated failures."
        for incident_id in (1, 2, 3):
            self.add_resolved(
                incident_id,
                "verified_stable",
                action=action_a,
            )
        for incident_id, outcome in (
            (10, "verified_stable"),
            (11, "verified_stable"),
            (12, "verified_stable"),
            (13, "recurred"),
        ):
            self.add_resolved(
                incident_id,
                outcome,
                action=action_b,
            )

        best = self.best()
        self.assertEqual(best["recommendation_text"], action_a)
        self.assertEqual(best["success_rate"], 1.0)
        self.assertEqual(len(best["alternatives"]), 1)
        self.assertEqual(best["alternatives"][0]["success_rate"], 0.75)
        self.assertIn("Ranked first among 2", best["ranking_reason"])

    def test_missing_evidence_is_never_aggregated(self):
        self.add_resolved(1, "verified_stable", evidence_count=0)
        self.assertEqual(self.all_playbooks(), [])

    def test_refresh_is_idempotent(self):
        self.add_resolved(1, "verified_stable")
        with server.db() as conn:
            playbook_learning.refresh_playbooks(conn, self.base + 9999)
            playbook_learning.refresh_playbooks(conn, self.base + 10000)
            count = conn.execute(
                "SELECT COUNT(*) FROM playbook_observations"
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()

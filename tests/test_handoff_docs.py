import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class HandoffDocumentationTests(unittest.TestCase):
    def test_required_handoff_files_exist(self):
        required = [
            "AGENTS.md",
            "docs/project-state.yaml",
            "docs/ROADMAP.md",
            "docs/ARCHITECTURE.md",
            "docs/RUNBOOK.md",
            "docs/DATA_API.md",
            "docs/DECISIONS.md",
        ]
        for path in required:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_current_release_is_consistent(self):
        state = read("docs/project-state.yaml")
        server = read("server.py")
        package = read("package/docker-compose.yml")
        readme = read("README.md")
        agents = read("AGENTS.md")

        current = re.search(
            r"(?m)^\s{2}current:\s*[\"']?([^\"'\s]+)",
            state,
        )
        self.assertIsNotNone(current)
        current_version = current.group(1)

        candidate = re.search(
            r"(?m)^\s{2}candidate:\s*[\"']?([^\"'\s]+)",
            state,
        )
        source_version = (
            candidate.group(1) if candidate is not None else current_version
        )

        server_version = re.search(
            r'(?m)^VERSION\s*=\s*"([^"]+)"',
            server,
        )
        self.assertIsNotNone(server_version)
        self.assertEqual(server_version.group(1), source_version)

        self.assertIn(
            f"livingpanda-pi-solohost-probe:{source_version}",
            package,
        )
        self.assertIn(f"## v{source_version}", readme)
        self.assertIn(
            f"Current canonical release: **v{current_version}",
            agents,
        )
        self.assertIn(f"## v{current_version}", readme)
        if candidate is not None:
            self.assertIn(
                f"Active candidate: **v{source_version}",
                agents,
            )

    def test_runtime_modules_are_packaged(self):
        dockerfile = read("Dockerfile")
        for runtime_file in ("server.py", "playbooks.py", "index.html"):
            self.assertIn(runtime_file, dockerfile)

    def test_next_milestone_is_consistent(self):
        state = read("docs/project-state.yaml")
        agents = read("AGENTS.md")
        readme = read("README.md")

        next_version = re.search(
            r'(?ms)^next_milestone:\s*\n\s{2}version:\s*"([^"]+)"',
            state,
        )
        self.assertIsNotNone(next_version)
        version = next_version.group(1)

        self.assertIn(
            f"Next planned milestone: **v{version}",
            agents,
        )
        self.assertIn(
            f"next planned build is **v{version}",
            readme,
        )

    def test_long_range_roadmap_is_machine_and_human_readable(self):
        state = read("docs/project-state.yaml")
        roadmap = read("docs/ROADMAP.md")
        readme = read("README.md")
        agents = read("AGENTS.md")

        expected = {
            "0.8.0": "Generic Pi Node Operator Edition",
            "0.9.0": "Private Beta Readiness",
            "1.0.0": "LivingPanda Node Intelligence / Listed SoloHost Candidate",
        }
        for version, name in expected.items():
            self.assertIn(f'version: "{version}"', state)
            self.assertIn(name, state)
            self.assertIn(f"v{version}", roadmap)
            self.assertIn(name, roadmap)

        self.assertIn("LivingPanda Local AI Worker", state)
        self.assertIn("LivingPanda SME Edge", state)
        self.assertIn("LivingPanda Education Edge", state)
        self.assertIn("LivingPanda Privacy Shield", state)
        self.assertIn("LivingPanda Compute Worker", state)
        self.assertIn("docs/ROADMAP.md", readme)
        self.assertIn("docs/ROADMAP.md", agents)

    def test_safety_boundary_is_documented(self):
        agents = read("AGENTS.md").lower().replace("_", " ")
        state = read("docs/project-state.yaml").lower().replace("_", " ")

        for phrase in (
            "docker socket",
            "privileged",
            "commander control",
            "automatic repair execution",
        ):
            self.assertIn(phrase, agents)
            self.assertIn(phrase, state)


if __name__ == "__main__":
    unittest.main()

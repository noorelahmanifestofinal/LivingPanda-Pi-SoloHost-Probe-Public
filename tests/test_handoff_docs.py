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
        version = current.group(1)

        server_version = re.search(
            r'(?m)^VERSION\s*=\s*"([^"]+)"',
            server,
        )
        self.assertIsNotNone(server_version)
        self.assertEqual(server_version.group(1), version)

        self.assertIn(
            f"livingpanda-pi-solohost-probe:{version}",
            package,
        )
        self.assertIn(f"## v{version}", readme)
        self.assertIn(
            f"Current canonical release: **v{version}",
            agents,
        )

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

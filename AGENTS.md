# AGENTS.md â€” LivingPanda Pi Utility

This file is the first handoff for any AI or human continuing this repository.

## Start here

Read these files in this order before changing code:

1. `docs/project-state.yaml` — machine-readable current state, active candidate, and roadmap.
2. `docs/ROADMAP.md` — human-readable product roadmap from v0.7 through v1.0 and future SoloHost apps.
3. `docs/ARCHITECTURE.md` — components, data flow, persistence, trust boundaries.
4. `docs/RUNBOOK.md` — build, test, release, upgrade, rollback, verification.
5. `README.md` — current release behavior and user-facing summary.
6. Relevant tests under `tests/` before modifying behavior.

Do not infer production state from old chat history if the repository/runtime can be checked directly.

## Current release

- Current canonical release: **v0.6.0 â€” Recovery Intelligence**
- Active candidate: **v0.7.0 â€” Recovery Learning / Playbook Intelligence** on `feat/solohost-v0.7-playbook-intelligence`
- Next planned milestone: **v0.7.0 â€” Recovery Learning / Playbook Intelligence**
- Canonical branch: `main`
- Releases are tagged `vX.Y.Z`.
- The Pi SoloHost listing must remain **Unlisted â€” link only** unless the owner explicitly changes that decision.

## Non-negotiable safety boundaries

The Pi SoloHost app is **observation + intelligence**, not a privileged control plane.

Never add any of the following to the SoloHost app:

- Docker socket access.
- Privileged containers.
- Windows host filesystem mounts.
- Commander control or a general command shell.
- Automatic Docker/Windows/network/Pi Node restarts.
- Automatic repair execution of any kind.
- API keys, passwords, wallet secrets, recovery phrases, or private credentials.
- Broad host log exposure.

Keep:

- `read_only: true`
- `cap_drop: [ALL]`
- one app-owned Docker volume for SQLite persistence
- localhost-only published port
- read-only Evidence Bridge summaries only

Recommendations may describe a safe next action, but **v0.6 does not execute repairs**. v0.7 must preserve guarded approval before any future action integration.

## Data integrity rules

- SQLite database: `/data/operator-intelligence.sqlite3`
- Schema changes are forward-only migrations using `ensure_column` or compatible additive migrations.
- Never drop or recreate the production database during an upgrade.
- Never fabricate historical root-cause or recovery outcomes when evidence did not exist at the time.
- Existing v0.4/v0.5/v0.6 records must remain readable after future upgrades.
- Keep retention policy documented in `README.md` and `docs/project-state.yaml`.

## Testing contract

Before opening a PR:

```bash
python -m py_compile server.py
python -m py_compile evidence-bridge/app.py
python -m unittest discover -s tests -v
docker build -t livingpanda-pi-solohost-probe:ci .
docker build -t livingpanda-pi-evidence-bridge:ci evidence-bridge
```

For release changes also:

- test migration against a copy of a recent production SQLite database;
- run Pi SoloHost package validation;
- verify public GHCR anonymous pull after publishing;
- verify live container health, persistence, security settings, Evidence Bridge, and Pi Node sync after upgrade.

Never claim a deployment succeeded until live verification is complete.

## Git/release workflow

Use a feature branch:

```text
feat/solohost-vX.Y-<short-name>
```

Then:

```text
feature branch
â†’ local tests
â†’ migration test
â†’ Pi package validation
â†’ PR
â†’ CI
â†’ merge to main
â†’ tag vX.Y.Z
â†’ sync public distribution repo
â†’ public GHCR build
â†’ anonymous pull verification
â†’ update Pi listing
â†’ fresh production DB backup
â†’ in-place Compose upgrade
â†’ live verification
â†’ remove temporary test stack
â†’ clean branch/repo
```

Do not bypass this order for convenience.

## v0.7 direction

The next build is **Recovery Learning / Playbook Intelligence**.

Goal: learn from repeated incidents and recovery outcomes which recommendation is most reliable for this specific machine/environment, while remaining read-only and approval-gated.

v0.7 should add:

- playbook/outcome aggregation by root cause and incident category;
- success/recurrence rates for recommendations;
- median/P95 recovery times by playbook;
- evidence-quality/confidence tracking;
- recommendation ranking based on local history;
- explicit distinction between `learned recommendation` and `executed action`;
- no autonomous repair execution;
- explainable recommendation provenance;
- enough samples before promoting a recommendation as learned.

See `docs/project-state.yaml` for acceptance criteria.

## When uncertain

Prefer:

1. read-only inspection;
2. preserving current production state;
3. collecting more evidence;
4. explicit uncertainty;
5. guarded/manual recovery over autonomous intervention.

Do not guess about runtime state. Verify it.


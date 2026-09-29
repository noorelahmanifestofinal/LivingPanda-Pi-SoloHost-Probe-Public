# Runbook

## 1. Before changing code

Read:

1. `AGENTS.md`
2. `docs/project-state.yaml`
3. `docs/ARCHITECTURE.md`
4. this file
5. relevant tests

Then verify live state instead of assuming it from documentation.

Minimum read-only verification:

- current Git branch/tag;
- production container image/health;
- production SQLite table counts;
- Evidence Bridge health;
- Pi Node structured state.

Do not modify production during discovery.

## 2. Branching

Create:

```text
feat/solohost-vX.Y-<short-name>
```

Never develop directly on `main`.

## 3. Local validation

Run the full suite:

```bash
python -m py_compile server.py
python -m py_compile evidence-bridge/app.py
python -m unittest discover -s tests -v
docker build -t livingpanda-pi-solohost-probe:ci .
docker build -t livingpanda-pi-evidence-bridge:ci evidence-bridge
```

On Windows where `python` is not on PATH, use the installed Python launcher or a Python 3.12 container.

## 4. Real-data migration test

Before release:

1. make a SQLite backup using SQLite's backup API;
2. copy the backup into the **local test** volume only;
3. start the candidate image against that copied database;
4. verify:
   - old tables/rows remain;
   - new columns/tables exist;
   - historical records are not fabricated;
   - APIs still return old data;
   - candidate health is good.

Never perform migration experiments against the production volume.

## 5. Pi package validation

Validate:

- `package/docker-compose.yml`
- `package/config_options.yml`

The validator must return `ok: true`.

Do not weaken security settings to make validation pass unless Pi explicitly forbids a field and the alternative keeps the same practical boundary.

## 6. Pull request + CI

Commit the exact tested source.

Open a PR against `main`.

CI must pass before merge.

The CI contract must include all regression tests and both image builds.

## 7. Canonical release

After merge:

1. fast-forward local `main`;
2. tag `vX.Y.Z`;
3. push tag;
4. record merge commit in `docs/project-state.yaml`.

## 8. Public distribution repo

Copy only distributable source/package/test files to the public distribution repo.

Commit/tag the same version.

Wait for its GHCR workflow to complete.

Verify an **anonymous** pull of:

```text
ghcr.io/noorelahmanifestofinal/livingpanda-pi-solohost-probe:X.Y.Z
```

Record the digest in `docs/project-state.yaml`.

Do not deploy an image that cannot be pulled anonymously by Pi.

## 9. Production backup

Immediately before live upgrade, create a fresh SQLite backup from the running production container.

Store backups outside the app volume.

Record at minimum:

- samples count;
- events count;
- incidents count;
- evidence_samples count;
- timestamp.

Keep the previous installed Compose/config files as a rollback artifact.

## 10. Pi listing update

Update the existing Pi listing only.

Preserve:

- same app identity;
- same category/icon unless deliberately changed;
- **Unlisted — link only** visibility.

Paste the exact release package YAML.

Run Pi validation again.

Submit the update.

Verify the registry endpoint returns the expected image tag/package.

Never click/delete the published listing as part of a normal upgrade.

## 11. In-place production upgrade

Use the same Compose project and same named `operator-data` volume.

Preferred pattern:

```text
copy new package files into installed app folder
docker compose -p <existing-project> pull
docker compose -p <existing-project> up -d
```

Do not run `down -v` against production.

## 12. Post-upgrade verification

Verify separately in small read-only checks:

- `/health` version;
- container health;
- image tag + digest;
- DB counts >= pre-upgrade counts;
- expected new schema exists;
- old historical rows remain semantically intact;
- Evidence Bridge is healthy/fresh;
- security:
  - privileged=false
  - readonly rootfs=true
  - CapDrop=ALL
  - one app-owned volume
- no host mounts in SoloHost app;
- Pi Node reaches structured `Synced!`;
- Git canonical/public repos are clean.

Do not infer Pi health only from container running state.

## 13. Temporary test cleanup

After production verification:

- remove local test container;
- remove local test volume;
- keep production volume;
- delete merged feature branch;
- fetch/prune remotes;
- confirm clean repo state.

## 14. Rollback principles

If the candidate fails before live upgrade:

- leave production untouched;
- fix candidate on feature branch.

If the candidate fails immediately after live upgrade:

1. stop making additional changes;
2. capture current logs/status;
3. restore the previous installed Compose/config files;
4. start previous known-good image with the same production volume;
5. verify DB readability and app health;
6. use the fresh pre-upgrade SQLite backup only if the live DB itself was damaged.

Do not overwrite a healthy production DB just because an application process failed.

## 15. Known operational traps

### Docker Desktop stopped

Symptoms:

- Pi Node and SoloHost both disappear;
- Docker CLI fails.

Treat as runtime availability first, not an application bug.

### Desktop RPC timeout

A long tool call can fail while the Cloud Device itself remains connected.

Response:

- re-check device connectivity;
- split verification into smaller commands;
- inspect actual runtime state before retrying mutation.

### Pi Node catching up

After Docker/runtime restart, Pi may report `Catching up`.

If it is making progress, wait and recheck.

Do not force restart a progressing node.

Only call recovery complete when structured Pi state reports `Synced!`.

## 16. Documentation update rule

Every release PR that changes behavior must update:

- `README.md`
- `docs/project-state.yaml`
- `docs/ARCHITECTURE.md` if architecture/schema/API changes
- tests for the new behavior

At release completion, `docs/project-state.yaml` must describe the actual deployed release, not the intended candidate.

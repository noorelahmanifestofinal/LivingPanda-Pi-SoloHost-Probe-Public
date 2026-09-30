# LivingPanda Pi Utility

> **AI / contributor handoff:** start with [AGENTS.md](AGENTS.md), then [docs/project-state.yaml](docs/project-state.yaml), [docs/ROADMAP.md](docs/ROADMAP.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/RUNBOOK.md](docs/RUNBOOK.md). The project-state file is the canonical machine-readable handoff; the roadmap carries the product direction through v1.0 and future SoloHost apps.

## v0.7.0 — Recovery Learning / Playbook Intelligence (candidate)

The deployed Pi SoloHost app remains **v0.6.0** while this feature branch is validated. The v0.7 candidate learns from resolved, evidence-backed v0.6+ recovery outcomes without executing repairs.

It adds:

- persistent playbook observations keyed to resolved incident IDs
- minimum sample count before a recommendation is called learned
- verified-stable and recurrence counts with local success rate
- median and p95 recovery duration
- observed post-recovery stability duration
- confidence distribution and evidence-quality labeling
- explainable ranking when more than one learned recommendation exists
- explicit sample counts and a correlation-not-causation warning
- `GET /api/playbooks` plus a playbook-learning summary in `/api/status`
- no automatic repair execution

Only incidents with resolved `verified_stable` or `recurred` outcomes and actual root-cause evidence are aggregated. Sparse history remains visible as insufficient evidence rather than being promoted to a learned playbook.

The candidate package points to `ghcr.io/noorelahmanifestofinal/livingpanda-pi-solohost-probe:0.7.0`, but production must not be upgraded until migration tests, Pi package validation, CI, image publishing/anonymous pull verification, and live Pi Node sync checks pass.

## v0.6.0 — Recovery Intelligence

v0.6 extends Root-Cause Intelligence into a safe recovery lifecycle:

**detect → explain → recommend → observe → verify**

### What it adds

- cause-specific safe recovery recommendations after an incident closes
- a 30-minute stability watch before declaring recovery verified
- component recovery timing for Pi reachability, LivingPanda worker, local web, and overall incident recovery
- recurrence detection for related incidents
- escalation from `observe` to `inspect` when a failure returns
- outcome records showing whether recovery remained stable or recurred
- no automatic repair execution

### Example

```text
Likely cause: Docker Desktop / Linux engine restart
Confidence: high

Recommended action:
No intervention required — services recovered automatically.

Recovery:
Pi Node: 74 seconds
LivingPanda worker: 41 seconds

Outcome:
No related failure returned within 30 minutes.
Status: verified_stable
```

If a related failure returns inside the stability window:

```text
Status: recurred
Recommended action:
Repeated failure detected — inspect Docker Desktop / Linux engine health
and use guarded recovery only if the engine is still unhealthy.
```

### Safety model

The SoloHost app remains read-only:

- no Docker socket
- no privileged mode
- read-only root filesystem
- all Linux capabilities dropped
- no Windows host mounts
- no Commander control
- no network or service restart commands
- recommendations are guidance only

The optional PC Intelligence Evidence Bridge remains read-only and only exposes whitelisted summaries from PC Intelligence and Cloud Device reliability logs.

### Persistence

- health samples: 24 hours
- raw events: 7 days
- root-cause evidence samples: 7 days
- incidents/recovery outcomes: 30 days
- SQLite remains in Pi's app-owned Docker volume

### Continuation map

The next planned build is **v0.7.0 — Recovery Learning / Playbook Intelligence**. It will learn from repeated v0.6 recovery outcomes, but it must remain recommendation-only and approval-gated. Acceptance criteria and proposed data fields are maintained in `docs/project-state.yaml`.

Additional technical references:

- [Product roadmap](docs/ROADMAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Release / rollback runbook](docs/RUNBOOK.md)
- [Data + API contract](docs/DATA_API.md)
- [Key architectural decisions](docs/DECISIONS.md)

Canonical source: `LivingPanda-Online/LivingPanda-Pi-SoloHost-Probe`

Public image: `ghcr.io/noorelahmanifestofinal/livingpanda-pi-solohost-probe:0.6.0`

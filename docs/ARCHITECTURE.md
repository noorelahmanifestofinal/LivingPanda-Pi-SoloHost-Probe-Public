# Architecture

## Purpose

LivingPanda Pi Utility is a local Pi SoloHost observability/intelligence app. It is intentionally separated from LivingPanda Commander and from privileged host control.

Its job is to answer:

1. What is healthy or degraded?
2. Which raw changes belong to one incident?
3. What is the most likely cause, with evidence and limitations?
4. What is the safest recommended next action?
5. Did recovery happen, how long did each component take, and did the problem recur?

It does **not** execute recovery commands.

## High-level data flow

```text
Pi Node local ports ───────────────┐
LivingPanda worker health ────────┤
LivingPanda local web health ─────┤
SoloHost container telemetry ─────┤
                                  ▼
                         Pi SoloHost app
                         server.py
                              │
                              ├─ samples
                              ├─ events
                              ├─ incidents
                              ├─ root-cause fields
                              └─ recovery fields
                                  │
                                  ▼
                       SQLite app-owned volume
                       /data/operator-intelligence.sqlite3

PC Intelligence snapshots ────────┐
Cloud Device reliability logs ────┤
host/runtime/internet signals ────┤
                                  ▼
                    Read-only Evidence Bridge
                       localhost:8001
                                  │
                                  ▼
                    summarized evidence only
                                  │
                                  ▼
                         Pi SoloHost app
```

## Components

### 1. SoloHost app

Files:

- `server.py` — monitoring, storage, incident/root-cause/recovery logic, HTTP API.
- `index.html` — mobile-friendly local dashboard.
- `package/docker-compose.yml` — Pi SoloHost package definition.
- `package/config_options.yml` — Pi package metadata/configuration.
- `docker-compose.local.yml` — isolated local test stack.

Security boundary:

- localhost-only host port;
- read-only root filesystem;
- all Linux capabilities dropped;
- no Docker socket;
- no host filesystem mounts;
- no Commander endpoint/control;
- one named app-owned volume for SQLite.

### 2. Evidence Bridge

Files:

- `evidence-bridge/app.py`
- `evidence-bridge/compose.host.yml`

Purpose:

- read selected PC Intelligence summaries;
- read selected Cloud Device reliability counters;
- probe host/runtime/internet state;
- emit a normalized summary to the SoloHost app.

The bridge is deliberately separate from the Pi app because the Pi app must not gain arbitrary host filesystem visibility.

The bridge must never expose:

- raw credentials or tokens;
- arbitrary file reads;
- arbitrary process/control APIs;
- Docker socket access;
- Commander control.

## Core pipeline

### Sampling

Every 10 seconds the app records:

- Pi ports reachable;
- LivingPanda worker availability/latency;
- local web availability/latency;
- container memory;
- Evidence Bridge summary where available.

### Event detection

State transitions produce raw events such as:

- Pi became unreachable/recovered;
- worker became unreachable/recovered;
- worker latency became slow/recovered.

### Incident correlation

Raw events are grouped into one incident where appropriate.

Examples:

- Pi-only degradation → `pi_connectivity`
- worker-only failure → `livingpanda_worker`
- worker + local web → `livingpanda_stack`
- Pi + LivingPanda failures close together → `host_network`
- latency anomaly → `worker_latency`

### Root-cause inference

When an incident closes, v0.5+ scores candidate causes using evidence.

Current cause vocabulary:

- `host_restart`
- `docker_restart`
- `network_interruption`
- `livingpanda_worker`
- `pi_node`
- `local_stack`
- `unknown`

The result contains:

- likely cause;
- confidence;
- score;
- evidence;
- limitations;
- alternative candidates.

This is evidence-based inference, not a certainty claim.

### Recovery Intelligence

v0.6 adds:

- recommendation text;
- recommendation mode (`observe` or `inspect`);
- component recovery timing;
- recovery status;
- 30-minute recurrence window;
- verified outcome.

Lifecycle:

```text
incident closes
   ↓
root cause inferred
   ↓
safe recommendation generated
   ↓
pending_stability
   ├─ no related incident for 30m → verified_stable
   └─ related incident returns     → recurred → mode=inspect
```

A condition that changes into another incident without clean recovery is marked `superseded`.

### Recovery Learning / Playbook Intelligence

v0.7 adds a separate learning layer over resolved recovery outcomes:

```text
resolved v0.6+ incident
   ↓
requires root-cause evidence + verified outcome
   ↓
playbook_observations (one compact row per incident)
   ↓
playbooks aggregate
   ↓
minimum sample gate
   ↓
learned recommendation + ranking explanation
```

Only `verified_stable` and `recurred` outcomes are eligible. Pending, superseded, evidence-free, and pre-v0.6 historical incidents are not promoted into learning data.

Playbook observations persist independently of the 30-day incident-retention window so learned aggregate history is not lost when verbose incident records expire. The learned layer stores only compact outcome metadata, not raw host logs.

A learned recommendation can affect which existing read-only recommendation text ranks first for a matching cause/category. It never invokes that recommendation. Every learned result includes its sample count and an explicit statement that observed association does not prove causation.

## Persistence model

Database:

```text
/data/operator-intelligence.sqlite3
```

Tables:

- `samples`
- `events`
- `incidents`
- `evidence_samples`
- `playbook_observations` — compact, idempotent resolved-outcome records
- `playbooks` — aggregated local recovery statistics and ranking inputs
- `meta`

Schema changes must be additive/forward-only. Use `ensure_column` or a compatible additive migration.

Never wipe/recreate the production DB during a release.

## Incident fields added over time

Base/v0.4 fields include timestamps, category, severity, status, duration data, peak latency, and details.

v0.5 adds:

- `root_cause`
- `root_cause_confidence`
- `root_cause_score`
- `root_cause_evidence_json`

v0.6 adds:

- `recovery_action`
- `recovery_action_mode`
- `recovery_status`
- `recovery_started_ts`
- `recovery_verified_ts`
- `recovery_window_seconds`
- `component_recovery_json`
- `recovery_outcome_json`

## HTTP API

Current endpoints:

- `GET /` — dashboard
- `GET /health` — app health/version
- `GET /api/status` — current health + intelligence summary
- `GET /api/history?minutes=N` — health samples/summary
- `GET /api/events?limit=N` — recent raw events
- `GET /api/incidents?limit=N&status=open|closed` — incident/root-cause/recovery records
- `GET /api/playbooks?limit=N&cause=X&category=Y&learned=true` — local learned/recovery playbooks and sample counts
- `GET /api/baseline` — adaptive worker latency baseline
- `GET /api/evidence` — latest normalized Evidence Bridge sample

Keep APIs backward-compatible where practical.

## Testing layers

1. Syntax/compile.
2. Incident regression tests.
3. Root-cause regression tests.
4. Recovery regression tests.
5. Docker image build.
6. Real-data migration against a copy of production SQLite.
7. Pi package validation.
8. Public image anonymous pull.
9. Live production verification.

## v0.7 safety boundary

Recovery Learning / Playbook Intelligence learns from historical outcomes, but it does not turn the SoloHost app into a repair executor.

The learned system answers:

- which recommendation has the best observed local success rate?
- how many samples support that conclusion?
- how long does recovery normally take?
- how often does the issue recur?
- what evidence quality/confidence supported those cases?
- why one learned recommendation ranks above alternatives?

Any future action execution belongs behind a separate guarded approval boundary, not inside this read-only Pi app.

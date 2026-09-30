# Data and API Contract

## Database

Path inside the SoloHost container:

```text
/data/operator-intelligence.sqlite3
```

The database lives on the app-owned `operator-data` Docker volume.

## Tables

### samples

Short-term health observations.

Key concepts:

- timestamp;
- number of Pi ports reachable;
- individual Pi port states;
- worker availability/latency;
- local web availability/latency;
- container memory.

Retention: 24 hours.

### events

Raw state-transition events.

Examples:

- Pi unreachable/recovered;
- worker unreachable/recovered;
- worker slow/recovered;
- incident opened/correlated/recovered;
- root-cause assessment;
- recovery recommendation;
- recovery verified;
- recovery recurred.

Retention: 7 days.

### evidence_samples

Normalized root-cause evidence.

Includes only whitelisted summaries such as:

- runtime uptime;
- live internet probe;
- PC Intelligence freshness;
- PC internet state/connect latency;
- PC boot timestamp/uptime;
- Wi-Fi state/signal;
- Cloud DEV 1006/reconnect/RPC/stable counters.

Retention: 7 days.

### incidents

Canonical lifecycle record.

An incident can contain:

- detection/correlation data;
- root-cause assessment;
- recovery recommendation;
- recovery timing;
- stability-watch outcome.

Retention: 30 days after close.

### playbook_observations

Compact, idempotent recovery-learning records. One row is stored per eligible resolved incident. Only evidence-backed `verified_stable` or `recurred` outcomes are eligible.

Key fields include incident ID, playbook key, cause/category, recommendation text, outcome, recovery/stability duration, confidence, evidence count, and observation timestamp.

These compact rows persist beyond incident retention so learned aggregate history survives normal cleanup without retaining verbose incident evidence indefinitely.

### playbooks

Materialized local recovery aggregates keyed by cause, incident category, and recommendation text. Fields include observation count, stable/recurrence counts, success rate, median/P95 recovery duration, median observed stability duration, confidence distribution, evidence quality, and last update timestamp.

A playbook is marked learned only when its observation count meets the configured minimum sample threshold. Learned status is descriptive evidence from this local environment, not proof that the recommendation caused recovery.

### meta

Migration/backfill markers and other small schema metadata.

## Recovery statuses

### pending_stability

Clean recovery observed. Waiting through the stability window.

### verified_stable

No related incident returned before the stability deadline.

### recurred

A related incident returned inside the stability window.

### superseded

The incident condition changed into a replacement incident before clean recovery.

## Recommendation modes

### observe

No current operator intervention required. Continue evidence collection.

### inspect

A recurrence or other evidence warrants targeted inspection. This still does not execute an action.

## Historical integrity

A field being present in the schema does not mean old rows should be populated.

Examples:

- v0.4 incidents that predate v0.5 evidence remain without root-cause assessment.
- pre-v0.6 incidents remain without recovery outcome.

This is intentional and must remain true.

## HTTP API stability

### GET /health

Stable minimal health/version endpoint.

### GET /api/status

Current state and intelligence summary.

### GET /api/history

Short-term samples + aggregate availability/latency.

### GET /api/events

Recent raw events.

### GET /api/incidents

Canonical incident records including root-cause and recovery data when available. v0.7 recovery records may include `learning` provenance showing whether the chosen recommendation came from a learned local playbook, the supporting sample count, observed success rate, evidence quality, alternatives, and ranking reason.

### GET /api/playbooks

Local recovery-learning aggregates. Optional query parameters: `limit`, `cause`, `category`, and `learned=true`. The response always includes the minimum observation threshold, a correlation-not-causation note, and `automatic_actions: "none"`.

### GET /api/baseline

Adaptive worker latency baseline.

### GET /api/evidence

Latest normalized evidence sample.

When evolving APIs, prefer additive fields over breaking renames/removals.

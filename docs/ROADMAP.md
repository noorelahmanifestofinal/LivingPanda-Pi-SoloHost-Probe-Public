# LivingPanda Pi / SoloHost Roadmap

This roadmap describes the product direction after the current production release. It is intentionally separate from the release runbook: the runbook explains *how* to ship safely; this file explains *what to build next and why*.

## Current position

Production today: **v0.6.0 — Recovery Intelligence**

Active candidate: **v0.7.0 — Recovery Learning / Playbook Intelligence**

The Pi listing remains **Unlisted — link only** during development and early validation.

---

## v0.7.0 — Recovery Learning / Playbook Intelligence

### Goal

Learn from repeated, evidence-backed recovery outcomes on this machine and rank recommendations by observed local reliability.

### Capabilities

- persistent playbook observations beyond incident retention;
- success and recurrence rates;
- median and p95 recovery times;
- median stability duration;
- root-cause confidence distribution;
- evidence-quality classification;
- minimum-sample threshold before calling a playbook learned;
- explainable ranking against alternatives;
- sample count shown beside learned recommendations;
- explicit correlation-not-causation warning;
- no automatic repair execution.

### Release gate

- preserve all v0.6 production rows;
- aggregate only resolved, evidence-backed v0.6+ outcomes;
- sparse data must remain unlearned;
- conflicting playbooks must rank explainably;
- full regression suite passes;
- migration against production SQLite copy passes;
- both Docker images build;
- Pi package validator returns ok=true;
- CI passes;
- public image anonymous pull works;
- production upgrade preserves the existing operator-data volume;
- Pi Node returns structured Synced! after upgrade.

---

## v0.8.0 — Generic Pi Node Operator Edition

### Goal

Remove assumptions that only work on this workstation and make the same SoloHost package useful to another Pi Node operator with a normal one-install experience.

### Product changes

- first-run capability discovery instead of machine-specific assumptions;
- graceful detection of optional LivingPanda services;
- Pi Node / Docker / network diagnostics usable without Commander;
- generic host/runtime evidence interface;
- privacy and data-retention controls;
- clear local-data disclosure;
- diagnostics export for support;
- explicit reset/delete-local-data workflow;
- compatibility checks for different Windows/Docker/Pi Desktop setups;
- useful UI when LivingPanda-specific workers are absent;
- no dependency on private paths or this PC's hostname.

### Acceptance criteria

- clean install works on a second machine without editing source;
- missing optional services do not create false incidents;
- all machine-specific paths/config are optional or discovered;
- no private LivingPanda identifiers are required for core Pi diagnostics;
- privacy disclosure explains exactly what remains local;
- export contains diagnostics, not secrets;
- reset removes app-owned telemetry safely;
- security boundary remains read-only.

---

## v0.9.0 — Private Beta Readiness

### Goal

Run LivingPanda Node Intelligence with a small group of real Pi Node operators before a public listing.

### Target

**5–20 opt-in Pi Node operators**

### Beta capabilities

- stable first-run setup;
- version and compatibility information;
- opt-in, privacy-safe support bundle/export;
- clear feedback path;
- compatibility matrix across tested Pi Desktop/Docker/Windows setups;
- update/rollback instructions;
- operator-facing troubleshooting documentation;
- no required centralized telemetry;
- any shared diagnostics are explicit opt-in.

### Success signals

- users can install without developer intervention;
- app remains running over multi-day periods;
- false incident/root-cause rates are acceptably low;
- users understand recommendations;
- no private data is exposed by default;
- upgrades preserve history reliably;
- uninstall/reset behavior is predictable.

The app should remain **Unlisted** during this phase unless the owner explicitly changes the rollout plan.

---

## v1.0.0 — LivingPanda Node Intelligence / Listed SoloHost Candidate

### Goal

Turn the internal operator utility into a broadly usable Pi SoloHost product.

### Public product scope

- Pi Node health;
- Docker/runtime health;
- network health;
- local uptime/history;
- incident correlation;
- root-cause evidence;
- safe recovery guidance;
- learned local playbooks where enough evidence exists;
- privacy-safe local-first operation;
- mobile-friendly dashboard;
- exportable diagnostics.

### Listing gate

Do not make the app publicly discoverable only because the code works.

Before changing from Unlisted to Listed, require:

- successful private beta;
- generic-machine compatibility proven;
- privacy/data disclosures complete;
- support/runbook documentation complete;
- stable upgrade/rollback process;
- no machine-specific secrets/paths;
- public repository/release artifacts match deployed package;
- owner explicitly approves listing.

---

## Beyond v1.0 — LivingPanda SoloHost product family

The long-term opportunity is not one giant container. Prefer specialized apps with narrow permissions and clear purposes.

### LivingPanda Node Intelligence

Pi Node, Docker, network, incident, root-cause, recovery and learning intelligence.

### LivingPanda Local AI Worker

Local model/runtime gateway for approved local AI workloads. Keep model/runtime routing separate from Pi operator diagnostics.

### LivingPanda SME Edge

Local-first SME services such as customer, inventory, workflow or automation capabilities where self-hosting is useful.

### LivingPanda Education Edge

Local family/school learning services designed for low-bandwidth environments and phone-first access.

### LivingPanda Privacy Shield

Local privacy-risk inspection and privacy-preserving tooling with strict data boundaries.

### LivingPanda Compute Worker

Future optional compute-worker integration if Pi distributed-compute capabilities become sufficiently stable and documented. Do not make current products depend on this future capability.

---

## Architectural principle

SoloHost should be treated as a **distribution + local-compute runtime**, not as a reason to couple LivingPanda to one blockchain, one AI model, or one privileged control plane.

LivingPanda should remain:

- model-agnostic;
- local-first where practical;
- privacy-aware;
- portable outside Pi;
- modular;
- safe by default;
- useful even when optional Pi/LivingPanda integrations are unavailable.

---

## Version progression

```text
v0.1  SoloHost compatibility
  ↓
v0.2  Health visibility
  ↓
v0.3  Persistent telemetry
  ↓
v0.4  Incident Intelligence
  ↓
v0.5  Root-Cause Intelligence
  ↓
v0.6  Recovery Intelligence
  ↓
v0.7  Recovery Learning / Playbook Intelligence
  ↓
v0.8  Generic Pi Node Operator Edition
  ↓
v0.9  Private Beta Readiness
  ↓
v1.0  LivingPanda Node Intelligence / Listed SoloHost Candidate
  ↓
future specialized LivingPanda SoloHost apps
```

## Rule for future AI

The nearest incomplete milestone wins.

Do not skip directly to a later roadmap item while an earlier milestone is still an unverified candidate. Finish its tests, migration, release, deployment, and handoff update first.

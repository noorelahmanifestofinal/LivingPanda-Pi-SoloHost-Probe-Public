# LivingPanda Pi Utility

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

Canonical source: `LivingPanda-Online/LivingPanda-Pi-SoloHost-Probe`

Public image: `ghcr.io/noorelahmanifestofinal/livingpanda-pi-solohost-probe:0.6.0`

# LivingPanda Pi Utility

## v0.5.0 - Root-Cause Intelligence

v0.5 extends the incident engine with evidence-based likely-cause assessments.

### What it correlates

- Pi Node port reachability
- LivingPanda worker and local-web health
- worker latency baseline/anomalies
- Windows host boot timestamp changes
- Docker/Linux runtime uptime resets
- live internet reachability
- PC Intelligence network snapshots and state changes
- Cloud DEV reconnect, WebSocket 1006, and Desktop RPC timeout counters

### Example

```text
Multiple local services degraded for 39 seconds -> recovered automatically

Likely cause: Docker Desktop / Linux engine restart
Confidence: high

Evidence:
- Docker/Linux runtime uptime reset
- multiple local services degraded together
- live internet remained reachable
- services recovered after the runtime returned
```

The result is a **likely cause**, not a claim that correlation mathematically proves causation. v0.5 stores supporting evidence and limitations with each closed incident.

## Optional PC Intelligence Evidence Bridge

The `evidence-bridge/` companion service runs outside Pi SoloHost on localhost port `8001`.

It has only two read-only host mounts:

- `${LIVINGPANDA_DATA_DIR:-D:/LivingPanda-Data}/PC-Intelligence`
- `${LOCALAPPDATA}/LivingPanda/CloudDevice/logs`

It returns only whitelisted summaries and counters. It does **not** return raw Cloud Device log content, expose Docker, or provide Commander access.

Run locally:

```powershell
cd evidence-bridge
docker compose -f compose.host.yml up -d --build
```

If the bridge is unavailable, the Pi utility continues working and marks the assessment as limited to local signals.

## Persistence

- health samples: 24 hours
- raw events: 7 days
- root-cause evidence samples: 7 days
- incidents: 30 days
- SQLite remains in Pi's app-owned Docker volume

## Security boundary

The Pi SoloHost container remains:

- unprivileged
- read-only root filesystem
- `cap_drop: ALL`
- no Docker socket
- no Windows host mounts
- no Commander control
- no API keys or private files

The optional Evidence Bridge is also unprivileged/read-only and exposes only localhost port `8001`.

Canonical source: `LivingPanda-Online/LivingPanda-Pi-SoloHost-Probe`

Public SoloHost image: `ghcr.io/noorelahmanifestofinal/livingpanda-pi-solohost-probe:0.5.0`

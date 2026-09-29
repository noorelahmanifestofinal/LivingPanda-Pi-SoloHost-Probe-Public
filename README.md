# LivingPanda Pi Utility

## v0.3.0 — Local Operator Intelligence

Read-only local monitoring for Pi SoloHost:

- Pi Node port reachability history for 31401–31403
- LivingPanda worker availability and latency history
- state-change events for unreachable / partial / recovered states
- worker latency slow/recovered events
- 60-minute availability and latency summary
- 24-hour sample retention
- 7-day event retention
- SQLite telemetry stored only in an app-owned Docker volume

Security boundary: no Docker socket, no Windows host mounts, no privileged mode, all Linux capabilities dropped, read-only container root filesystem, no Commander control, no API keys or private files.

Pi SoloHost currently disallows Docker Compose `security_opt`, so v0.3 does not request `no-new-privileges` through Compose.

Canonical source: `LivingPanda-Online/LivingPanda-Pi-SoloHost-Probe`

Public image: `ghcr.io/noorelahmanifestofinal/livingpanda-pi-solohost-probe:0.3.0`

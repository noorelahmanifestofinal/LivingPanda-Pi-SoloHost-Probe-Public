# LivingPanda Pi Utility

## v0.4.0 — Incident Intelligence

v0.4 turns raw health events into operator-readable incidents.

- correlates related Pi / worker / local-web failures inside a 30-second window
- calculates exact incident duration
- closes incidents automatically when monitored signals recover
- classifies Pi connectivity vs LivingPanda worker vs local stack vs correlated host/network incidents
- learns a robust six-hour worker-latency baseline using median, p95, and MAD
- records latency anomalies against the adaptive baseline
- preserves v0.3 samples and events through a forward-only SQLite schema migration
- keeps 24-hour samples, 7-day raw events, and 30-day incidents

Example: `Pi connectivity degraded for 43 seconds → recovered automatically`.

Security boundary: no Docker socket, no Windows host mounts, no privileged mode, all Linux capabilities dropped, read-only root filesystem, app-owned Docker volume only, no Commander control, no API keys or private files.

Canonical source: `LivingPanda-Online/LivingPanda-Pi-SoloHost-Probe`

Public image: `ghcr.io/noorelahmanifestofinal/livingpanda-pi-solohost-probe:0.4.0`

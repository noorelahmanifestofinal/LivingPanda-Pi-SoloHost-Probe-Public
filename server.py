import json
import math
import os
import socket
import sqlite3
import statistics
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.5.0"
HOST = "0.0.0.0"
PORT = 8080
HOST_GATEWAY = "host.docker.internal"
EVIDENCE_BRIDGE_URL = os.environ.get(
    "EVIDENCE_BRIDGE_URL",
    f"http://{HOST_GATEWAY}:8001/evidence",
)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "operator-intelligence.sqlite3"
SAMPLE_INTERVAL_SECONDS = 10
SAMPLE_RETENTION_SECONDS = 24 * 60 * 60
EVENT_RETENTION_SECONDS = 7 * 24 * 60 * 60
INCIDENT_RETENTION_SECONDS = 30 * 24 * 60 * 60
EVIDENCE_RETENTION_SECONDS = 7 * 24 * 60 * 60
BASELINE_WINDOW_SECONDS = 6 * 60 * 60
MIN_BASELINE_SAMPLES = 12
STATIC_LATENCY_FALLBACK_MS = 250.0
INCIDENT_CORRELATION_SECONDS = 30
ROOT_CAUSE_CONTEXT_SECONDS = 30
PC_INTEL_FRESH_SECONDS = 5 * 60
RUNTIME_RESET_MARGIN_SECONDS = 60
BASE_DIR = Path(__file__).resolve().parent
INDEX = (BASE_DIR / "index.html").read_text(encoding="utf-8")

STATE_LOCK = threading.Lock()
LAST_STATUS = None
LAST_EVIDENCE = None

CATEGORY_LABELS = {
    "pi_connectivity": "Pi connectivity degraded",
    "livingpanda_worker": "LivingPanda worker unavailable",
    "livingpanda_stack": "LivingPanda local stack degraded",
    "livingpanda_web": "LivingPanda local web unavailable",
    "worker_latency": "LivingPanda worker latency degraded",
    "host_network": "Multiple local services degraded",
}

CAUSE_LABELS = {
    "host_restart": "Windows / host restart",
    "docker_restart": "Docker Desktop / Linux engine restart",
    "network_interruption": "Local internet or Wi-Fi interruption",
    "livingpanda_worker": "LivingPanda worker/service issue",
    "pi_node": "Pi Node service/connectivity issue",
    "local_stack": "Local Docker stack disruption",
    "unknown": "Insufficient evidence",
}


def utc_iso(ts=None):
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_column(conn, table, name, definition):
    if name not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER PRIMARY KEY,
                pi_reachable_ports INTEGER NOT NULL,
                pi_31401 INTEGER NOT NULL,
                pi_31402 INTEGER NOT NULL,
                pi_31403 INTEGER NOT NULL,
                worker_ok INTEGER NOT NULL,
                worker_latency_ms REAL,
                local_web_ok INTEGER NOT NULL,
                local_web_latency_ms REAL,
                memory_mb REAL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_ts INTEGER NOT NULL,
                closed_ts INTEGER,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                close_reason TEXT,
                peak_worker_latency_ms REAL,
                min_pi_ports INTEGER,
                baseline_ms REAL,
                details_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS evidence_samples (
                ts INTEGER PRIMARY KEY,
                bridge_ok INTEGER NOT NULL DEFAULT 0,
                runtime_uptime_seconds REAL,
                bridge_runtime_uptime_seconds REAL,
                internet_ok INTEGER,
                internet_latency_ms REAL,
                pc_intel_freshness_seconds REAL,
                pc_internet_ok INTEGER,
                pc_connect_latency_ms REAL,
                pc_boot_at TEXT,
                pc_uptime_seconds REAL,
                pc_wifi_state TEXT,
                pc_wifi_signal REAL,
                cloud_log_file TEXT,
                cloud_log_size_bytes INTEGER,
                cloud_ws_1006_count INTEGER,
                cloud_reconnect_count INTEGER,
                cloud_rpc_timeout_count INTEGER,
                cloud_stable_count INTEGER,
                cloud_mcp_connected_count INTEGER,
                evidence_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS idx_incidents_opened_ts ON incidents(opened_ts);
            CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
            CREATE INDEX IF NOT EXISTS idx_evidence_ts ON evidence_samples(ts);
            """
        )
        ensure_column(conn, "incidents", "root_cause", "TEXT")
        ensure_column(conn, "incidents", "root_cause_confidence", "TEXT")
        ensure_column(conn, "incidents", "root_cause_score", "REAL")
        ensure_column(conn, "incidents", "root_cause_evidence_json", "TEXT")
        ensure_column(conn, "evidence_samples", "pc_boot_at", "TEXT")
        ensure_column(conn, "evidence_samples", "pc_uptime_seconds", "REAL")
        ensure_column(conn, "evidence_samples", "pc_wifi_state", "TEXT")
        ensure_column(conn, "evidence_samples", "pc_wifi_signal", "REAL")
        backfill_incidents_from_events(conn)


def tcp_probe(host: str, port: int, timeout: float = 1.0):
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "reachable": True,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            }
    except Exception as exc:
        return {"reachable": False, "error": exc.__class__.__name__}


def http_probe(url: str, timeout: float = 2.0):
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", "replace")
            payload = None
            try:
                payload = json.loads(body)
            except Exception:
                payload = None
            return {
                "reachable": True,
                "status_code": response.status,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "payload": payload,
            }
    except Exception as exc:
        return {"reachable": False, "error": exc.__class__.__name__}


def read_number(path: str):
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
        if value == "max":
            return None
        return int(value)
    except Exception:
        return None


def container_status():
    memory_current = read_number("/sys/fs/cgroup/memory.current")
    memory_max = read_number("/sys/fs/cgroup/memory.max")
    try:
        uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        uptime_seconds = None
    return {
        "running_in_docker": Path("/.dockerenv").exists(),
        "hostname": socket.gethostname(),
        "uptime_seconds": round(uptime_seconds, 1) if uptime_seconds is not None else None,
        "memory_current_mb": round(memory_current / 1024 / 1024, 1)
        if memory_current is not None
        else None,
        "memory_limit_mb": round(memory_max / 1024 / 1024, 1)
        if memory_max is not None
        else None,
    }


def collect_raw_status():
    pi_ports = {
        str(port): tcp_probe(HOST_GATEWAY, port)
        for port in (31401, 31402, 31403)
    }
    reachable = sum(1 for item in pi_ports.values() if item["reachable"])
    data_worker = http_probe(f"http://{HOST_GATEWAY}:8000/health")
    local_web = http_probe(f"http://{HOST_GATEWAY}:8080/")
    return {
        "ok": True,
        "version": VERSION,
        "checked_at": utc_iso(),
        "checked_ts": int(time.time()),
        "pi_node": {
            "host": HOST_GATEWAY,
            "reachable_ports": reachable,
            "total_ports": 3,
            "status": "reachable"
            if reachable == 3
            else ("partial" if reachable else "not_detected"),
            "ports": pi_ports,
            "note": "Read-only TCP reachability only. This does not claim blockchain sync state.",
        },
        "solo_host": container_status(),
        "livingpanda": {
            "data_worker": data_worker,
            "local_web": local_web,
            "note": "Only allow-listed read-only health endpoints are probed. Commander is not connected.",
        },
        "security": {
            "docker_socket": False,
            "host_mounts": False,
            "commander_control": False,
            "privileged": False,
            "persistent_storage": "app-owned Docker volume only",
            "evidence_bridge": "optional read-only localhost companion",
        },
    }


def empty_evidence(status=None):
    runtime = None
    if status:
        runtime = status.get("solo_host", {}).get("uptime_seconds")
    return {
        "bridge_ok": False,
        "runtime_uptime_seconds": runtime,
        "bridge_runtime_uptime_seconds": None,
        "internet_ok": None,
        "internet_latency_ms": None,
        "pc_intel_freshness_seconds": None,
        "pc_internet_ok": None,
        "pc_connect_latency_ms": None,
        "pc_boot_at": None,
        "pc_uptime_seconds": None,
        "pc_wifi_state": None,
        "pc_wifi_signal": None,
        "cloud_log_file": None,
        "cloud_log_size_bytes": None,
        "cloud_ws_1006_count": None,
        "cloud_reconnect_count": None,
        "cloud_rpc_timeout_count": None,
        "cloud_stable_count": None,
        "cloud_mcp_connected_count": None,
        "payload": {},
    }


def normalize_evidence(payload, status=None):
    result = empty_evidence(status)
    if not isinstance(payload, dict) or not payload.get("ok"):
        return result
    internet = payload.get("internet_live") or {}
    pc = payload.get("pc_intelligence") or {}
    latest = pc.get("latest_network") or {}
    cloud = payload.get("cloud_dev") or {}
    counts = cloud.get("counts") or {}
    result.update(
        {
            "bridge_ok": True,
            "bridge_runtime_uptime_seconds": payload.get("runtime_uptime_seconds"),
            "internet_ok": internet.get("ok"),
            "internet_latency_ms": internet.get("latency_ms"),
            "pc_intel_freshness_seconds": latest.get("freshness_seconds"),
            "pc_internet_ok": latest.get("internet_ok"),
            "pc_connect_latency_ms": latest.get("connect_latency_ms"),
            "pc_boot_at": latest.get("boot_at"),
            "pc_uptime_seconds": latest.get("uptime_seconds"),
            "pc_wifi_state": latest.get("wifi_state"),
            "pc_wifi_signal": latest.get("wifi_signal"),
            "cloud_log_file": cloud.get("log_file"),
            "cloud_log_size_bytes": cloud.get("size_bytes"),
            "cloud_ws_1006_count": counts.get("ws_closed_1006"),
            "cloud_reconnect_count": counts.get("reconnect_requested"),
            "cloud_rpc_timeout_count": counts.get("desktop_rpc_timeout"),
            "cloud_stable_count": counts.get("cloud_stable"),
            "cloud_mcp_connected_count": counts.get("desktop_mcp_connected"),
            "payload": {
                "checked_at": payload.get("checked_at"),
                "internet_live": internet,
                "pc_intelligence": {
                    "available": pc.get("available"),
                    "latest_network": latest,
                    "last_internet_change": pc.get("last_internet_change"),
                    "error": pc.get("error"),
                },
                "cloud_dev": cloud,
            },
        }
    )
    return result


def collect_evidence(status):
    result = empty_evidence(status)
    if not EVIDENCE_BRIDGE_URL:
        return result
    try:
        with urllib.request.urlopen(EVIDENCE_BRIDGE_URL, timeout=3) as response:
            payload = json.loads(response.read(65536).decode("utf-8", "replace"))
        return normalize_evidence(payload, status)
    except Exception:
        return result


def record_evidence(conn, ts, evidence):
    conn.execute(
        """
        INSERT OR REPLACE INTO evidence_samples(
            ts, bridge_ok, runtime_uptime_seconds, bridge_runtime_uptime_seconds,
            internet_ok, internet_latency_ms, pc_intel_freshness_seconds,
            pc_internet_ok, pc_connect_latency_ms, pc_boot_at,
            pc_uptime_seconds, pc_wifi_state, pc_wifi_signal, cloud_log_file,
            cloud_log_size_bytes, cloud_ws_1006_count, cloud_reconnect_count,
            cloud_rpc_timeout_count, cloud_stable_count,
            cloud_mcp_connected_count, evidence_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ts,
            1 if evidence.get("bridge_ok") else 0,
            evidence.get("runtime_uptime_seconds"),
            evidence.get("bridge_runtime_uptime_seconds"),
            None
            if evidence.get("internet_ok") is None
            else (1 if evidence.get("internet_ok") else 0),
            evidence.get("internet_latency_ms"),
            evidence.get("pc_intel_freshness_seconds"),
            None
            if evidence.get("pc_internet_ok") is None
            else (1 if evidence.get("pc_internet_ok") else 0),
            evidence.get("pc_connect_latency_ms"),
            evidence.get("pc_boot_at"),
            evidence.get("pc_uptime_seconds"),
            evidence.get("pc_wifi_state"),
            evidence.get("pc_wifi_signal"),
            evidence.get("cloud_log_file"),
            evidence.get("cloud_log_size_bytes"),
            evidence.get("cloud_ws_1006_count"),
            evidence.get("cloud_reconnect_count"),
            evidence.get("cloud_rpc_timeout_count"),
            evidence.get("cloud_stable_count"),
            evidence.get("cloud_mcp_connected_count"),
            json.dumps(evidence.get("payload") or {}, separators=(",", ":")),
        ),
    )


def percentile(values, pct):
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    idx = (len(xs) - 1) * pct
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (idx - lo)


def latency_baseline(conn, ts=None):
    if ts is None:
        ts = int(time.time())
    since = ts - BASELINE_WINDOW_SECONDS
    rows = conn.execute(
        """
        SELECT worker_latency_ms
        FROM samples
        WHERE ts >= ? AND ts < ? AND worker_ok = 1
          AND worker_latency_ms IS NOT NULL
        ORDER BY ts ASC
        """,
        (since, ts),
    ).fetchall()
    values = [float(r["worker_latency_ms"]) for r in rows]
    if not values:
        return {
            "sample_count": 0,
            "median_ms": None,
            "p95_ms": None,
            "mad_ms": None,
            "anomaly_threshold_ms": STATIC_LATENCY_FALLBACK_MS,
            "ready": False,
        }
    median = statistics.median(values)
    deviations = [abs(v - median) for v in values]
    mad = statistics.median(deviations) if deviations else 0.0
    p95 = percentile(values, 0.95)
    robust = median + 6 * max(mad, 10.0)
    p95_rule = (p95 or median) * 2.5
    threshold = max(150.0, robust, p95_rule)
    ready = len(values) >= MIN_BASELINE_SAMPLES
    return {
        "sample_count": len(values),
        "median_ms": round(median, 1),
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "mad_ms": round(mad, 1),
        "anomaly_threshold_ms": round(
            threshold if ready else STATIC_LATENCY_FALLBACK_MS, 1
        ),
        "ready": ready,
    }


def status_states(status, baseline=None):
    pi_count = status["pi_node"]["reachable_ports"]
    pi_state = "up" if pi_count == 3 else ("partial" if pi_count else "down")
    worker = status["livingpanda"]["data_worker"]
    worker_state = "up" if worker.get("reachable") else "down"
    latency = worker.get("latency_ms")
    threshold = (baseline or {}).get(
        "anomaly_threshold_ms", STATIC_LATENCY_FALLBACK_MS
    )
    latency_state = (
        "slow"
        if worker_state == "up"
        and latency is not None
        and latency >= threshold
        else "normal"
    )
    return {"pi": pi_state, "worker": worker_state, "latency": latency_state}


def previous_states(conn, baseline=None):
    row = conn.execute(
        """
        SELECT pi_reachable_ports, worker_ok, worker_latency_ms
        FROM samples ORDER BY ts DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    pi_count = row["pi_reachable_ports"]
    threshold = (baseline or {}).get(
        "anomaly_threshold_ms", STATIC_LATENCY_FALLBACK_MS
    )
    return {
        "pi": "up" if pi_count == 3 else ("partial" if pi_count else "down"),
        "worker": "up" if row["worker_ok"] else "down",
        "latency": "slow"
        if row["worker_ok"]
        and row["worker_latency_ms"] is not None
        and row["worker_latency_ms"] >= threshold
        else "normal",
    }


def add_event(conn, ts, kind, severity, message, details=None):
    conn.execute(
        """
        INSERT INTO events(ts, kind, severity, message, details_json)
        VALUES(?,?,?,?,?)
        """,
        (
            ts,
            kind,
            severity,
            message,
            json.dumps(details or {}, separators=(",", ":")),
        ),
    )


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} second" + ("" if seconds == 1 else "s")
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minute = divmod(minutes, 60)
    return f"{hours}h {minute}m" if minute else f"{hours}h"


def severity_rank(value):
    return {"info": 0, "warning": 1, "error": 2}.get(value, 0)


def classify_fault(status, baseline):
    pi_count = status["pi_node"]["reachable_ports"]
    worker = status["livingpanda"]["data_worker"]
    local_web = status["livingpanda"]["local_web"]
    worker_ok = bool(worker.get("reachable"))
    web_ok = bool(local_web.get("reachable"))
    latency = worker.get("latency_ms")
    threshold = baseline["anomaly_threshold_ms"]

    if pi_count < 3 and (not worker_ok or not web_ok):
        return {
            "category": "host_network",
            "severity": "error" if pi_count == 0 else "warning",
            "title": CATEGORY_LABELS["host_network"],
            "details": {
                "pi_ports": pi_count,
                "worker_ok": worker_ok,
                "local_web_ok": web_ok,
            },
        }
    if pi_count < 3:
        return {
            "category": "pi_connectivity",
            "severity": "error" if pi_count == 0 else "warning",
            "title": CATEGORY_LABELS["pi_connectivity"],
            "details": {"pi_ports": pi_count},
        }
    if not worker_ok and not web_ok:
        return {
            "category": "livingpanda_stack",
            "severity": "error",
            "title": CATEGORY_LABELS["livingpanda_stack"],
            "details": {"worker_ok": False, "local_web_ok": False},
        }
    if not worker_ok:
        return {
            "category": "livingpanda_worker",
            "severity": "error",
            "title": CATEGORY_LABELS["livingpanda_worker"],
            "details": {"local_web_ok": web_ok},
        }
    if not web_ok:
        return {
            "category": "livingpanda_web",
            "severity": "warning",
            "title": CATEGORY_LABELS["livingpanda_web"],
            "details": {"worker_ok": worker_ok},
        }
    if latency is not None and latency >= threshold:
        return {
            "category": "worker_latency",
            "severity": "warning",
            "title": CATEGORY_LABELS["worker_latency"],
            "details": {
                "latency_ms": latency,
                "threshold_ms": threshold,
            },
        }
    return None


def correlate_categories(a, b):
    if a == b:
        return a
    cats = {a, b}
    if "host_network" in cats:
        return "host_network"
    if "pi_connectivity" in cats and len(cats) > 1:
        return "host_network"
    if cats == {"livingpanda_worker", "livingpanda_web"}:
        return "livingpanda_stack"
    if cats == {"worker_latency", "livingpanda_worker"}:
        return "livingpanda_worker"
    if cats == {"worker_latency", "livingpanda_stack"}:
        return "livingpanda_stack"
    return None


def get_open_incident(conn):
    return conn.execute(
        """
        SELECT * FROM incidents
        WHERE status='open'
        ORDER BY opened_ts DESC, id DESC LIMIT 1
        """
    ).fetchone()


def incident_summary(category, duration, recovered=True):
    label = CATEGORY_LABELS.get(category, "Local service degraded")
    suffix = " â†’ recovered automatically" if recovered else ""
    return f"{label} for {format_duration(duration)}{suffix}"


def counter_delta(rows, field):
    known = [r for r in rows if r[field] is not None]
    if len(known) < 2:
        return 0
    first = known[0]
    last = known[-1]
    if (
        first["cloud_log_file"]
        and last["cloud_log_file"]
        and first["cloud_log_file"] != last["cloud_log_file"]
    ):
        return 0
    return max(0, int(last[field]) - int(first[field]))


def infer_root_cause(conn, incident_row, closed_ts):
    start = incident_row["opened_ts"]
    before = conn.execute(
        """
        SELECT * FROM evidence_samples
        WHERE ts < ?
        ORDER BY ts DESC LIMIT 1
        """,
        (start,),
    ).fetchone()
    rows = conn.execute(
        """
        SELECT * FROM evidence_samples
        WHERE ts >= ? AND ts <= ?
        ORDER BY ts ASC
        """,
        (start - ROOT_CAUSE_CONTEXT_SECONDS, closed_ts + ROOT_CAUSE_CONTEXT_SECONDS),
    ).fetchall()
    service_rows = conn.execute(
        """
        SELECT * FROM samples
        WHERE ts >= ? AND ts <= ?
        ORDER BY ts ASC
        """,
        (start - ROOT_CAUSE_CONTEXT_SECONDS, closed_ts + ROOT_CAUSE_CONTEXT_SECONDS),
    ).fetchall()

    scores = {
        "host_restart": 0,
        "docker_restart": 0,
        "network_interruption": 0,
        "livingpanda_worker": 0,
        "pi_node": 0,
        "local_stack": 0,
    }
    evidence = []
    limitations = []

    runtime_reset = False
    sample_gap = None
    uptime_rows = [r for r in rows if r["runtime_uptime_seconds"] is not None]
    if before and uptime_rows and before["runtime_uptime_seconds"] is not None:
        first = uptime_rows[0]
        sample_gap = max(0, first["ts"] - before["ts"])
        if (
            float(first["runtime_uptime_seconds"]) + RUNTIME_RESET_MARGIN_SECONDS
            < float(before["runtime_uptime_seconds"])
        ):
            runtime_reset = True
            evidence.append(
                "Docker/Linux runtime uptime reset "
                f"from {round(float(before['runtime_uptime_seconds']), 1)}s "
                f"to {round(float(first['runtime_uptime_seconds']), 1)}s"
            )
    previous = before
    for row in uptime_rows:
        if (
            previous
            and previous["runtime_uptime_seconds"] is not None
            and float(row["runtime_uptime_seconds"]) + RUNTIME_RESET_MARGIN_SECONDS
            < float(previous["runtime_uptime_seconds"])
        ):
            runtime_reset = True
            marker = "Docker/Linux runtime uptime reset "
            if not any(item.startswith(marker) for item in evidence):
                evidence.append(
                    marker
                    + f"from {round(float(previous['runtime_uptime_seconds']), 1)}s "
                    + f"to {round(float(row['runtime_uptime_seconds']), 1)}s"
                )
        previous = row

    boot_values = []
    if before and before["pc_boot_at"]:
        boot_values.append(before["pc_boot_at"])
    boot_values.extend(r["pc_boot_at"] for r in rows if r["pc_boot_at"])
    boot_changed = len(set(boot_values)) > 1
    if boot_changed:
        scores["host_restart"] += 7
        evidence.append("PC Intelligence host boot timestamp changed during the incident window")
        if runtime_reset:
            scores["host_restart"] += 2
    elif runtime_reset:
        scores["docker_restart"] += 5

    if sample_gap and sample_gap > SAMPLE_INTERVAL_SECONDS * 2:
        evidence.append(f"Monitoring gap of {sample_gap}s occurred around the incident")
        if runtime_reset:
            scores["docker_restart"] += 1

    internet_values = [r["internet_ok"] for r in rows if r["internet_ok"] is not None]
    internet_outage = any(v == 0 for v in internet_values)
    internet_healthy_known = bool(internet_values) and all(v == 1 for v in internet_values)
    if internet_outage:
        scores["network_interruption"] += 5
        evidence.append("Live internet probe failed during the incident window")
    elif internet_healthy_known:
        evidence.append("Live internet probe remained reachable in the observed window")

    pc_fresh_rows = [
        r
        for r in rows
        if r["pc_intel_freshness_seconds"] is not None
        and r["pc_intel_freshness_seconds"] <= PC_INTEL_FRESH_SECONDS
    ]
    if pc_fresh_rows:
        if any(r["pc_internet_ok"] == 0 for r in pc_fresh_rows):
            scores["network_interruption"] += 2
            evidence.append("Fresh PC Intelligence also recorded internet unavailable")
        if any(r["pc_wifi_state"] not in (None, "connected") for r in pc_fresh_rows):
            scores["network_interruption"] += 2
            evidence.append("Fresh PC Intelligence recorded Wi-Fi disconnected or degraded")
    elif rows:
        stale = [
            r["pc_intel_freshness_seconds"]
            for r in rows
            if r["pc_intel_freshness_seconds"] is not None
        ]
        if stale:
            limitations.append(
                "PC Intelligence network snapshots were stale during this incident"
            )

    reconnect_delta = counter_delta(rows, "cloud_reconnect_count")
    ws_delta = counter_delta(rows, "cloud_ws_1006_count")
    rpc_delta = counter_delta(rows, "cloud_rpc_timeout_count")
    stable_delta = counter_delta(rows, "cloud_stable_count")
    if reconnect_delta:
        evidence.append(
            f"Cloud DEV reconnect counter increased by {reconnect_delta}"
        )
        scores["network_interruption"] += 1
    if ws_delta:
        evidence.append(
            f"Cloud DEV abnormal WebSocket closure count increased by {ws_delta}"
        )
        scores["network_interruption"] += 1
    if rpc_delta:
        evidence.append(
            f"Desktop RPC timeout counter increased by {rpc_delta}"
        )
    if stable_delta:
        evidence.append(
            f"Cloud DEV stable-connection counter increased by {stable_delta}"
        )

    category = incident_row["category"]
    if category in ("host_network", "livingpanda_stack"):
        evidence.append("Multiple local services degraded in the same incident")
        scores["local_stack"] += 2
        if runtime_reset:
            if boot_changed:
                scores["host_restart"] += 2
            else:
                scores["docker_restart"] += 2
        if internet_healthy_known and not runtime_reset:
            scores["local_stack"] += 2
    if category == "livingpanda_worker":
        scores["livingpanda_worker"] += 4
        if internet_healthy_known:
            scores["livingpanda_worker"] += 1
        if not runtime_reset:
            scores["livingpanda_worker"] += 1
        evidence.append(
            "Pi connectivity remained separate from the LivingPanda worker failure"
        )
    if category == "livingpanda_web":
        scores["local_stack"] += 3
        if internet_healthy_known:
            scores["local_stack"] += 1
    if category == "worker_latency":
        scores["livingpanda_worker"] += 3
        if internet_healthy_known:
            scores["livingpanda_worker"] += 1
        evidence.append("Incident was isolated to LivingPanda worker latency")
    if category == "pi_connectivity":
        scores["pi_node"] += 4
        if internet_healthy_known:
            scores["pi_node"] += 1
        if not runtime_reset:
            scores["pi_node"] += 1
        if service_rows and all(r["worker_ok"] for r in service_rows):
            scores["pi_node"] += 1
            evidence.append(
                "LivingPanda worker remained reachable while Pi ports degraded"
            )

    if runtime_reset and internet_healthy_known:
        if boot_changed:
            scores["host_restart"] += 1
        else:
            scores["docker_restart"] += 1
    if internet_outage and not runtime_reset:
        scores["network_interruption"] += 1

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    cause, score = ordered[0]
    if score <= 1:
        cause = "unknown"
        score = 0

    second_score = ordered[1][1] if len(ordered) > 1 else 0
    margin = score - second_score
    if cause == "unknown":
        confidence = "low"
    elif score >= 6 and margin >= 2:
        confidence = "high"
    elif score >= 4:
        confidence = "medium"
    else:
        confidence = "low"

    if not rows:
        limitations.append("No v0.5 evidence samples were available for this incident")
    elif not any(r["bridge_ok"] for r in rows):
        limitations.append(
            "PC Intelligence evidence bridge was unavailable; assessment used local signals only"
        )

    return {
        "cause": cause,
        "label": CAUSE_LABELS[cause],
        "confidence": confidence,
        "score": score,
        "evidence": evidence[:8],
        "limitations": limitations[:4],
        "candidates": [
            {
                "cause": key,
                "label": CAUSE_LABELS[key],
                "score": value,
            }
            for key, value in ordered
            if value > 0
        ][:4],
    }


def close_incident(conn, row, ts, reason="recovered"):
    duration = max(0, ts - row["opened_ts"])
    recovered = reason == "recovered"
    summary = incident_summary(row["category"], duration, recovered=recovered)
    root = infer_root_cause(conn, row, ts)
    conn.execute(
        """
        UPDATE incidents
        SET closed_ts=?, status='closed', close_reason=?, summary=?,
            root_cause=?, root_cause_confidence=?, root_cause_score=?,
            root_cause_evidence_json=?
        WHERE id=?
        """,
        (
            ts,
            reason,
            summary,
            root["cause"],
            root["confidence"],
            root["score"],
            json.dumps(
                {
                    "evidence": root["evidence"],
                    "limitations": root["limitations"],
                    "candidates": root["candidates"],
                },
                separators=(",", ":"),
            ),
            row["id"],
        ),
    )
    add_event(
        conn,
        ts,
        "incident_root_cause",
        "info",
        f"Likely cause: {root['label']} ({root['confidence']} confidence)",
        {
            "incident_id": row["id"],
            "cause": root["cause"],
            "confidence": root["confidence"],
            "score": root["score"],
        },
    )
    if recovered:
        add_event(
            conn,
            ts,
            "incident_recovered",
            "info",
            summary,
            {
                "incident_id": row["id"],
                "category": row["category"],
                "duration_seconds": duration,
            },
        )


def update_incident(conn, row, fault, status, baseline, ts):
    latency = status["livingpanda"]["data_worker"].get("latency_ms")
    pi_count = status["pi_node"]["reachable_ports"]
    peak = row["peak_worker_latency_ms"]
    if latency is not None:
        peak = max(float(peak or 0), float(latency))
    min_pi = min(
        int(row["min_pi_ports"] if row["min_pi_ports"] is not None else 3),
        int(pi_count),
    )
    severity = (
        fault["severity"]
        if severity_rank(fault["severity"]) > severity_rank(row["severity"])
        else row["severity"]
    )
    details = json.loads(row["details_json"] or "{}")
    seen = set(details.get("correlated_categories", [row["category"]]))
    seen.add(fault["category"])
    details["correlated_categories"] = sorted(seen)
    details["last_state"] = fault["details"]
    details["last_updated_ts"] = ts
    conn.execute(
        """
        UPDATE incidents
        SET severity=?, peak_worker_latency_ms=?, min_pi_ports=?, details_json=?
        WHERE id=?
        """,
        (
            severity,
            peak,
            min_pi,
            json.dumps(details, separators=(",", ":")),
            row["id"],
        ),
    )


def open_incident(conn, fault, status, baseline, ts):
    latency = status["livingpanda"]["data_worker"].get("latency_ms")
    pi_count = status["pi_node"]["reachable_ports"]
    details = {
        "first_state": fault["details"],
        "correlated_categories": [fault["category"]],
        "baseline": baseline,
    }
    cur = conn.execute(
        """
        INSERT INTO incidents(
            opened_ts, category, status, severity, title, summary,
            peak_worker_latency_ms, min_pi_ports, baseline_ms, details_json
        ) VALUES(?,?,'open',?,?,?,?,?,?,?)
        """,
        (
            ts,
            fault["category"],
            fault["severity"],
            fault["title"],
            fault["title"] + " â€” ongoing",
            latency,
            pi_count,
            baseline.get("median_ms"),
            json.dumps(details, separators=(",", ":")),
        ),
    )
    incident_id = cur.lastrowid
    add_event(
        conn,
        ts,
        "incident_opened",
        fault["severity"],
        fault["title"],
        {
            "incident_id": incident_id,
            "category": fault["category"],
            **fault["details"],
        },
    )


def handle_incident(conn, status, baseline):
    ts = status["checked_ts"]
    fault = classify_fault(status, baseline)
    open_row = get_open_incident(conn)

    if fault is None:
        if open_row:
            close_incident(conn, open_row, ts, "recovered")
        return

    if open_row is None:
        open_incident(conn, fault, status, baseline, ts)
        return

    if open_row["category"] == fault["category"]:
        update_incident(conn, open_row, fault, status, baseline, ts)
        return

    merged = correlate_categories(open_row["category"], fault["category"])
    age = ts - open_row["opened_ts"]
    if merged and age <= INCIDENT_CORRELATION_SECONDS:
        details = json.loads(open_row["details_json"] or "{}")
        seen = set(
            details.get("correlated_categories", [open_row["category"]])
        )
        seen.add(fault["category"])
        details["correlated_categories"] = sorted(seen)
        details["correlation_window_seconds"] = INCIDENT_CORRELATION_SECONDS
        details["last_state"] = fault["details"]
        title = CATEGORY_LABELS[merged]
        severity = (
            "error"
            if severity_rank(fault["severity"]) >= severity_rank("error")
            or severity_rank(open_row["severity"]) >= severity_rank("error")
            else "warning"
        )
        latency = status["livingpanda"]["data_worker"].get("latency_ms")
        peak = open_row["peak_worker_latency_ms"]
        if latency is not None:
            peak = max(float(peak or 0), float(latency))
        min_pi = min(
            int(
                open_row["min_pi_ports"]
                if open_row["min_pi_ports"] is not None
                else 3
            ),
            int(status["pi_node"]["reachable_ports"]),
        )
        conn.execute(
            """
            UPDATE incidents SET category=?, severity=?, title=?, summary=?,
                peak_worker_latency_ms=?, min_pi_ports=?, details_json=?
            WHERE id=?
            """,
            (
                merged,
                severity,
                title,
                title + " â€” correlated incident ongoing",
                peak,
                min_pi,
                json.dumps(details, separators=(",", ":")),
                open_row["id"],
            ),
        )
        add_event(
            conn,
            ts,
            "incident_correlated",
            severity,
            f"Correlated {open_row['category']} + {fault['category']} into {merged}",
            {"incident_id": open_row["id"], "category": merged},
        )
        return

    close_incident(conn, open_row, ts, "condition_changed")
    open_incident(conn, fault, status, baseline, ts)


def backfill_incidents_from_events(conn):
    if conn.execute(
        "SELECT value FROM meta WHERE key='incident_backfill_v1'"
    ).fetchone():
        return
    if conn.execute("SELECT count(*) FROM incidents").fetchone()[0] > 0:
        conn.execute(
            """
            INSERT OR REPLACE INTO meta(key,value)
            VALUES('incident_backfill_v1','existing')
            """
        )
        return

    opens = {
        "worker_slow": ("worker_latency", "warning"),
        "pi_unreachable": ("pi_connectivity", "error"),
        "pi_partial": ("pi_connectivity", "warning"),
        "worker_unreachable": ("livingpanda_worker", "error"),
    }
    closes = {
        "worker_latency_recovered": "worker_latency",
        "pi_recovered": "pi_connectivity",
        "worker_recovered": "livingpanda_worker",
    }
    active = {}
    rows = conn.execute(
        "SELECT * FROM events ORDER BY ts ASC, id ASC"
    ).fetchall()
    for row in rows:
        kind = row["kind"]
        if kind in opens:
            category, severity = opens[kind]
            active.setdefault(category, row)
        elif kind in closes:
            category = closes[kind]
            start = active.pop(category, None)
            if start:
                duration = max(0, row["ts"] - start["ts"])
                details = {
                    "backfilled_from_events": [start["id"], row["id"]]
                }
                conn.execute(
                    """
                    INSERT INTO incidents(
                        opened_ts, closed_ts, category, status, severity, title,
                        summary, close_reason, details_json
                    ) VALUES(?,? ,?,'closed',?,?,?,?,?)
                    """,
                    (
                        start["ts"],
                        row["ts"],
                        category,
                        start["severity"],
                        CATEGORY_LABELS[category],
                        incident_summary(category, duration, True),
                        "recovered",
                        json.dumps(details, separators=(",", ":")),
                    ),
                )
    conn.execute(
        """
        INSERT OR REPLACE INTO meta(key,value)
        VALUES('incident_backfill_v1','done')
        """
    )


def record_sample(status, evidence=None):
    ts = status["checked_ts"]
    pi = status["pi_node"]
    worker = status["livingpanda"]["data_worker"]
    local_web = status["livingpanda"]["local_web"]
    mem = status["solo_host"].get("memory_current_mb")
    if evidence is None:
        evidence = empty_evidence(status)

    with db() as conn:
        baseline = latency_baseline(conn, ts)
        prev = previous_states(conn, baseline)
        now = status_states(status, baseline)
        conn.execute(
            """
            INSERT OR REPLACE INTO samples(
                ts, pi_reachable_ports, pi_31401, pi_31402, pi_31403,
                worker_ok, worker_latency_ms, local_web_ok,
                local_web_latency_ms, memory_mb
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                pi["reachable_ports"],
                1 if pi["ports"]["31401"]["reachable"] else 0,
                1 if pi["ports"]["31402"]["reachable"] else 0,
                1 if pi["ports"]["31403"]["reachable"] else 0,
                1 if worker.get("reachable") else 0,
                worker.get("latency_ms"),
                1 if local_web.get("reachable") else 0,
                local_web.get("latency_ms"),
                mem,
            ),
        )
        record_evidence(conn, ts, evidence)

        if prev is None:
            add_event(
                conn,
                ts,
                "monitor",
                "info",
                "Local operator monitoring started",
                {"version": VERSION},
            )
        else:
            if prev["pi"] != now["pi"]:
                if now["pi"] == "up":
                    add_event(
                        conn,
                        ts,
                        "pi_recovered",
                        "info",
                        "Pi Node ports recovered",
                        {"reachable_ports": 3},
                    )
                elif now["pi"] == "partial":
                    add_event(
                        conn,
                        ts,
                        "pi_partial",
                        "warning",
                        "Some Pi Node ports became unreachable",
                        {"reachable_ports": pi["reachable_ports"]},
                    )
                else:
                    add_event(
                        conn,
                        ts,
                        "pi_unreachable",
                        "error",
                        "Pi Node became unreachable",
                        {"reachable_ports": 0},
                    )
            if prev["worker"] != now["worker"]:
                if now["worker"] == "up":
                    add_event(
                        conn,
                        ts,
                        "worker_recovered",
                        "info",
                        "LivingPanda worker recovered",
                        {"latency_ms": worker.get("latency_ms")},
                    )
                else:
                    add_event(
                        conn,
                        ts,
                        "worker_unreachable",
                        "warning",
                        "LivingPanda worker became unreachable",
                    )
            if (
                prev["latency"] != now["latency"]
                and now["worker"] == "up"
            ):
                if now["latency"] == "slow":
                    add_event(
                        conn,
                        ts,
                        "worker_slow",
                        "warning",
                        "LivingPanda worker latency became slow",
                        {
                            "latency_ms": worker.get("latency_ms"),
                            "threshold_ms": baseline[
                                "anomaly_threshold_ms"
                            ],
                        },
                    )
                else:
                    add_event(
                        conn,
                        ts,
                        "worker_latency_recovered",
                        "info",
                        "LivingPanda worker latency recovered",
                        {"latency_ms": worker.get("latency_ms")},
                    )

        handle_incident(conn, status, baseline)
        conn.execute(
            "DELETE FROM samples WHERE ts < ?",
            (ts - SAMPLE_RETENTION_SECONDS,),
        )
        conn.execute(
            "DELETE FROM events WHERE ts < ?",
            (ts - EVENT_RETENTION_SECONDS,),
        )
        conn.execute(
            "DELETE FROM evidence_samples WHERE ts < ?",
            (ts - EVIDENCE_RETENTION_SECONDS,),
        )
        conn.execute(
            """
            DELETE FROM incidents
            WHERE status='closed' AND closed_ts < ?
            """,
            (ts - INCIDENT_RETENTION_SECONDS,),
        )


def history(minutes=60):
    minutes = max(5, min(1440, int(minutes)))
    since = int(time.time()) - minutes * 60
    with db() as conn:
        rows = conn.execute(
            """
            SELECT ts, pi_reachable_ports, worker_ok, worker_latency_ms,
                   local_web_ok, local_web_latency_ms, memory_mb
            FROM samples WHERE ts >= ? ORDER BY ts ASC
            """,
            (since,),
        ).fetchall()

    samples = [
        {
            "ts": r["ts"],
            "time": utc_iso(r["ts"]),
            "pi_ports": r["pi_reachable_ports"],
            "worker_ok": bool(r["worker_ok"]),
            "worker_latency_ms": r["worker_latency_ms"],
            "local_web_ok": bool(r["local_web_ok"]),
            "local_web_latency_ms": r["local_web_latency_ms"],
            "memory_mb": r["memory_mb"],
        }
        for r in rows
    ]
    count = len(samples)
    worker_latencies = [
        x["worker_latency_ms"]
        for x in samples
        if x["worker_ok"] and x["worker_latency_ms"] is not None
    ]
    return {
        "minutes": minutes,
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "samples": samples,
        "summary": {
            "sample_count": count,
            "pi_full_availability_pct": round(
                sum(1 for x in samples if x["pi_ports"] == 3)
                * 100
                / count,
                1,
            )
            if count
            else None,
            "worker_availability_pct": round(
                sum(1 for x in samples if x["worker_ok"]) * 100 / count,
                1,
            )
            if count
            else None,
            "worker_latency_avg_ms": round(
                sum(worker_latencies) / len(worker_latencies), 1
            )
            if worker_latencies
            else None,
            "worker_latency_p95_ms": round(
                percentile(worker_latencies, 0.95), 1
            )
            if worker_latencies
            else None,
        },
    }


def recent_events(limit=50):
    limit = max(1, min(200, int(limit)))
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, ts, kind, severity, message, details_json
            FROM events ORDER BY ts DESC, id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "ts": r["ts"],
            "time": utc_iso(r["ts"]),
            "kind": r["kind"],
            "severity": r["severity"],
            "message": r["message"],
            "details": json.loads(r["details_json"] or "{}"),
        }
        for r in rows
    ]


def root_cause_from_row(row):
    cause = row["root_cause"]
    if not cause:
        return None
    detail = {}
    try:
        detail = json.loads(row["root_cause_evidence_json"] or "{}")
    except Exception:
        detail = {}
    return {
        "cause": cause,
        "label": CAUSE_LABELS.get(cause, cause),
        "confidence": row["root_cause_confidence"],
        "score": row["root_cause_score"],
        "evidence": detail.get("evidence", []),
        "limitations": detail.get("limitations", []),
        "candidates": detail.get("candidates", []),
    }


def incident_to_dict(row):
    now = int(time.time())
    closed = row["closed_ts"]
    duration = (closed or now) - row["opened_ts"]
    return {
        "id": row["id"],
        "opened_ts": row["opened_ts"],
        "opened_at": utc_iso(row["opened_ts"]),
        "closed_ts": closed,
        "closed_at": utc_iso(closed) if closed else None,
        "duration_seconds": max(0, duration),
        "duration": format_duration(duration),
        "category": row["category"],
        "status": row["status"],
        "severity": row["severity"],
        "title": row["title"],
        "summary": row["summary"]
        if row["status"] == "closed"
        else f"{row['title']} for {format_duration(duration)} â€” ongoing",
        "close_reason": row["close_reason"],
        "peak_worker_latency_ms": row["peak_worker_latency_ms"],
        "min_pi_ports": row["min_pi_ports"],
        "baseline_ms": row["baseline_ms"],
        "root_cause": root_cause_from_row(row),
        "details": json.loads(row["details_json"] or "{}"),
    }


def incidents(limit=30, status=None):
    limit = max(1, min(200, int(limit)))
    sql = "SELECT * FROM incidents"
    params = []
    if status in ("open", "closed"):
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY opened_ts DESC, id DESC LIMIT ?"
    params.append(limit)
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [incident_to_dict(r) for r in rows]


def baseline_status():
    with db() as conn:
        result = latency_baseline(conn, int(time.time()) + 1)
    result["window_hours"] = BASELINE_WINDOW_SECONDS // 3600
    result["minimum_samples"] = MIN_BASELINE_SAMPLES
    return result


def latest_evidence():
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM evidence_samples
            ORDER BY ts DESC LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    return {
        "ts": row["ts"],
        "checked_at": utc_iso(row["ts"]),
        "bridge_ok": bool(row["bridge_ok"]),
        "runtime_uptime_seconds": row["runtime_uptime_seconds"],
        "bridge_runtime_uptime_seconds": row[
            "bridge_runtime_uptime_seconds"
        ],
        "internet_ok": None
        if row["internet_ok"] is None
        else bool(row["internet_ok"]),
        "internet_latency_ms": row["internet_latency_ms"],
        "pc_intel_freshness_seconds": row[
            "pc_intel_freshness_seconds"
        ],
        "pc_internet_ok": None
        if row["pc_internet_ok"] is None
        else bool(row["pc_internet_ok"]),
        "pc_connect_latency_ms": row["pc_connect_latency_ms"],
        "pc_boot_at": row["pc_boot_at"],
        "pc_uptime_seconds": row["pc_uptime_seconds"],
        "pc_wifi_state": row["pc_wifi_state"],
        "pc_wifi_signal": row["pc_wifi_signal"],
        "cloud_log_file": row["cloud_log_file"],
        "cloud_log_size_bytes": row["cloud_log_size_bytes"],
        "cloud_counts": {
            "ws_closed_1006": row["cloud_ws_1006_count"],
            "reconnect_requested": row["cloud_reconnect_count"],
            "desktop_rpc_timeout": row["cloud_rpc_timeout_count"],
            "cloud_stable": row["cloud_stable_count"],
            "desktop_mcp_connected": row[
                "cloud_mcp_connected_count"
            ],
        },
    }


def monitor_loop():
    global LAST_STATUS, LAST_EVIDENCE
    while True:
        started = time.monotonic()
        try:
            status = collect_raw_status()
            evidence = collect_evidence(status)
            record_sample(status, evidence)
            with STATE_LOCK:
                LAST_STATUS = status
                LAST_EVIDENCE = evidence
        except Exception as exc:
            print(f"monitor error: {exc!r}", flush=True)
        delay = max(
            1.0,
            SAMPLE_INTERVAL_SECONDS - (time.monotonic() - started),
        )
        time.sleep(delay)


def current_status():
    with STATE_LOCK:
        status = LAST_STATUS
    if status is None:
        status = collect_raw_status()
    status = json.loads(json.dumps(status))
    h = history(60)
    with db() as conn:
        baseline = latency_baseline(conn, int(time.time()) + 1)
        open_row = get_open_incident(conn)
    status["intelligence"] = {
        "history_window_minutes": 60,
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "summary": h["summary"],
        "baseline": baseline,
        "active_incident": incident_to_dict(open_row) if open_row else None,
        "recent_incidents": incidents(8),
        "recent_events": recent_events(8),
        "latest_evidence": latest_evidence(),
        "storage": "SQLite in app-owned Docker volume",
        "sample_retention_hours": 24,
        "event_retention_days": 7,
        "incident_retention_days": 30,
        "evidence_retention_days": 7,
        "correlation_window_seconds": INCIDENT_CORRELATION_SECONDS,
    }
    return status


class Handler(BaseHTTPRequestHandler):
    server_version = "LivingPandaPiUtility/0.5"

    def send_bytes(self, status, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/":
            self.send_bytes(
                200,
                INDEX.encode("utf-8"),
                "text/html; charset=utf-8",
            )
        elif parsed.path == "/health":
            self.send_json(
                {
                    "ok": True,
                    "service": "livingpanda-pi-utility",
                    "version": VERSION,
                }
            )
        elif parsed.path == "/api/status":
            self.send_json(current_status())
        elif parsed.path == "/api/history":
            self.send_json(
                history(query.get("minutes", ["60"])[0])
            )
        elif parsed.path == "/api/events":
            self.send_json(
                {
                    "events": recent_events(
                        query.get("limit", ["50"])[0]
                    )
                }
            )
        elif parsed.path == "/api/incidents":
            self.send_json(
                {
                    "incidents": incidents(
                        query.get("limit", ["30"])[0],
                        query.get("status", [None])[0],
                    )
                }
            )
        elif parsed.path == "/api/baseline":
            self.send_json(baseline_status())
        elif parsed.path == "/api/evidence":
            self.send_json({"evidence": latest_evidence()})
        else:
            self.send_json(
                {"ok": False, "error": "not_found"},
                404,
            )

    def log_message(self, fmt, *args):
        print(
            f"{self.address_string()} - {fmt % args}",
            flush=True,
        )


if __name__ == "__main__":
    init_db()
    thread = threading.Thread(
        target=monitor_loop,
        name="operator-monitor",
        daemon=True,
    )
    thread.start()
    print(
        f"LivingPanda Pi Utility v{VERSION} listening on {HOST}:{PORT}",
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

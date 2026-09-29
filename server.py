import json
import math
import os
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.3.0"
HOST = "0.0.0.0"
PORT = 8080
HOST_GATEWAY = "host.docker.internal"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "operator-intelligence.sqlite3"
SAMPLE_INTERVAL_SECONDS = 10
SAMPLE_RETENTION_SECONDS = 24 * 60 * 60
EVENT_RETENTION_SECONDS = 7 * 24 * 60 * 60
SLOW_WORKER_MS = 250.0
BASE_DIR = Path(__file__).resolve().parent
INDEX = (BASE_DIR / "index.html").read_text(encoding="utf-8")

STATE_LOCK = threading.Lock()
LAST_STATUS = None


def utc_iso(ts=None):
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


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
            CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            """
        )


def tcp_probe(host: str, port: int, timeout: float = 1.0):
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"reachable": True, "latency_ms": round((time.monotonic() - started) * 1000, 1)}
    except Exception as exc:
        return {"reachable": False, "error": exc.__class__.__name__}


def http_probe(url: str, timeout: float = 2.0):
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(1024).decode("utf-8", "replace")
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
        "memory_current_mb": round(memory_current / 1024 / 1024, 1) if memory_current is not None else None,
        "memory_limit_mb": round(memory_max / 1024 / 1024, 1) if memory_max is not None else None,
    }


def collect_raw_status():
    pi_ports = {str(port): tcp_probe(HOST_GATEWAY, port) for port in (31401, 31402, 31403)}
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
            "status": "reachable" if reachable == 3 else ("partial" if reachable else "not_detected"),
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
        },
    }


def status_states(status):
    pi_count = status["pi_node"]["reachable_ports"]
    pi_state = "up" if pi_count == 3 else ("partial" if pi_count else "down")
    worker = status["livingpanda"]["data_worker"]
    worker_state = "up" if worker.get("reachable") else "down"
    latency = worker.get("latency_ms")
    latency_state = "slow" if worker_state == "up" and latency is not None and latency >= SLOW_WORKER_MS else "normal"
    return {"pi": pi_state, "worker": worker_state, "latency": latency_state}


def previous_states(conn):
    row = conn.execute(
        "SELECT pi_reachable_ports, worker_ok, worker_latency_ms FROM samples ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    pi_count = row["pi_reachable_ports"]
    return {
        "pi": "up" if pi_count == 3 else ("partial" if pi_count else "down"),
        "worker": "up" if row["worker_ok"] else "down",
        "latency": "slow" if row["worker_ok"] and row["worker_latency_ms"] is not None and row["worker_latency_ms"] >= SLOW_WORKER_MS else "normal",
    }


def add_event(conn, ts, kind, severity, message, details=None):
    conn.execute(
        "INSERT INTO events(ts, kind, severity, message, details_json) VALUES(?,?,?,?,?)",
        (ts, kind, severity, message, json.dumps(details or {}, separators=(",", ":"))),
    )


def record_sample(status):
    ts = status["checked_ts"]
    pi = status["pi_node"]
    worker = status["livingpanda"]["data_worker"]
    local_web = status["livingpanda"]["local_web"]
    mem = status["solo_host"].get("memory_current_mb")
    with db() as conn:
        prev = previous_states(conn)
        now = status_states(status)
        conn.execute(
            """
            INSERT OR REPLACE INTO samples(
                ts, pi_reachable_ports, pi_31401, pi_31402, pi_31403,
                worker_ok, worker_latency_ms, local_web_ok, local_web_latency_ms, memory_mb
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

        if prev is None:
            add_event(conn, ts, "monitor", "info", "Local operator monitoring started", {"version": VERSION})
        else:
            if prev["pi"] != now["pi"]:
                if now["pi"] == "up":
                    add_event(conn, ts, "pi_recovered", "info", "Pi Node ports recovered", {"reachable_ports": 3})
                elif now["pi"] == "partial":
                    add_event(conn, ts, "pi_partial", "warning", "Some Pi Node ports became unreachable", {"reachable_ports": pi["reachable_ports"]})
                else:
                    add_event(conn, ts, "pi_unreachable", "error", "Pi Node became unreachable", {"reachable_ports": 0})

            if prev["worker"] != now["worker"]:
                if now["worker"] == "up":
                    add_event(conn, ts, "worker_recovered", "info", "LivingPanda worker recovered", {"latency_ms": worker.get("latency_ms")})
                else:
                    add_event(conn, ts, "worker_unreachable", "warning", "LivingPanda worker became unreachable")

            if prev["latency"] != now["latency"] and now["worker"] == "up":
                if now["latency"] == "slow":
                    add_event(conn, ts, "worker_slow", "warning", "LivingPanda worker latency became slow", {"latency_ms": worker.get("latency_ms"), "threshold_ms": SLOW_WORKER_MS})
                else:
                    add_event(conn, ts, "worker_latency_recovered", "info", "LivingPanda worker latency recovered", {"latency_ms": worker.get("latency_ms")})

        conn.execute("DELETE FROM samples WHERE ts < ?", (ts - SAMPLE_RETENTION_SECONDS,))
        conn.execute("DELETE FROM events WHERE ts < ?", (ts - EVENT_RETENTION_SECONDS,))


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
    worker_latencies = [x["worker_latency_ms"] for x in samples if x["worker_ok"] and x["worker_latency_ms"] is not None]
    return {
        "minutes": minutes,
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "samples": samples,
        "summary": {
            "sample_count": count,
            "pi_full_availability_pct": round(sum(1 for x in samples if x["pi_ports"] == 3) * 100 / count, 1) if count else None,
            "worker_availability_pct": round(sum(1 for x in samples if x["worker_ok"]) * 100 / count, 1) if count else None,
            "worker_latency_avg_ms": round(sum(worker_latencies) / len(worker_latencies), 1) if worker_latencies else None,
            "worker_latency_p95_ms": round(percentile(worker_latencies, 0.95), 1) if worker_latencies else None,
        },
    }


def recent_events(limit=50):
    limit = max(1, min(200, int(limit)))
    with db() as conn:
        rows = conn.execute(
            "SELECT id, ts, kind, severity, message, details_json FROM events ORDER BY ts DESC, id DESC LIMIT ?",
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


def monitor_loop():
    global LAST_STATUS
    while True:
        started = time.monotonic()
        try:
            status = collect_raw_status()
            record_sample(status)
            with STATE_LOCK:
                LAST_STATUS = status
        except Exception as exc:
            print(f"monitor error: {exc!r}", flush=True)
        delay = max(1.0, SAMPLE_INTERVAL_SECONDS - (time.monotonic() - started))
        time.sleep(delay)


def current_status():
    with STATE_LOCK:
        status = LAST_STATUS
    if status is None:
        status = collect_raw_status()
    status = json.loads(json.dumps(status))
    h = history(60)
    status["intelligence"] = {
        "history_window_minutes": 60,
        "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS,
        "summary": h["summary"],
        "recent_events": recent_events(8),
        "storage": "SQLite in app-owned Docker volume",
        "sample_retention_hours": 24,
        "event_retention_days": 7,
    }
    return status


class Handler(BaseHTTPRequestHandler):
    server_version = "LivingPandaPiUtility/0.3"

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
            self.send_bytes(200, INDEX.encode("utf-8"), "text/html; charset=utf-8")
        elif parsed.path == "/health":
            self.send_json({"ok": True, "service": "livingpanda-pi-utility", "version": VERSION})
        elif parsed.path == "/api/status":
            self.send_json(current_status())
        elif parsed.path == "/api/history":
            self.send_json(history(query.get("minutes", ["60"])[0]))
        elif parsed.path == "/api/events":
            self.send_json({"events": recent_events(query.get("limit", ["50"])[0])})
        else:
            self.send_json({"ok": False, "error": "not_found"}, 404)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    init_db()
    thread = threading.Thread(target=monitor_loop, name="operator-monitor", daemon=True)
    thread.start()
    print(f"LivingPanda Pi Utility v{VERSION} listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

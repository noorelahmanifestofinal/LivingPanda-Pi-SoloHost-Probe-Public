import json
import os
import socket
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.2.0"
HOST = "0.0.0.0"
PORT = 8080
HOST_GATEWAY = "host.docker.internal"
BASE_DIR = Path(__file__).resolve().parent
INDEX = (BASE_DIR / "index.html").read_text(encoding="utf-8")


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


def collect_status():
    pi_ports = {str(port): tcp_probe(HOST_GATEWAY, port) for port in (31401, 31402, 31403)}
    reachable = sum(1 for item in pi_ports.values() if item["reachable"])
    data_worker = http_probe(f"http://{HOST_GATEWAY}:8000/health")
    local_web = http_probe(f"http://{HOST_GATEWAY}:8080/")
    return {
        "ok": True,
        "version": VERSION,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "pi_node": {
            "host": HOST_GATEWAY,
            "reachable_ports": reachable,
            "total_ports": 3,
            "status": "reachable" if reachable else "not_detected",
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
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LivingPandaPiUtility/0.2"

    def send_bytes(self, status, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.send_bytes(200, INDEX.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/health":
            body = json.dumps({"ok": True, "service": "livingpanda-pi-utility", "version": VERSION}).encode()
            self.send_bytes(200, body, "application/json; charset=utf-8")
            return
        if self.path == "/api/status":
            body = json.dumps(collect_status()).encode()
            self.send_bytes(200, body, "application/json; charset=utf-8")
            return
        self.send_bytes(404, b'{"ok":false,"error":"not_found"}', "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)


if __name__ == "__main__":
    print(f"LivingPanda Pi Utility v{VERSION} listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

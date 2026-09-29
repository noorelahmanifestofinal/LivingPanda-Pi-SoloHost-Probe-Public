import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from fastapi import FastAPI

VERSION = "0.1.0"
PC_DB = Path(
    os.environ.get(
        "PC_INTELLIGENCE_DB",
        "/pc-intelligence/pc-intelligence.duckdb",
    )
)
PC_STATE_JSON = PC_DB.parent / "state" / "last-activity-network.json"
CLOUD_LOG_DIR = Path(
    os.environ.get("CLOUD_DEVICE_LOG_DIR", "/cloud-device-logs")
)
MAX_LOG_BYTES = 2 * 1024 * 1024

app = FastAPI(
    title="LivingPanda PC Intelligence Evidence Bridge",
    version=VERSION,
)


def utc_iso_from_timestamp(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def runtime_uptime_seconds():
    try:
        return round(
            float(Path("/proc/uptime").read_text().split()[0]),
            1,
        )
    except Exception:
        return None


def live_internet_probe():
    started = time.monotonic()
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2.0):
            return {
                "ok": True,
                "latency_ms": round(
                    (time.monotonic() - started) * 1000,
                    1,
                ),
                "target": "1.1.1.1:443",
            }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": None,
            "target": "1.1.1.1:443",
            "error": exc.__class__.__name__,
        }


def state_network_summary():
    if not PC_STATE_JSON.exists():
        return None
    state = json.loads(
        PC_STATE_JSON.read_text(encoding="utf-8")
    )
    captured_text = state.get("capturedAt")
    captured = (
        datetime.fromisoformat(captured_text)
        if captured_text
        else None
    )
    if captured and captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    freshness = None
    if captured:
        freshness = max(
            0.0,
            (
                datetime.now(timezone.utc)
                - captured.astimezone(timezone.utc)
            ).total_seconds(),
        )
    wifi = state.get("wifi") or {}
    return {
        "captured_at": captured_text,
        "freshness_seconds": (
            round(freshness, 1)
            if freshness is not None
            else None
        ),
        "boot_at": state.get("bootAt"),
        "uptime_seconds": state.get("uptimeSeconds"),
        "internet_ok": state.get("internetOk"),
        "connect_latency_ms": state.get("connectLatencyMs"),
        "wifi_state": wifi.get("state"),
        "wifi_signal": wifi.get("signal_percent"),
        "wifi_receive_mbps": wifi.get("receive_mbps"),
        "wifi_transmit_mbps": wifi.get("transmit_mbps"),
    }


def duckdb_network_summary(con):
    row = con.execute(
        """
        SELECT captured_at, host, boot_at, uptime_seconds, internet_ok,
               connect_latency_ms, wifi_state, wifi_signal,
               wifi_receive_mbps, wifi_transmit_mbps
        FROM activity_network
        ORDER BY captured_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    captured_at = row[0]
    now_naive_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    freshness = max(
        0.0,
        (now_naive_utc - captured_at).total_seconds(),
    )
    return {
        "captured_at": captured_at.isoformat(),
        "freshness_seconds": round(freshness, 1),
        "boot_at": row[2].isoformat() if row[2] else None,
        "uptime_seconds": row[3],
        "internet_ok": (
            bool(row[4]) if row[4] is not None else None
        ),
        "connect_latency_ms": row[5],
        "wifi_state": row[6],
        "wifi_signal": row[7],
        "wifi_receive_mbps": row[8],
        "wifi_transmit_mbps": row[9],
    }


def pc_intelligence_summary():
    result = {
        "available": False,
        "source": None,
        "db_file": PC_DB.name,
        "latest_network": None,
        "last_internet_change": None,
        "error": None,
    }

    try:
        latest = state_network_summary()
        if latest:
            result["latest_network"] = latest
            result["available"] = True
            result["source"] = "collector_state"
    except Exception as exc:
        result["error"] = exc.__class__.__name__

    try:
        con = duckdb.connect(str(PC_DB), read_only=True)
        if result["latest_network"] is None:
            latest = duckdb_network_summary(con)
            if latest:
                result["latest_network"] = latest
                result["available"] = True
                result["source"] = "duckdb"

        changes = con.execute(
            """
            SELECT captured_at, changes_json
            FROM pc_state_events
            WHERE component = 'pc.activity-network'
              AND changes_json LIKE '%internetOk%'
            ORDER BY captured_at DESC
            LIMIT 1
            """
        ).fetchone()
        if changes:
            payload = {}
            try:
                payload = json.loads(changes[1] or "{}")
            except Exception:
                payload = {}
            internet_change = payload.get("internetOk")
            if isinstance(internet_change, dict):
                result["last_internet_change"] = {
                    "captured_at": changes[0].isoformat(),
                    "old": internet_change.get("old"),
                    "new": internet_change.get("new"),
                }
        con.close()
    except Exception as exc:
        if result["error"] is None:
            result["error"] = exc.__class__.__name__

    return result


def read_tail(path: Path, limit=MAX_LOG_BYTES):
    size = path.stat().st_size
    with path.open("rb") as handle:
        start = max(0, size - limit)
        # UTF-16 data must start on a two-byte boundary.
        if start % 2:
            start += 1
        if start:
            handle.seek(start)
        data = handle.read()

    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le", "replace")
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16-be", "replace")

    sample = data[:512]
    if (
        sample
        and sample.count(b"\x00") > len(sample) // 4
    ):
        return data.decode("utf-16-le", "replace")

    return data.decode("utf-8-sig", "replace")


def cloud_device_summary():
    result = {
        "available": False,
        "log_file": None,
        "size_bytes": None,
        "modified_at": None,
        "counts": {
            "ws_closed_1006": 0,
            "reconnect_requested": 0,
            "desktop_rpc_timeout": 0,
            "cloud_stable": 0,
            "desktop_mcp_connected": 0,
        },
        "error": None,
    }
    try:
        files = sorted(
            (
                p
                for p in CLOUD_LOG_DIR.glob("device-*.log")
                if p.is_file()
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return result

        path = files[0]
        text = read_tail(path)
        stat = path.stat()
        result.update(
            {
                "available": True,
                "log_file": path.name,
                "size_bytes": stat.st_size,
                "modified_at": utc_iso_from_timestamp(
                    stat.st_mtime
                ),
                "counts": {
                    "ws_closed_1006": text.count(
                        "[Cloud WS] closed code=1006"
                    ),
                    "reconnect_requested": text.count(
                        "Cloud connection closed; reconnecting"
                    ),
                    "desktop_rpc_timeout": text.count(
                        "DESKTOP_RPC_TIMEOUT"
                    ),
                    "cloud_stable": text.count(
                        "Cloud connection stable; reconnect backoff reset."
                    ),
                    "desktop_mcp_connected": text.count(
                        "Connected to Desktop Commander MCP"
                    ),
                },
            }
        )
    except Exception as exc:
        result["error"] = exc.__class__.__name__
    return result


def evidence():
    return {
        "ok": True,
        "version": VERSION,
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "runtime_uptime_seconds": runtime_uptime_seconds(),
        "internet_live": live_internet_probe(),
        "pc_intelligence": pc_intelligence_summary(),
        "cloud_dev": cloud_device_summary(),
        "security": {
            "docker_socket": False,
            "commander_control": False,
            "host_access": (
                "read-only PC Intelligence directory "
                "and Cloud Device logs only"
            ),
            "raw_log_content_returned": False,
        },
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": (
            "livingpanda-pc-intelligence-evidence-bridge"
        ),
        "version": VERSION,
    }


@app.get("/evidence")
def get_evidence():
    return evidence()

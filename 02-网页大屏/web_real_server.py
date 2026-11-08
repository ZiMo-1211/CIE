# -*- coding: utf-8 -*-
"""
Web real-device server for demo-real.

Run:
    python web_real_server.py

Open:
    http://127.0.0.1:8090
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from modbus_real_runtime import RealDeviceBridge
from modbus_real_shared import (
    COIL_KEYS,
    COIL_ZH_NAME,
    DEFAULT_POLL_INTERVAL_SEC,
    SCALE,
    TAGS,
    UNITS,
    SharedRealState,
    clamp_percent,
)

#HOST = "127.0.0.1"
HOST = "0.0.0.0"
PORT = 8090
STATIC_DIR = Path(__file__).parent / "webui"

STATE_STORE = SharedRealState()
BRIDGE = RealDeviceBridge(state_store=STATE_STORE, poll_interval_sec=DEFAULT_POLL_INTERVAL_SEC)


def build_status_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    sensor = snapshot.get("sensor", {})
    actuators = snapshot.get("actuators", {})
    values = sensor.get("values", [])

    pretty = [
        {
            "tag": TAGS[i],
            "value": round(float(values[i]) if i < len(values) else 0.0, 3),
            "unit": UNITS[i],
            "scale": SCALE[i],
        }
        for i in range(10)
    ]

    return {
        "success": True,
        "meta": {
            "updated_at": snapshot.get("meta", {}).get("updated_at"),
            "sensor_revision": int(snapshot.get("meta", {}).get("sensor_revision", 0)),
            "control_revision": int(snapshot.get("meta", {}).get("control_revision", 0)),
            "state_file": STATE_STORE.state_file,
            "runtime": "real-device",
        },
        "sensor": {
            "online": bool(sensor.get("online", False)),
            "values": values,
            "pretty": pretty,
            "updated_at": sensor.get("updated_at"),
            "last_source": sensor.get("last_source"),
        },
        "actuators": {
            "coils": {key: bool(actuators.get("coils", {}).get(key, False)) for key in COIL_KEYS},
            "coil_labels": dict(COIL_ZH_NAME),
            "brightness_percent": clamp_percent(actuators.get("brightness_percent", 0)),
            "fan_percent": clamp_percent(actuators.get("fan_percent", 0)),
            "updated_at": actuators.get("updated_at"),
            "last_source": actuators.get("last_source"),
        },
    }


class WebHandler(BaseHTTPRequestHandler):
    server_version = "ModbusRealWeb/1.0"

    def _send_json(self, data: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        content = file_path.read_bytes()
        suffix = file_path.suffix.lower()
        mime = "text/plain; charset=utf-8"
        if suffix == ".html":
            mime = "text/html; charset=utf-8"
        elif suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif suffix == ".js":
            mime = "application/javascript; charset=utf-8"
        elif suffix == ".json":
            mime = "application/json; charset=utf-8"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _status(self, refresh_device: bool = False) -> dict[str, Any]:
        snap = BRIDGE.get_status_snapshot(refresh_device=refresh_device)
        return build_status_payload(snap)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            query = parse_qs(parsed.query)
            refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
            self._send_json(self._status(refresh_device=refresh))
            return

        if path in ("/", ""):
            self._send_file(STATIC_DIR / "index.html")
            return

        safe_path = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(safe_path).startswith(str(STATIC_DIR.resolve())):
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        self._send_file(safe_path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except Exception as exc:
            self._send_json({"success": False, "error": f"invalid json: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            if path == "/api/control/coil":
                snap = BRIDGE.set_coil(payload.get("name", ""), bool(payload.get("enabled", False)), source="web-ui")
                self._send_json(build_status_payload(snap))
                return

            if path == "/api/control/brightness":
                snap = BRIDGE.set_brightness(float(payload.get("percent", 0.0)), source="web-ui")
                self._send_json(build_status_payload(snap))
                return

            if path == "/api/control/fan":
                snap = BRIDGE.set_fan(float(payload.get("percent", 0.0)), source="web-ui")
                self._send_json(build_status_payload(snap))
                return

            self._send_json({"success": False, "error": f"unknown path: {path}"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"success": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}")


def run_server() -> None:
    STATIC_DIR.mkdir(exist_ok=True)
    BRIDGE.start_polling()

    server = ThreadingHTTPServer((HOST, PORT), WebHandler)
    print(f"Real Web UI started: http://{HOST}:{PORT}")
    print(f"Shared state file: {STATE_STORE.state_file}")
    print(f"Poll interval: {DEFAULT_POLL_INTERVAL_SEC}s")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        BRIDGE.stop()
        server.server_close()


if __name__ == "__main__":
    run_server()

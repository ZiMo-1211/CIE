# -*- coding: utf-8 -*-
"""
Shared state and protocol constants for demo-real.

Protocol baseline follows modbus-demo2.py:
- 10-in-1 sensor registers: holding 0~9
- Coils: 0~3 => buzzer/green/yellow/red
- Analog outputs: holding register 6 (lamp brightness), 7 (fan speed)
- Analog raw range: 0~4095
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

STATE_FILE_ENV = "MODBUS_REAL_STATE_FILE"
DEFAULT_STATE_FILENAME = "modbus_real_state.json"

HOST_10IN1 = os.environ.get("MODBUS_10IN1_HOST", "10.60.0.222")
PORT_10IN1 = int(os.environ.get("MODBUS_10IN1_PORT", "502"))
HOST_AIMO = os.environ.get("MODBUS_AIMO_HOST", "10.60.0.223")
PORT_AIMO = int(os.environ.get("MODBUS_AIMO_PORT", "502"))


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except Exception:
        return int(default)

# From modbus-demo2.py
TAGS = ["二氧化碳", "TVOC总挥发物", "甲醛", "PM2.5", "湿度", "温度", "PM10", "PM1.0", "光照度", "噪声"]
SCALE = [1, 0.001, 0.001, 1, 0.015, 0.008, 1, 1, 1, 0.01]
UNITS = ["ppm", "mg/m³", "mg/m³", "μg/m³", "%RH", "℃", "μg/m³", "μg/m³", "Lux", "dB"]

COIL_KEYS = ("buzzer", "green", "yellow", "red")
COIL_ZH_NAME = {
    "buzzer": "蜂鸣器",
    "green": "绿灯",
    "yellow": "黄灯",
    "red": "红灯",
}
COIL_INDEX_TO_KEY = {0: "buzzer", 1: "green", 2: "yellow", 3: "red"}
COIL_KEY_TO_INDEX = {v: k for k, v in COIL_INDEX_TO_KEY.items()}

AO_BRIGHTNESS_REG = _int_env("MODBUS_AIMO_BRIGHTNESS_REG", 7)
AO_FAN_REG = _int_env("MODBUS_AIMO_FAN_REG", 6)
AO_RAW_MAX = max(1, _int_env("MODBUS_AIMO_ANALOG_RAW_MAX", 4095))

DEFAULT_POLL_INTERVAL_SEC = float(os.environ.get("MODBUS_REAL_POLL_INTERVAL_SEC", "0.8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp_percent(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def percent_to_raw(percent: float) -> int:
    return int(round(clamp_percent(percent) / 100.0 * AO_RAW_MAX))


def raw_to_percent(raw: int | float) -> float:
    return clamp_percent(float(raw) / float(AO_RAW_MAX) * 100.0)


def default_state() -> dict[str, Any]:
    return {
        "meta": {
            "version": 1,
            "updated_at": now_iso(),
            "sensor_revision": 0,
            "control_revision": 0,
        },
        "device": {
            "host_10in1": HOST_10IN1,
            "port_10in1": PORT_10IN1,
            "host_aimo": HOST_AIMO,
            "port_aimo": PORT_AIMO,
            "protocol": {
                "sensor_register_start": 0,
                "sensor_register_count": 10,
                "coil_start": 0,
                "coil_count": 4,
                "brightness_register": AO_BRIGHTNESS_REG,
                "fan_register": AO_FAN_REG,
                "analog_raw_max": AO_RAW_MAX,
            },
        },
        "sensor": {
            "online": False,
            "values": [0.0] * 10,
            "updated_at": now_iso(),
            "last_source": "init",
        },
        "actuators": {
            "coils": {key: False for key in COIL_KEYS},
            "brightness_percent": 0.0,
            "fan_percent": 0.0,
            "updated_at": now_iso(),
            "last_source": "init",
        },
    }


class SharedRealState:
    """JSON-backed shared state with file lock for cross-process sync."""

    def __init__(self, state_file: str | None = None):
        if state_file:
            self.state_file = os.path.abspath(state_file)
        else:
            env_file = os.environ.get(STATE_FILE_ENV, "").strip()
            if env_file:
                self.state_file = os.path.abspath(env_file)
            else:
                self.state_file = os.path.join(os.path.dirname(__file__), DEFAULT_STATE_FILENAME)
        self.lock_file = self.state_file + ".lock"

    def get_snapshot(self) -> dict[str, Any]:
        if not os.path.exists(self.state_file):
            state = default_state()
            self._write_state_without_lock(state)
            return state
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._merge_missing_fields(data)
        except Exception:
            state = default_state()
            self._write_state_without_lock(state)
            return state

    def update(self, mutator) -> dict[str, Any]:
        self._acquire_lock()
        try:
            state = self.get_snapshot()
            mutator(state)
            state["meta"]["updated_at"] = now_iso()
            self._write_state_without_lock(state)
            return deepcopy(state)
        finally:
            self._release_lock()

    def update_sensor_values(self, values: list[float], online: bool, source: str = "real-web") -> dict[str, Any]:
        values_copy = [float(v) for v in values[:10]]
        if len(values_copy) < 10:
            values_copy.extend([0.0] * (10 - len(values_copy)))

        def _mut(state: dict[str, Any]) -> None:
            state["sensor"]["online"] = bool(online)
            state["sensor"]["values"] = values_copy
            state["sensor"]["updated_at"] = now_iso()
            state["sensor"]["last_source"] = source
            state["meta"]["sensor_revision"] = int(state["meta"].get("sensor_revision", 0)) + 1

        return self.update(_mut)

    def set_coil(self, name: str, enabled: bool, source: str = "mcp") -> dict[str, Any]:
        key = str(name).strip().lower()
        if key not in COIL_KEYS:
            raise ValueError(f"未知线圈名称: {name}")

        def _mut(state: dict[str, Any]) -> None:
            state["actuators"]["coils"][key] = bool(enabled)
            state["actuators"]["updated_at"] = now_iso()
            state["actuators"]["last_source"] = source
            state["meta"]["control_revision"] = int(state["meta"].get("control_revision", 0)) + 1

        return self.update(_mut)

    def set_brightness(self, percent: float, source: str = "mcp") -> dict[str, Any]:
        val = clamp_percent(percent)

        def _mut(state: dict[str, Any]) -> None:
            state["actuators"]["brightness_percent"] = val
            state["actuators"]["updated_at"] = now_iso()
            state["actuators"]["last_source"] = source
            state["meta"]["control_revision"] = int(state["meta"].get("control_revision", 0)) + 1

        return self.update(_mut)

    def set_fan(self, percent: float, source: str = "mcp") -> dict[str, Any]:
        val = clamp_percent(percent)

        def _mut(state: dict[str, Any]) -> None:
            state["actuators"]["fan_percent"] = val
            state["actuators"]["updated_at"] = now_iso()
            state["actuators"]["last_source"] = source
            state["meta"]["control_revision"] = int(state["meta"].get("control_revision", 0)) + 1

        return self.update(_mut)

    def set_actuators_snapshot(
        self,
        coils: dict[str, bool],
        brightness_percent: float,
        fan_percent: float,
        source: str = "real-readback",
    ) -> dict[str, Any]:
        clean_coils = {key: bool(coils.get(key, False)) for key in COIL_KEYS}
        bright = clamp_percent(brightness_percent)
        fan = clamp_percent(fan_percent)

        def _mut(state: dict[str, Any]) -> None:
            state["actuators"]["coils"] = clean_coils
            state["actuators"]["brightness_percent"] = bright
            state["actuators"]["fan_percent"] = fan
            state["actuators"]["updated_at"] = now_iso()
            state["actuators"]["last_source"] = source

        return self.update(_mut)

    def _merge_missing_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        base = default_state()
        merged = deepcopy(base)
        for section in ("meta", "device", "sensor", "actuators"):
            if isinstance(data.get(section), dict):
                merged[section].update(data[section])

        coils = dict(base["actuators"]["coils"])
        user_coils = data.get("actuators", {}).get("coils", {})
        if isinstance(user_coils, dict):
            for key in COIL_KEYS:
                if key in user_coils:
                    coils[key] = bool(user_coils[key])
        merged["actuators"]["coils"] = coils

        values = data.get("sensor", {}).get("values", [])
        if isinstance(values, list):
            merged["sensor"]["values"] = [float(v) for v in values[:10]] + [0.0] * max(0, 10 - len(values))
            merged["sensor"]["values"] = merged["sensor"]["values"][:10]

        return merged

    def _write_state_without_lock(self, state: dict[str, Any]) -> None:
        state_dir = os.path.dirname(self.state_file) or "."
        os.makedirs(state_dir, exist_ok=True)
        temp_file = f"{self.state_file}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, self.state_file)

    def _acquire_lock(self, timeout_sec: float = 2.0, stale_sec: float = 15.0) -> None:
        start = time.time()
        while True:
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"{os.getpid()} {time.time()}")
                return
            except FileExistsError:
                try:
                    mtime = os.path.getmtime(self.lock_file)
                    if (time.time() - mtime) > stale_sec:
                        os.remove(self.lock_file)
                        continue
                except FileNotFoundError:
                    continue
                if (time.time() - start) >= timeout_sec:
                    raise TimeoutError(f"获取状态锁超时: {self.lock_file}")
                time.sleep(0.05)

    def _release_lock(self) -> None:
        try:
            os.remove(self.lock_file)
        except FileNotFoundError:
            pass



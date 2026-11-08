# -*- coding: utf-8 -*-
"""Runtime bridge for real Modbus devices used by web + MCP."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from pymodbus.client import ModbusTcpClient

from modbus_real_shared import (
    AO_BRIGHTNESS_REG,
    AO_FAN_REG,
    COIL_KEYS,
    COIL_KEY_TO_INDEX,
    DEFAULT_POLL_INTERVAL_SEC,
    HOST_10IN1,
    HOST_AIMO,
    PORT_10IN1,
    PORT_AIMO,
    SCALE,
    SharedRealState,
    clamp_percent,
    percent_to_raw,
    raw_to_percent,
)


class RealDeviceBridge:
    """Thread-safe bridge with persistent Modbus connections."""

    def __init__(self, state_store: SharedRealState | None = None, poll_interval_sec: float | None = None):
        self.state_store = state_store or SharedRealState()
        self.poll_interval_sec = float(poll_interval_sec or DEFAULT_POLL_INTERVAL_SEC)

        self.sensor_client = ModbusTcpClient(HOST_10IN1, port=PORT_10IN1, timeout=2)
        self.aimo_client = ModbusTcpClient(HOST_AIMO, port=PORT_AIMO, timeout=2)

        self._sensor_lock = threading.Lock()
        self._actuator_lock = threading.Lock()
        self._poll_thread: threading.Thread | None = None
        self._running = False

        self.logger = logging.getLogger("RealDeviceBridge")
        self.logger.info(
            "bridge init: sensor=%s:%s aimo=%s:%s brightness_reg=%s fan_reg=%s",
            HOST_10IN1,
            PORT_10IN1,
            HOST_AIMO,
            PORT_AIMO,
            AO_BRIGHTNESS_REG,
            AO_FAN_REG,
        )

    def start_polling(self) -> None:
        if self._running:
            return
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)

        with self._sensor_lock:
            self.sensor_client.close()
        with self._actuator_lock:
            self.aimo_client.close()

    def get_status_snapshot(self, refresh_device: bool = False) -> dict[str, Any]:
        if refresh_device:
            return self.refresh_from_device()
        return self.state_store.get_snapshot()

    def refresh_from_device(self) -> dict[str, Any]:
        # Sensor path and actuator path are decoupled to avoid control latency.
        with self._sensor_lock:
            sensor_online, values = self._read_sensor_locked()
        snap = self.state_store.update_sensor_values(values, online=sensor_online, source="real-bridge")

        current_act = snap.get("actuators", {})
        with self._actuator_lock:
            ok_act, actuators = self._read_actuators_locked(current_act)
        if ok_act:
            snap = self.state_store.set_actuators_snapshot(
                coils=actuators["coils"],
                brightness_percent=actuators["brightness_percent"],
                fan_percent=actuators["fan_percent"],
                source="real-bridge-readback",
            )
        return snap

    def set_coil(self, name: str, enabled: bool, source: str = "web-ui") -> dict[str, Any]:
        key = str(name).strip().lower()
        if key not in COIL_KEYS:
            raise ValueError(f"未知线圈名称: {name}")

        with self._actuator_lock:
            if not self._write_coil_with_retry(COIL_KEY_TO_INDEX[key], bool(enabled)):
                raise RuntimeError(f"{key} 线圈写入失败")

        return self.state_store.set_coil(key, bool(enabled), source=source)

    def set_brightness(self, percent: float, source: str = "web-ui") -> dict[str, Any]:
        value = clamp_percent(percent)
        raw = percent_to_raw(value)

        with self._actuator_lock:
            if not self._write_register_with_retry(AO_BRIGHTNESS_REG, raw):
                raise RuntimeError("小灯亮度写入失败")

        return self.state_store.set_brightness(value, source=source)

    def set_fan(self, percent: float, source: str = "web-ui") -> dict[str, Any]:
        value = clamp_percent(percent)
        raw = percent_to_raw(value)

        with self._actuator_lock:
            if not self._write_register_with_retry(AO_FAN_REG, raw):
                raise RuntimeError("风扇转速写入失败")

        return self.state_store.set_fan(value, source=source)

    def _ensure_connected(self, client: ModbusTcpClient) -> bool:
        if client.is_socket_open():
            return True
        try:
            return bool(client.connect())
        except Exception:
            return False

    def _reconnect(self, client: ModbusTcpClient) -> bool:
        try:
            client.close()
        except Exception:
            pass
        try:
            return bool(client.connect())
        except Exception:
            return False

    def _write_coil_with_retry(self, addr: int, value: bool) -> bool:
        if not self._ensure_connected(self.aimo_client):
            self.logger.warning("write coil failed: connect error addr=%s value=%s", addr, value)
            return False

        try:
            wr = self.aimo_client.write_coil(addr, value)
            if not wr.isError():
                return True
            self.logger.warning("write coil first attempt failed addr=%s value=%s", addr, value)
        except Exception as exc:
            self.logger.warning("write coil exception on first attempt addr=%s value=%s err=%s", addr, value, exc)

        if not self._reconnect(self.aimo_client):
            return False

        try:
            wr = self.aimo_client.write_coil(addr, value)
            return not wr.isError()
        except Exception as exc:
            self.logger.warning("write coil retry failed addr=%s value=%s err=%s", addr, value, exc)
            return False

    def _write_register_with_retry(self, addr: int, value: int) -> bool:
        if not self._ensure_connected(self.aimo_client):
            self.logger.warning("write register failed: connect error addr=%s value=%s", addr, value)
            return False

        try:
            wr = self.aimo_client.write_register(addr, value)
            if not wr.isError():
                return True
            self.logger.warning("write register first attempt failed addr=%s value=%s", addr, value)
        except Exception as exc:
            self.logger.warning("write register exception on first attempt addr=%s value=%s err=%s", addr, value, exc)

        if not self._reconnect(self.aimo_client):
            return False

        try:
            wr = self.aimo_client.write_register(addr, value)
            return not wr.isError()
        except Exception as exc:
            self.logger.warning("write register retry failed addr=%s value=%s err=%s", addr, value, exc)
            return False

    def _read_sensor_locked(self) -> tuple[bool, list[float]]:
        if not self._ensure_connected(self.sensor_client):
            return False, [0.0] * 10
        try:
            rr = self.sensor_client.read_holding_registers(0, count=10)
            if rr.isError():
                return False, [0.0] * 10
            values = [raw * scale for raw, scale in zip(rr.registers, SCALE)]
            if len(values) < 10:
                values += [0.0] * (10 - len(values))
            return True, values[:10]
        except Exception:
            try:
                self.sensor_client.close()
            except Exception:
                pass
            return False, [0.0] * 10

    def _read_analog_pair(self) -> tuple[bool, float, float]:
        start = min(AO_BRIGHTNESS_REG, AO_FAN_REG)
        count = abs(AO_BRIGHTNESS_REG - AO_FAN_REG) + 1
        rr_regs = self.aimo_client.read_holding_registers(start, count=count)
        if rr_regs.isError() or len(rr_regs.registers) < count:
            return False, 0.0, 0.0

        bright_idx = AO_BRIGHTNESS_REG - start
        fan_idx = AO_FAN_REG - start
        bright = raw_to_percent(rr_regs.registers[bright_idx])
        fan = raw_to_percent(rr_regs.registers[fan_idx])
        return True, bright, fan

    def _read_actuators_locked(self, fallback: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        if not self._ensure_connected(self.aimo_client):
            return False, {}

        # Keep last known values when readback partially fails.
        coils = {key: bool(fallback.get("coils", {}).get(key, False)) for key in COIL_KEYS}
        bright = clamp_percent(fallback.get("brightness_percent", 0.0))
        fan = clamp_percent(fallback.get("fan_percent", 0.0))

        fallback_nonzero = (bright > 1.0) or (fan > 1.0)

        coils_ok = False
        regs_ok = False

        try:
            rr_coils = self.aimo_client.read_coils(0, count=4)
            if not rr_coils.isError():
                bits = rr_coils.bits or []
                if len(bits) >= 4:
                    for key in COIL_KEYS:
                        idx = COIL_KEY_TO_INDEX[key]
                        coils[key] = bool(bits[idx])
                    coils_ok = True
        except Exception as exc:
            self.logger.debug("read coils failed: %s", exc)

        try:
            ok1, b1, f1 = self._read_analog_pair()
            if ok1:
                # Some devices may occasionally report transient zero for both channels.
                # Guard by confirming once more if previous state is non-zero.
                if fallback_nonzero and (b1 < 0.5 and f1 < 0.5):
                    ok2, b2, f2 = self._read_analog_pair()
                    if ok2 and not (b2 < 0.5 and f2 < 0.5):
                        b1, f1 = b2, f2
                    elif ok2 and (b2 < 0.5 and f2 < 0.5):
                        self.logger.warning(
                            "actuator readback zero confirmed twice (accept): b=%.2f f=%.2f", b2, f2
                        )
                    else:
                        self.logger.warning(
                            "actuator transient zero ignored: keep fallback b=%.2f f=%.2f", bright, fan
                        )
                        ok1 = False

                if ok1:
                    bright, fan = b1, f1
                    regs_ok = True
        except Exception as exc:
            self.logger.debug("read actuator regs failed: %s", exc)

        if not (coils_ok or regs_ok):
            return False, {}

        return True, {
            "coils": coils,
            "brightness_percent": bright,
            "fan_percent": fan,
        }

    def _poll_loop(self) -> None:
        while self._running:
            try:
                self.refresh_from_device()
                # Keep active session to reduce timeout disconnect on some devices.
                with self._actuator_lock:
                    if self.aimo_client.is_socket_open():
                        try:
                            self.aimo_client.read_holding_registers(AO_BRIGHTNESS_REG, count=1)
                        except Exception:
                            self.aimo_client.close()
            except Exception as exc:
                self.logger.warning("poll loop error: %s", exc)

            time.sleep(max(0.2, self.poll_interval_sec))

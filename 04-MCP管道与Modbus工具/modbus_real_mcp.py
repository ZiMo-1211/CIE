# -*- coding: utf-8 -*-
"""MCP tools for demo-real (real device)."""

from __future__ import annotations

import atexit
import logging
import os
import re
import sys
from typing import Any

from fastmcp import FastMCP

from modbus_real_runtime import RealDeviceBridge
from modbus_real_shared import (
    COIL_KEYS,
    COIL_ZH_NAME,
    TAGS,
    UNITS,
    SharedRealState,
    clamp_percent,
    now_iso,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ModbusRealMCP")

if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

mcp = FastMCP("ModbusRealControl")
state_store = SharedRealState()
bridge = RealDeviceBridge(state_store=state_store)

COIL_NAME_ALIASES = {
    "buzzer": "buzzer",
    "beeper": "buzzer",
    "蜂鸣器": "buzzer",
    "green": "green",
    "greenlight": "green",
    "绿灯": "green",
    "yellow": "yellow",
    "yellowlight": "yellow",
    "黄灯": "yellow",
    "red": "red",
    "redlight": "red",
    "红灯": "red",
}

_bridge_started = False
MCP_ENABLE_POLLER = os.environ.get("MODBUS_REAL_MCP_ENABLE_POLLER", "0").strip().lower() in ("1", "true", "yes")


def ensure_bridge_started() -> None:
    global _bridge_started
    if _bridge_started:
        return
    if MCP_ENABLE_POLLER:
        bridge.start_polling()
        logger.info("MCP poller enabled")
    else:
        logger.info("MCP poller disabled (web side should own polling)")
    _bridge_started = True


def _console_event(message: str) -> None:
    ts = now_iso()
    line = f"[ModbusRealMCP][{ts}] {message}"
    print(line, file=sys.stderr, flush=True)
    logger.info(line)


def _status_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    sensor = snapshot.get("sensor", {})
    actuators = snapshot.get("actuators", {})
    coils = actuators.get("coils", {})
    values = sensor.get("values", [])

    pretty_sensor = []
    for i, tag in enumerate(TAGS):
        value = float(values[i]) if i < len(values) else 0.0
        pretty_sensor.append({"tag": tag, "value": value, "unit": UNITS[i]})

    return {
        "success": True,
        "meta": snapshot.get("meta", {}),
        "device": snapshot.get("device", {}),
        "sensor": {
            "online": bool(sensor.get("online", False)),
            "values": values,
            "pretty": pretty_sensor,
            "updated_at": sensor.get("updated_at"),
            "last_source": sensor.get("last_source"),
        },
        "actuators": {
            "coils": {key: bool(coils.get(key, False)) for key in COIL_KEYS},
            "brightness_percent": clamp_percent(actuators.get("brightness_percent", 0)),
            "fan_percent": clamp_percent(actuators.get("fan_percent", 0)),
            "updated_at": actuators.get("updated_at"),
            "last_source": actuators.get("last_source"),
        },
    }


def _get_status(refresh_device: bool = True) -> dict[str, Any]:
    ensure_bridge_started()
    snap = bridge.get_status_snapshot(refresh_device=refresh_device)
    return _status_view(snap)


def _normalize_coil_name(text: str) -> str | None:
    lower = text.strip().lower().replace(" ", "")
    if lower in COIL_NAME_ALIASES:
        return COIL_NAME_ALIASES[lower]
    for alias, key in COIL_NAME_ALIASES.items():
        if alias in lower:
            return key
    return None


def _normalize_switch_action(text: str) -> bool | None:
    lower = text.strip().lower()
    on_keywords = ("打开", "开启", "on", "true", "enable")
    off_keywords = ("关闭", "关掉", "off", "false", "disable")
    if any(k in lower for k in on_keywords):
        return True
    if any(k in lower for k in off_keywords):
        return False
    return None


def _set_coil(name: str, enabled: bool) -> dict[str, Any]:
    ensure_bridge_started()
    snap = bridge.set_coil(name, enabled, source="real-mcp")
    key = str(name).strip().lower()
    return {
        "success": True,
        "message": f"{COIL_ZH_NAME.get(key, key)}已设置为{'开' if enabled else '关'}",
        "status": _status_view(snap)["actuators"],
    }


def _set_brightness(percent: float) -> dict[str, Any]:
    ensure_bridge_started()
    value = clamp_percent(percent)
    snap = bridge.set_brightness(value, source="real-mcp")
    return {
        "success": True,
        "message": f"小灯亮度已设置为 {value:.1f}%",
        "status": _status_view(snap)["actuators"],
    }


def _set_fan(percent: float) -> dict[str, Any]:
    ensure_bridge_started()
    value = clamp_percent(percent)
    snap = bridge.set_fan(value, source="real-mcp")
    return {
        "success": True,
        "message": f"风扇转速已设置为 {value:.1f}%",
        "status": _status_view(snap)["actuators"],
    }


def execute_control_command(command: str) -> dict[str, Any]:
    text = str(command).strip()
    if not text:
        return {"success": False, "error": "命令不能为空"}

    lower = text.lower()
    if any(k in lower for k in ("状态", "status", "查询", "查看")):
        _console_event(f"control command parsed: query status ({text})")
        return _get_status(refresh_device=True)

    if ("亮度" in text) or ("brightness" in lower) or ("小灯" in text):
        m = re.search(r"(-?\d+(?:\.\d+)?)", text)
        if not m:
            return {"success": False, "error": "未识别到亮度数值，例如：亮度 60"}
        _console_event(f"control command parsed: brightness={m.group(1)}")
        return _set_brightness(float(m.group(1)))

    if ("风扇" in text) or ("fan" in lower):
        m = re.search(r"(-?\d+(?:\.\d+)?)", text)
        if not m:
            return {"success": False, "error": "未识别到风扇数值，例如：风扇 35"}
        _console_event(f"control command parsed: fan={m.group(1)}")
        return _set_fan(float(m.group(1)))

    coil_name = _normalize_coil_name(text)
    action = _normalize_switch_action(text)
    if coil_name is not None and action is not None:
        _console_event(f"control command parsed: coil={coil_name}, action={'ON' if action else 'OFF'}")
        return _set_coil(coil_name, action)

    return {
        "success": False,
        "error": "无法解析命令。示例：打开绿灯、关闭红灯、亮度60、风扇35、查询状态",
    }


@mcp.tool()
def modbus_real_get_status(refresh_device: bool = True) -> dict:
    """查询真实状态。refresh_device=True 时先拉取设备最新值。"""

    try:
        result = _get_status(refresh_device=refresh_device)
        _console_event(f"tool called: modbus_real_get_status(refresh_device={refresh_device})")
        return result
    except Exception as exc:
        logger.exception("modbus_real_get_status failed: %s", exc)
        return {"success": False, "error": str(exc)}


@mcp.tool()
def modbus_real_set_coil(name: str, enabled: bool) -> dict:
    """控制蜂鸣器/绿灯/黄灯/红灯。name: buzzer|green|yellow|red。"""

    try:
        _console_event(f"tool called: modbus_real_set_coil(name={name}, enabled={enabled})")
        return _set_coil(name, enabled)
    except Exception as exc:
        logger.exception("modbus_real_set_coil failed: %s", exc)
        return {"success": False, "error": str(exc)}


@mcp.tool()
def modbus_real_set_brightness(percent: float) -> dict:
    """设置小灯亮度百分比（0~100）。"""

    try:
        _console_event(f"tool called: modbus_real_set_brightness(percent={percent})")
        return _set_brightness(percent)
    except Exception as exc:
        logger.exception("modbus_real_set_brightness failed: %s", exc)
        return {"success": False, "error": str(exc)}


@mcp.tool()
def modbus_real_set_fan(percent: float) -> dict:
    """设置风扇转速百分比（0~100）。"""

    try:
        _console_event(f"tool called: modbus_real_set_fan(percent={percent})")
        return _set_fan(percent)
    except Exception as exc:
        logger.exception("modbus_real_set_fan failed: %s", exc)
        return {"success": False, "error": str(exc)}


@mcp.tool()
def modbus_real_control(command: str) -> dict:
    """
    自然语言控制入口。
    示例：打开绿灯 / 关闭蜂鸣器 / 亮度60 / 风扇35 / 查询状态
    """

    try:
        _console_event(f"tool called: modbus_real_control(command={command})")
        return execute_control_command(command)
    except Exception as exc:
        logger.exception("modbus_real_control failed: %s", exc)
        return {"success": False, "error": str(exc)}


def _cleanup() -> None:
    try:
        bridge.stop()
    except Exception:
        pass


atexit.register(_cleanup)


if __name__ == "__main__":
    logger.info("Starting ModbusReal MCP server in stdio mode")
    ensure_bridge_started()
    mcp.run(transport="stdio")



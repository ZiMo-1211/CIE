#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""让 video_real12.py **原封不动**跑在 Linux（K1 / riscv64）上。

原文件里有两处 Windows 专用假设：

    video_real12.py:110    serialPort = "COM7"
    video_real12.py:401    self.cap = cv2.VideoCapture(0)

本启动器在导入原程序之前把这两个接口包一层，然后用 runpy 以 __main__ 身份执行
原文件，所以原程序的解析、聚类、GUI、文件保存行为全都完全不变 —— 原文件一个
字节都没有被修改（可用 md5sum 核对）。

环境变量：
    MMWAVE_SERIAL   串口设备，默认 /dev/ttyUSB0（对应原程序里的 "COM7"）
    MMWAVE_CAMERA   摄像头设备，默认空 = 保持原程序的 VideoCapture(0) 不变
    MMWAVE_BAUD     可选，覆盖波特率（原程序写死 2000000，正常不需要动）
    MMWAVE_SYNTH_CAM=1  仅用于"板子上还没有摄像头时"验证链路：
                    用一个合成画面顶替摄像头（画面上有走动的圆点和帧号）。
                    真机运行不要设这个变量。
"""
import glob
import os
import runpy
import subprocess
import sys

import cv2
import numpy as np
import serial

HERE = os.path.dirname(os.path.abspath(__file__))


def _probe(cmd, timeout=40):
    """跑一个探测脚本，返回它打印的设备路径（没有就返回空）。"""
    path = os.path.join(HERE, cmd)
    if not os.path.exists(path):
        return ""
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip().splitlines()[-1].strip() if r.stdout.strip() else ""
    except Exception:
        return ""


def _pick_serial():
    """没设 MMWAVE_SERIAL 就自己找：先按报文包头探测，再退回 /dev/ttyACM*。"""
    env = os.environ.get("MMWAVE_SERIAL", "").strip()
    if env:
        return env
    dev = _probe("pick_serial.py")
    if dev:
        print(f"[launcher] 自动探测串口 {dev}", flush=True)
        return dev
    for pat in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        got = sorted(glob.glob(pat))
        if got:
            print(f"[launcher] 串口退回 {got[0]}（没探测到主语数据流）", flush=True)
            return got[0]
    return "/dev/ttyUSB0"          # 都没有，保持原来的报错提示


def _pick_camera():
    """没设 MMWAVE_CAMERA 就自己找真正能读帧的 /dev/video*。"""
    env = os.environ.get("MMWAVE_CAMERA", "").strip()
    if env.startswith("/dev/"):
        return env
    if env:
        # 环境里给了 "0" 这类镜头序号：K1 上 VideoCapture(0) 不是摄像头，
        # 会变成 GStreamer "no element 0"，所以宁可当成没设。
        print(f"[launcher] 忽略 MMWAVE_CAMERA={env!r}（不是 /dev/ 设备节点）",
              flush=True)
    dev = _probe("pick_camera.py")
    # 只接受真正的设备节点；探测脚本偶尔会吐 "0" 这种镜头序号，
    # 直接给 cv2 会变成 GStreamer "no element 0" 然后整个采集起不来。
    if dev and dev.startswith("/dev/"):
        print(f"[launcher] 自动探测摄像头 {dev}", flush=True)
        return dev
    if dev:
        print(f"[launcher] 探测结果 {dev!r} 不是设备节点，忽略，自己扫", flush=True)
    for path in _scan_video_devices():
        print(f"[launcher] 摄像头退回 {path}", flush=True)
        return path
    print("[launcher] 没找到能读帧的摄像头：本次只显示点云（插上后重启即可）",
          flush=True)
    return ""


def _scan_video_devices():
    """按 /dev/videoN 顺序试读一帧，能读的返回路径列表。"""
    def num(p):
        try:
            return int(p[len("/dev/video"):])
        except ValueError:
            return 9999

    good = []
    for path in sorted(glob.glob("/dev/video*"), key=num):
        try:
            cap = cv2.VideoCapture(path)
            ok = cap.isOpened()
            if ok:
                ok, frame = cap.read()
                ok = bool(ok) and frame is not None and getattr(frame, "size", 0) > 0
            cap.release()
            if ok:
                good.append(path)
        except Exception:
            pass
    return good


SERIAL_DEV = _pick_serial()
CAMERA_DEV = _pick_camera()


def _patch_serial():
    """把 "COMx" 换成真实设备节点。"""
    real = serial.Serial

    class Serial(real):
        def __init__(self, *args, **kwargs):
            port = kwargs.get("port", args[0] if args else None)
            if isinstance(port, str) and port.upper().startswith("COM"):
                print(f"[launcher] 串口 {port} -> {SERIAL_DEV}", flush=True)
                kwargs["port"] = SERIAL_DEV
                args = ()
            baud = os.environ.get("MMWAVE_BAUD")
            if baud and not args and kwargs.get("baudrate") is None:
                kwargs["baudrate"] = int(baud)
                print(f"[launcher] 波特率 -> {baud}", flush=True)
            super().__init__(*args, **kwargs)

    serial.Serial = Serial


def _patch_camera():
    """VideoCapture(0) 在 K1 上不是摄像头，按需替换成真实节点。"""
    if not CAMERA_DEV:
        return
    real = cv2.VideoCapture

    def VideoCapture(src=None, *a, **kw):
        if src is None or (isinstance(src, int) and src == 0):
            print(f"[launcher] 摄像头 {src} -> {CAMERA_DEV}", flush=True)
            src = CAMERA_DEV
        return real(src, *a, **kw)

    cv2.VideoCapture = VideoCapture


def _patch_synth_camera():
    """仅用于无摄像头硬件的链路验证（MMWAVE_SYNTH_CAM=1）。"""
    import math
    import time as _time

    class SynthCamera:
        """只实现原程序用到的 isOpened() / read() / release()。"""

        def __init__(self, *a, **kw):
            self.n = 0
            self.t0 = _time.time()
            print("[launcher] !! 使用合成摄像头（仅验证用）", flush=True)

        def isOpened(self):
            return True

        def read(self):
            self.n += 1
            w, h = 640, 480
            img = np.zeros((h, w, 3), np.uint8)
            t = self.n * 0.1
            cx = int(w / 2 + 180 * math.sin(t))
            cy = int(h / 2 + 90 * math.cos(t * 1.7))
            cv2.circle(img, (cx, cy), 40, (60, 200, 255), -1)
            cv2.putText(img, f"SYNTH {self.n}", (16, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            cv2.putText(img, f"t={_time.time() - self.t0:.1f}s", (16, 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
            return True, img

        def set(self, *a, **kw):
            return True

        def release(self):
            return None

    cv2.VideoCapture = SynthCamera


def main():
    _patch_serial()
    if os.environ.get("MMWAVE_SYNTH_CAM") == "1":
        _patch_synth_camera()
    else:
        _patch_camera()
    entry = os.environ.get("MMWAVE_ENTRY", "video_real12.py").strip() or "video_real12.py"
    target = entry if os.path.isabs(entry) else os.path.join(HERE, entry)
    if not os.path.exists(target):
        sys.exit(f"[launcher] 找不到 {target}")
    print(f"[launcher] 运行 {target}", flush=True)
    print(f"[launcher] 串口={SERIAL_DEV}  摄像头={CAMERA_DEV or '原程序默认(0)'}", flush=True)
    runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
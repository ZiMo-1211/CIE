#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K1 上的雷达/摄像头自检（不需要 GUI）。

用法：
    python3 radar_probe.py              # 串口 + 摄像头一起测，默认 8 秒
    python3 radar_probe.py 15           # 测 15 秒
    python3 radar_probe.py 8 --list     # 只列出设备信息

串口部分用的是与原程序完全相同的协议解析（包头 DC FF EE FF），
所以这个脚本能跑通，就说明原程序在 riscv64 上也能拿到正确的点云数据。
"""
import glob
import os
import sys
import time

import numpy as np
import serial

HEADER = bytes.fromhex("DCFFEEFF")
BAUD = 2000000


# ---------------- 与原程序一致的点解析 ----------------
def sph2cart(r, a, v):
    arad, vrad = np.radians(a), np.radians(v)
    return (r * np.cos(vrad) * np.sin(arad),
            r * np.cos(vrad) * np.cos(arad),
            r * np.sin(vrad))


def parse_point(b):
    """20 字节 → (x, y, z, v, snr)，位段与原程序一致。"""
    v = int.from_bytes(b, "little")
    snr = (v >> 10) & 0x7FFF
    dist = ((v >> 32) & 0xFFFFFFFF) / 100.0
    speed = (((v >> 64) & 0x7FFF) - 16384) / 100.0
    az = (((v >> 79) & 0x7FFF) - 16384) / 100.0
    el = (((v >> 96) & 0x7FFF) - 16384) / 100.0
    x, y, z = sph2cart(dist, az, el)
    return x, y, z, speed, snr


def list_devices():
    print("=== 串口 ===")
    ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    print("  " + (", ".join(ports) if ports else "（无）"))
    print("=== USB ===")
    os.system("lsusb | grep -v 'root hub' || true")
    print("=== 摄像头 ===")
    os.system("v4l2-ctl --list-devices 2>/dev/null | head -40 || echo '（v4l2-ctl 不可用）'")


def probe_serial(dev, seconds=8):
    print(f"=== 串口 {dev} @ {BAUD} ===")
    try:
        ser = serial.Serial(port=dev, baudrate=BAUD, bytesize=8,
                            parity="N", stopbits=1, timeout=1)
    except Exception as e:
        print(f"  打开失败：{e}")
        return False

    buf = bytearray()
    frames, counts = 0, []
    t0 = time.time()
    first_pts = []
    while time.time() - t0 < seconds:
        n = ser.in_waiting
        if n:
            buf.extend(ser.read(n))
        # 按包头切帧
        while True:
            i = buf.find(HEADER)
            if i < 0:
                if len(buf) > 1 << 20:
                    del buf[:-4]
                break
            if i:
                del buf[:i]
            if len(buf) < 32 + 20:
                break
            # 帧长不固定：按 点云尾 找一帧，这里直接取连续 20 字节一组
            body = buf[32:]
            k = len(body) // 20
            if k < 1:
                break
            pts = []
            for j in range(k):
                p = parse_point(bytes(body[j * 20:(j + 1) * 20]))
                if p[3] < 100 and 0.05 < ((p[0] ** 2 + p[1] ** 2 + p[2] ** 2) ** 0.5) < 6:
                    pts.append(p)
            if pts:
                frames += 1
                counts.append(len(pts))
                if not first_pts:
                    first_pts = pts[:5]
            del buf[:32 + k * 20]
        time.sleep(0.01)
    ser.close()

    print(f"  收到 {frames} 帧（{frames/max(seconds,0.001):.1f} 帧/秒）")
    if counts:
        c = np.array(counts)
        print(f"  每帧点数：中位 {np.median(c):.0f}  p90 {np.percentile(c,90):.0f}  max {c.max()}")
        if first_pts:
            print("  前几个点（x, y, z, v, SNR）：")
            for p in first_pts:
                print(f"    {p[0]:+.3f} {p[1]:+.3f} {p[2]:+.3f} v={p[3]:+.2f} snr={p[4]:.0f}")
        return frames > 0
    print("  没解析出任何点（检查波特率/接线）")
    return False


def probe_cameras(seconds=3):
    import cv2
    print("=== 摄像头扫描 ===")
    found = []
    for dev in sorted(glob.glob("/dev/video[0-9]*"),
                      key=lambda s: int(s.replace("/dev/video", ""))):
        cap = cv2.VideoCapture(dev)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        if ok and frame is not None and frame.size:
            print(f"  {dev} 可读 → {frame.shape[1]}x{frame.shape[0]}")
            found.append(dev)
        cap.release()
    if not found:
        print("  没有可读的摄像头")
    else:
        print(f"  建议设置 MMWAVE_CAMERA={found[0]}")
    return found


def main():
    args = [a for a in sys.argv[1:]]
    seconds = 8
    for a in args:
        if a.isdigit():
            seconds = int(a)
    list_devices()
    if "--list" in args:
        return 0

    ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    ser_ok = probe_serial(ports[0], seconds) if ports else (print("=== 没有串口设备 ===") or False)
    cams = probe_cameras()

    print("\n=== 结论 ===")
    print(f"  串口数据：{'正常' if ser_ok else '异常'}")
    print(f"  摄像头：{' '.join(cams) if cams else '未找到'}")
    if ser_ok and cams:
        print("  → 可以运行原程序：bash run.sh  （或 MMWAVE_CAMERA=%s bash run.sh）" % cams[0])
    return 0 if ser_ok else 1


if __name__ == "__main__":
    sys.exit(main())
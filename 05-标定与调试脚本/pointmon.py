#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时统计雷达每秒的点数（不依赖 GUI），用于确认"雷达到底能不能看到东西"。

    python3 pointmon.py /dev/ttyACM0 120 > /tmp/pointmon.log 2>&1 &

每秒一行：帧数 / 非零帧 / 点数最大值 / 距离范围
"""
import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
BAUD = 2000000

H = bytes.fromhex("DCFFEEFF")
S = bytes.fromhex("CBFEDDFF")
E = bytes.fromhex("C4FEDDFF")


def sph2cart(r, a, v):
    import math
    arad, vrad = math.radians(a), math.radians(v)
    return (r * math.cos(vrad) * math.sin(arad),
            r * math.cos(vrad) * math.cos(arad),
            r * math.sin(vrad))


def frm_stats(body):
    """body: CBFEDDFF 与 C4FEDDFF 之间的 hex；返回 (点数, 最近距离, 最远距离)"""
    n = len(body) // 20
    dmin = dmax = None
    for i in range(n):
        chunk = body[i * 20:(i + 1) * 20]
        bits = "".join(f"{int(c, 16):04b}" for c in bytes.fromhex(chunk)[::-1].hex())
        dist = int(bits[-64:-32], 2) / 100.0
        if dist > 0:
            dmin = dist if dmin is None else min(dmin, dist)
            dmax = dist if dmax is None else max(dmax, dist)
    return n, dmin, dmax


def main():
    try:
        ser = serial.Serial(port, BAUD, timeout=0.2)
    except Exception as e:
        print(f"打开 {port} 失败：{e}", flush=True)
        return 1
    print(f"# 监听 {port} @ {BAUD}，共 {seconds} 秒", flush=True)
    print("# 时间      帧数  非零帧  最大点数  最近(m)  最远(m)", flush=True)

    buf = b""
    t_start = time.time()
    sec_frames = sec_nonzero = sec_maxpts = 0
    dmin_all = dmax_all = None
    t_next = t_start + 1.0

    while time.time() - t_start < seconds:
        chunk = ser.read(8192)
        if chunk:
            buf += chunk
        while True:
            i = buf.find(H)
            if i < 0:
                if len(buf) > 65536:
                    buf = buf[-4096:]
                break
            if i > 0:
                buf = buf[i:]
            j = buf.find(E)
            if j < 0:
                break
            packet = buf[:j + 4]
            buf = buf[j + 4:]
            k = packet.find(S)
            body = packet[k + 4:j].hex() if k >= 0 else ""
            n, dmin, dmax = frm_stats(body)
            sec_frames += 1
            if n > 0:
                sec_nonzero += 1
            sec_maxpts = max(sec_maxpts, n)
            if dmin is not None:
                dmin_all = dmin if dmin_all is None else min(dmin_all, dmin)
                dmax_all = dmax if dmax_all is None else max(dmax_all, dmax)

        now = time.time()
        if now >= t_next:
            print(f"{time.strftime('%H:%M:%S')}  {sec_frames:4d}  {sec_nonzero:5d}  "
                  f"{sec_maxpts:7d}  "
                  f"{(f'{dmin_all:.2f}' if dmin_all else '-'):>7}  "
                  f"{(f'{dmax_all:.2f}' if dmax_all else '-'):>7}", flush=True)
            sec_frames = sec_nonzero = sec_maxpts = 0
            dmin_all = dmax_all = None
            t_next += 1.0
    ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
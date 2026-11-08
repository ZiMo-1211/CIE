#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""虚拟串口重放：让原程序在没有雷达硬件时也能完整跑起来（链路验证用）。

用 mkframe.py 按原程序协议编码"站立的人"点云，以 10 帧/秒写进 PTY，
原程序（video_real12.py 原封不动）从 PTY 读数据、解析、聚类、显示。

用法（在容器里）：
    python3 replay_serial.py            # 只建虚拟串口并持续重放
    python3 replay_serial.py --run      # 建虚拟串口 + 用它启动原程序
"""
import math
import os
import pty
import subprocess
import sys
import threading
import time
import tty

import mkframe

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = False


def scene(t):
    """一帧"站立的人"：高 0.35~1.6 m 的一列点，轻微摆动，偶尔挥手。"""
    pts = []
    idx = 0
    sway = 0.12 * math.sin(t * 0.7)          # 身体左右轻摆
    for k in range(26):
        z = 0.35 + 1.25 * k / 25.0
        # 躯干略宽，两端收窄，看起来像人
        half = 0.22 if 0.5 < z < 1.35 else 0.10
        for side in (-1.0, 1.0):
            x = 0.30 + sway + side * half * (0.55 + 0.45 * math.sin(k * 1.1))
            y = 2.00 + 0.06 * math.sin(t * 1.5 + k * 0.4)
            v = 0.04 * math.sin(t * 2.0 + k)
            pts.append((idx, x, y, z, v, 3000 + int(1500 * abs(math.sin(k * 0.9 + t)))))
            idx += 1
    # 挥手：一只手在头侧来回
    for j in range(6):
        ang = t * 3.0 + j * 0.35
        x = 0.30 + sway + 0.42 + 0.12 * math.cos(ang)
        z = 1.45 + 0.12 * math.sin(ang)
        pts.append((idx, x, 2.02, z, 0.30 * math.cos(ang), 9000))
        idx += 1
    return pts


def pump(master, hz=10.0):
    period = 1.0 / hz
    t0 = time.time()
    n = 0
    while not STOP:
        try:
            os.write(master, mkframe.build_frame(scene(time.time() - t0)))
            n += 1
        except OSError:
            pass                              # 从端暂时没开（原程序会 close/open）
        time.sleep(period)
    print(f"[replay] 共重放 {n} 帧", flush=True)


def main():
    global STOP
    master, slave = pty.openpty()
    tty.setraw(master)
    name = os.ttyname(slave)
    try:
        os.chmod(name, 0o666)
    except OSError:
        pass
    try:
        if os.path.lexists("/tmp/ttyMMWAVE"):
            os.remove("/tmp/ttyMMWAVE")
        os.symlink(name, "/tmp/ttyMMWAVE")
    except OSError:
        pass

    sample = mkframe.build_frame(scene(0.0))
    print(f"[replay] 虚拟串口 {name}（软链接 /tmp/ttyMMWAVE）", flush=True)
    print(f"[replay] 每 0.1 s 一帧，{len(scene(0.0))} 个点，报文 {len(sample)} 字节", flush=True)

    th = threading.Thread(target=pump, args=(master,), daemon=True)
    th.start()

    if "--run" in sys.argv:
        env = dict(os.environ)
        env["MMWAVE_SERIAL"] = name
        cmd = [sys.executable, os.path.join(HERE, "launcher.py")]
        print(f"[replay] 启动原程序（MMWAVE_SERIAL={name}）", flush=True)
        try:
            rc = subprocess.call(cmd, env=env)
            print(f"[replay] 原程序退出，返回码 {rc}", flush=True)
        finally:
            STOP = True
        return 0

    print("[replay] Ctrl-C 结束", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        STOP = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
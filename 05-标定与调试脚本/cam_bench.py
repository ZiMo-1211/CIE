# -*- coding: utf-8 -*-
"""测摄像头自身能跑多快（和程序无关）：裸 read 帧率 + MJPG/YUYV 格式对比。

    python3 cam_bench.py                 # 默认 /dev/video0
    python3 cam_bench.py /dev/video20    # 指定设备
"""
import sys
import time

import cv2

dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/video0"
idx = int(dev.replace("/dev/video", "")) if dev.startswith("/dev/video") else 0


def bench(desc, fourcc=None, warm=15, n=60):
    cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"  {desc:26s} 打不开")
        return None
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    for _ in range(warm):                       # 预热：丢掉开头不稳的帧
        cap.read()
    t0 = time.perf_counter()
    ok = 0
    for _ in range(n):
        ret, frame = cap.read()
        if ret:
            ok += 1
    dt = time.perf_counter() - t0
    w = frame.shape[1] if ok else 0
    h = frame.shape[0] if ok else 0
    cc = int(cap.get(cv2.CAP_PROP_FOURCC))
    cc_s = "".join(chr((cc >> (8 * i)) & 0xFF) for i in range(4)) or "?"
    cap.release()
    fps = ok / dt if dt else 0
    print(f"  {desc:26s} {w}x{h} {cc_s:5s} 读到 {ok}/{n} 帧 -> {fps:5.1f} fps"
          f"（单帧 {dt / max(ok, 1) * 1000:5.1f} ms）")
    return fps


print(f"设备 {dev}")
bench("默认（不指定格式）")
bench("MJPG", "MJPG")
bench("YUYV", "YUYV")
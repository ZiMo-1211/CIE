# -*- coding: utf-8 -*-
"""盯着毫米波雷达的串口看 20 秒：数帧、数点、看坐标范围，判断"没人"还是"没数据"。

直接复用原程序 video_real12.py 的 RadarCapture，解析路径和正式运行完全一致。

    python3 radar_watch.py              # 默认 COM7 -> 由 launcher 之外的路径决定
    MMWAVE_SERIAL=/dev/ttyACM0 python3 radar_watch.py /dev/ttyACM0
"""
import os
import queue
import sys
import time

# 本脚本在 _local_debug/ 里，原程序在上一级目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import video_real12 as V

# 原程序把串口名写在方法里的局部变量（COM7），改模块属性没用。
# 直接在 serial.Serial 这一层把 COM7 换成真实设备。
import serial as _pyserial

dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
_Serial = _pyserial.Serial


def _patched_serial(port=None, *a, **kw):
    if port in (None, "", "COM7") or str(port).upper().startswith("COM"):
        port = dev
    return _Serial(port, *a, **kw)


_pyserial.Serial = _patched_serial

print(f"串口 {dev}  波特率 2000000（原程序解析器）")
radar = V.RadarCapture(V.radar_raw_data_save_path,
                       V.radar_points_data_save_path,
                       V.radar_points_data_save_dir)
radar.start()

DUR = 20
t0 = time.time()
frames = 0
all_pts = []
per_frame = []
try:
    while time.time() - t0 < DUR:
        try:
            d = V.radar_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        frames += 1
        pts = [p for p in d['frame'] if p['y'] >= 0]
        fb = [p for p in d['frame'] if p['y'] < 0]
        per_frame.append(len(pts))
        all_pts.extend(pts)
        if frames <= 5 or frames % 10 == 0:
            print(f"  第 {frames:3d} 帧  {time.time() - t0:5.1f}s  "
                  f"有效点 {len(pts):3d} 个（y<0 丢弃 {len(fb):2d} 个）", flush=True)
finally:
    el = time.time() - t0
    radar.running = False
    print()
    print(f"== {el:.1f} 秒内收到 {frames} 帧 = {frames / el:.1f} 帧/秒 ==")
    if frames:
        print(f"   每帧点数：最少 {min(per_frame)} / 最多 {max(per_frame)} / "
              f"平均 {sum(per_frame) / len(per_frame):.1f}")
    if all_pts:
        import numpy as np
        a = np.array([[p['x'], p['y'], p['z']] for p in all_pts])
        print(f"   共 {len(a)} 个点")
        for i, ax in enumerate('xyz'):
            print(f"   {ax}: {a[:, i].min():7.2f} ~ {a[:, i].max():7.2f} "
                  f"（均值 {a[:, i].mean():6.2f} m）")
        print()
        print("   判定：")
        n = len(a) / max(frames, 1)
        if n < 5:
            print("     ❌ 几乎没有点 —— 雷达在发帧，但探测不到目标")
            print("        → 前方 1~3 m 站个人（正对雷达），再跑一次")
        else:
            print(f"     ✅ 每帧 {n:.0f} 个点，雷达工作正常，前方有目标")
        if len(a) and abs(a[:, 0]).max() > 10:
            print("     ⚠ x 超过 ±10 m，可能是噪点或多径反射")
    else:
        print("    ❌ 一个有效点都没有")
        print("     → 逐项查：波特率是否 2000000 / 雷达是否在发数据 / 目标距离")
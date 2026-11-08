# -*- coding: utf-8 -*-
"""本地调试用的假硬件：假雷达串口 + 假摄像头。

PC 上没有毫米波雷达、也没有板上那个 USB 摄像头，但程序本身可以完整跑起来：

  FakeSerial   冒充 serial.Serial，按原程序协议以 10 Hz 吐"站立的人"点云报文
  FakeCapture  冒充 cv2.VideoCapture，吐 640x480 的动态测试画面

报文编码复用 _k1_deploy/mkframe.py（那份编码器已经用 200 组随机数据
按原程序的解析逻辑自检过，坐标误差 < 2 cm）。

用法：
    import fake_hw; fake_hw.install()        # 装上假硬件
    fake_hw.install(camera=False)            # 只用真摄像头
    python fake_hw.py --selftest             # 自检：造报文再用原程序逻辑解回来
"""
import math
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# mkframe.py 可能在 _k1_deploy/ 里（PC 上的布局），也可能和入口一起平铺（板子上的布局）
for _p in (os.path.join(ROOT, "_k1_deploy"), ROOT, HERE):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import mkframe  # noqa: E402

FPS = float(os.environ.get("FAKE_RADAR_HZ", 10.0))
PEOPLE = int(os.environ.get("FAKE_PEOPLE", 1))


# ---------------- 点云场景 ----------------
def scene(t, people=PEOPLE):
    """造一帧点云：默认 1 个"站立的人"，people>1 时并排多个人（用于加压）。"""
    pts = []
    idx = 0
    for p in range(people):
        base_x = -1.3 + 1.3 * p
        base_y = 2.0 + 0.55 * p
        sway = 0.12 * math.sin(t * 0.7 + p)
        for k in range(26):                     # 躯干：一列点
            z = 0.35 + 1.25 * k / 25.0
            half = 0.22 if 0.5 < z < 1.35 else 0.10
            for side in (-1.0, 1.0):
                x = base_x + sway + side * half * (0.55 + 0.45 * math.sin(k * 1.1))
                y = base_y + 0.06 * math.sin(t * 1.5 + k * 0.4)
                v = 0.04 * math.sin(t * 2.0 + k)
                pts.append((idx, x, y, z, v,
                            3000 + int(1500 * abs(math.sin(k * 0.9 + t)))))
                idx += 1
        for j in range(6):                      # 挥手：手在头侧来回
            ang = t * 3.0 + j * 0.35 + p
            x = base_x + sway + 0.42 + 0.12 * math.cos(ang)
            z = 1.45 + 0.12 * math.sin(ang)
            pts.append((idx, x, base_y + 0.02, z, 0.30 * math.cos(ang), 9000))
            idx += 1
    return pts


# ---------------- 假串口 ----------------
class FakeSerial:
    """冒充 pyserial 的 Serial：原程序只用到 in_waiting / read / is_open / open / close / port。"""

    def __init__(self, port=None, baudrate=115200, **_kw):
        self.port = port
        self.baudrate = baudrate
        self.is_open = True
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = False
        self._frames = 0
        self._t0 = time.time()
        with self._lock:                        # 先塞一帧，保证 openRadar() 立刻找到包头
            self._buf += mkframe.build_frame(scene(0.0))
        self._th = threading.Thread(target=self._pump, daemon=True)
        self._th.start()

    def _pump(self):
        period = 1.0 / FPS
        nxt = time.time()
        while not self._stop:
            nxt += period
            frame = mkframe.build_frame(scene(time.time() - self._t0))
            with self._lock:
                if len(self._buf) > 2 * 1024 * 1024:   # 消费端卡住就丢，别撑爆内存
                    self._buf.clear()
                self._buf += frame
                self._frames += 1
            sleep = nxt - time.time()
            if sleep > 0:
                time.sleep(sleep)

    @property
    def in_waiting(self):
        with self._lock:
            return len(self._buf)

    def read(self, size=1):
        with self._lock:
            n = min(size, len(self._buf))
            data = bytes(self._buf[:n])
            del self._buf[:n]
            return data

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = True        # 原程序关掉后马上又检查 is_open，这里保持可用

    def reset_input_buffer(self):
        with self._lock:
            self._buf.clear()

    def __repr__(self):
        return f"<FakeSerial port={self.port} 已发 {self._frames} 帧>"


# ---------------- 假摄像头 ----------------
class FakeCapture:
    """冒充 cv2.VideoCapture：吐 640x480 BGR 动态画面（渐变 + 移动方块 + 帧号）。"""

    def __init__(self, index=0, *args, **kwargs):
        self.index = index
        self.w, self.h = 640, 480
        self.opened = True
        self.t0 = time.time()
        self.n = 0
        self._period = 1.0 / float(os.environ.get("FAKE_CAM_HZ", 30.0))
        self._next = time.time()
        # 静态渐变底图，每帧只叠加动态元素，省 CPU
        xs = np.linspace(0, 255, self.w, dtype=np.uint8)
        ys = np.linspace(0, 255, self.h, dtype=np.uint8)
        base = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        base[:, :, 0] = ys[:, None]                      # B
        base[:, :, 1] = xs[None, :]                      # G
        base[:, :, 2] = 60                               # R
        self._base = base

    def isOpened(self):
        return self.opened

    def read(self):
        if not self.opened:
            return False, None
        # 模仿真实摄像头：按固定帧率出图，否则采集线程会空转把 CPU 跑满
        now = time.time()
        wait = self._next - now
        if wait > 0:
            time.sleep(wait)
            now = time.time()
        self._next = max(self._next + self._period, now)
        self.n += 1
        t = time.time() - self.t0
        frame = self._base.copy()
        # 一个横向来回移动的亮块，肉眼看得出画面在动
        cx = int((0.5 + 0.4 * math.sin(t * 1.2)) * (self.w - 80))
        cy = int((0.5 + 0.3 * math.sin(t * 2.1)) * (self.h - 80))
        frame[cy:cy + 80, cx:cx + 80] = (240, 240, 240)
        frame[cy + 20:cy + 60, cx + 20:cx + 60] = (30, 30, 200)
        # 顶部一条帧计数条，拍屏校验时能看出在刷新
        bar = int((self.n % 64) / 64.0 * self.w)
        frame[0:8, :bar] = (255, 255, 255)
        return True, frame

    def set(self, *a, **kw):
        return True

    def get(self, *a, **kw):
        return 0.0

    def release(self):
        self.opened = False

    def __repr__(self):
        return f"<FakeCapture 已出 {self.n} 帧>"


# ---------------- 安装 ----------------
def install(serial_on=True, camera=True):
    """把 serial.Serial / cv2.VideoCapture 换成假实现。返回被替换的类，便于还原。"""
    originals = {}
    if serial_on:
        import serial
        originals["serial"] = serial.Serial
        serial.Serial = FakeSerial
    if camera:
        import cv2
        originals["cv2"] = cv2.VideoCapture
        cv2.VideoCapture = FakeCapture
    print(f"[假硬件] 串口={'假' if serial_on else '真'} "
          f"摄像头={'假' if camera else '真'} "
          f"({PEOPLE} 人 / {len(scene(0.0))} 点 / {FPS:g} Hz)", flush=True)
    return originals


def main():
    if "--selftest" in sys.argv:
        # 用假串口喂数据，再用原程序的解析逻辑解回来核对（只取第一帧）
        ser = FakeSerial(port="FAKE")
        time.sleep(0.05)
        n = ser.in_waiting
        data = ser.read(n)
        stop = data.find(mkframe.POINT_STOP)
        assert stop > 0, "报文里找不到点云结束标志"
        one = data[:stop + len(mkframe.POINT_STOP)]
        print(f"假串口缓冲 {n} 字节，取第一帧 {len(one)} 字节 → 解析中 ...")
        got = mkframe.parse_like_original(one)
        want = scene(0.0)
        err = max(max(abs(g["x"] - w[1]), abs(g["y"] - w[2]), abs(g["z"] - w[3]))
                  for g, w in zip(got, want))
        print(f"解出 {len(got)} 个点（期望 {len(want)}），最大坐标误差 {err * 100:.1f} cm")
        assert len(got) == len(want) and err < 0.03, "假串口数据与原程序解析不匹配"
        cap = FakeCapture()
        ok, img = cap.read()
        print(f"假摄像头 {img.shape} dtype={img.dtype} 有画面={ok}")
        print("自检通过")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
# -*- coding: utf-8 -*-
"""K1 极速版：采集解析放独立进程 + 分块自绘（摄像头 30 fps / 点云 10 Hz）。原程序一个字节不改。

== 为什么需要这个版本（都在 K1 板上原生实测过）==

1) pyqtgraph 的多控件界面太慢
   原程序界面（3 张图 + 3D + 图像）        0.5 fps   单帧 878 ms
   只改渲染写法（还是 pyqtgraph）           3.3 fps   单帧  95 ms
   本文件（分块自绘 + 采集独立进程）        9.0 fps   单帧  16 ms
   瓶颈不在算力，在 pyqtgraph 的 QGraphicsView 控件：
   每个控件每帧重绘要 55~90 ms，而贴一张图只要几毫秒。

2) Python 的 GIL 是第二个隐形杀手
   同一套自绘代码，没有后台线程时 26 fps，接上原程序的
   串口解析/摄像头/同步三个线程后掉到 4.8 fps。
   那三个线程是纯 Python 密集循环（2 Mbaud 串口逐字符解析），
   和 UI 线程抢同一个 GIL。所以这里把采集放到独立进程。

3) 摄像头不能跟着雷达的 10 Hz 走
   原程序把两路数据按时间戳配对，配对速率 = 雷达帧率 = 10 Hz，
   于是 30 fps 的摄像头也只能显示 10 fps —— 这就是"摄像头也卡"。
   这里让摄像头单独走一条通道（每从设备读到一帧就发出去），
   摄像头按自己的帧率刷，点云按雷达节奏刷。

== 界面分三块（各刷各的，互不拖累）==
   +------------------+   +------------------------+
   |   摄像头 30 fps   |   |  XY 图     |  YZ 图      |
   +------------------+   |------------+------------|
   |   状态/人数 10 Hz |   |  XZ 图     |  3D 视图    |
   +------------------+   +------------------------+
   每块都是独立 QLabel：摄像头只刷 640x480，点云画布只刷 730x780，
   不再每帧重画整块 1400x800（那样仅背景填充就白花十几毫秒）。

== 保持不变 ==
   串口协议解析、时间同步、DBSCAN 参数(eps=0.5/min_samples=30)、
   跌倒判定、按簇配色、各视图坐标范围 —— 全部沿用原程序。

用法：
    bash run_native.sh lite       板上原生运行（推荐）
    bash run.sh lite              Docker 里运行

环境变量：
    MMWAVE_UI_MS        点云刷新间隔毫秒，默认 33
    MMWAVE_CAM_MS       摄像头刷新间隔毫秒，默认 25（≈40 fps 上限）
    MMWAVE_WIN_FRAME    滑动窗口帧数，默认沿用原程序的 3
    MMWAVE_MAX_POINTS   每帧最多绘制的点数，默认 1200
    MMWAVE_PROC=0       把采集放回 UI 进程（对照用，会明显变慢）
    MMWAVE_SYNTH_HW=1   本地调试用假雷达/假摄像头
    MMWAVE_PROFILE=1    每 2 秒打印点云/摄像头帧率与各段耗时
    MMWAVE_DBSCAN_EVERY 聚类每 N 帧做一次，默认 2（点云照常每帧重画，颜色慢 100 ms 看不出来）
    MMWAVE_MIN_SAMPLES  DBSCAN 最少点数，默认 30（同原程序）。近距离目标点少聚不出簇、
                        界面"人数"恒为 0 时，可以调到 10~15 试试
    MMWAVE_DBSCAN_EPS   DBSCAN 邻域半径，默认 0.5 m
    MMWAVE_FALL_BY_CLUSTER=1  状态行的跌倒判定改成按簇（和"实时跌倒人数"一致），
                        避免"人数 1、跌倒人数 0、状态却疑似跌倒"。默认 0 = 原行为
    MMWAVE_PERSON_MIN   判"有人"的最少点数，默认 10（同原程序 len(X) > 10）。
                        空场地上十几二十个噪点会被误判成有人，抬到 30 更稳
    MMWAVE_PERSON_HOLD  去抖帧数，默认 1 = 不去抖。连续 N 帧才算有人，10 fps 下 5 帧≈0.5 秒
"""
import json
import multiprocessing as mp
import os
import queue
import sys
import threading
import time

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
from sklearn.cluster import DBSCAN

import video_real12 as V          # 原程序：只导入，不执行它的 main()

UI_INTERVAL_MS = int(os.environ.get("MMWAVE_UI_MS", 33))
CAM_INTERVAL_MS = int(os.environ.get("MMWAVE_CAM_MS", 25))
MAX_POINTS = int(os.environ.get("MMWAVE_MAX_POINTS", 1200))
PROFILE = os.environ.get("MMWAVE_PROFILE", "") == "1"
USE_PROC = os.environ.get("MMWAVE_PROC", "1") != "0"
DBSCAN_EVERY = max(1, int(os.environ.get("MMWAVE_DBSCAN_EVERY", 2)))
# DBSCAN 参数，默认和原程序一致（eps=0.5 / min_samples=30）。
# min_samples=30 的问题：近距离目标一帧只出 10~20 个点，两帧窗口也才 20~40 个，
# 聚不出簇 -> 界面上"范围内人数"永远是 0，而"状态"却因为点数>10 说"检测到目标"。
# 想让近距离小目标也成簇就把 min_samples 调小（10~15），默认值不动原程序行为。
DBSCAN_EPS = float(os.environ.get("MMWAVE_DBSCAN_EPS", 0.5))
MIN_SAMPLES = max(2, int(os.environ.get("MMWAVE_MIN_SAMPLES", 30)))
# 状态行的跌倒判据。原程序：全局最高点<0.6 且高差<0.4；
# 而"实时跌倒人数"是：簇内最高点<-0.4 且高差<0.5。两套阈值不同，
# 就会出现"人数 1、跌倒人数 0、状态却疑似跌倒"这种自相矛盾的显示。
# 置 1 让状态行也按簇判断（fall_count>0 才算跌倒），两边就一致了。
# 默认 0 = 完全保留原程序行为。
FALL_BY_CLUSTER = os.environ.get("MMWAVE_FALL_BY_CLUSTER", "0") == "1"
# "有人"的判据。原程序是 len(X) > 10，空场地上十几二十个地面/噪点就能骗过去。
# 真人站在 1~3 m 处一般 30~120 点/帧，所以阈值抬到 30 更稳。
PERSON_MIN = max(1, int(os.environ.get("MMWAVE_PERSON_MIN", 10)))
# 去抖帧数：连续 N 帧都判定才认。10 fps 下 N=5 约等于"持续 0.5 秒"。
PERSON_HOLD = max(1, int(os.environ.get("MMWAVE_PERSON_HOLD", 1)))
# 最小距离门限（米）：扔掉雷达正前方的近场杂波（桌面/支架/旁边设备）。
# 实测空场地上有 15~29 个点全挤在 y=0.17~0.50 m 处，就是它们把原程序
# 骗成"有人 + 跌倒"。默认 0 = 不过滤，和原程序一致。
MIN_RANGE = max(0.0, float(os.environ.get("MMWAVE_MIN_RANGE", 0)))
# 对外状态接口：给网页大屏用。0 = 关闭。
HTTP_PORT = int(os.environ.get("MMWAVE_HTTP_PORT", 8091))

# 最新一帧的判定结果，供 HTTP 接口读取（UI 线程写，HTTP 线程读）
_STATE = {}
_STATE_LOCK = threading.Lock()

W, H = 1400, 800
CAM_W, CAM_H = 640, 480

# 窗口里三块区域的位置
CAM_RECT = QtCore.QRect(10, 30, 640, 480)          # 摄像头
STATUS_RECT = QtCore.QRect(10, 520, 640, 270)      # 状态/人数
PLOT_POS = QtCore.QRect(660, 10, 730, 780)         # 点云画布在窗口里的位置

# 点云画布内部（本地坐标）2x2 排布
CANVAS_W, CANVAS_H = PLOT_POS.width(), PLOT_POS.height()
XY_RECT = QtCore.QRect(8, 26, 350, 350)
YZ_RECT = QtCore.QRect(372, 26, 350, 350)
XZ_RECT = QtCore.QRect(8, 404, 350, 356)
V3_RECT = QtCore.QRect(372, 404, 350, 356)

SCALE = 3          # 点云小位图的缩放比：小图 1 像素 = 屏上 3 像素

# 每个簇一套颜色（与原程序 create_cluster_spots 的顺序一致）
COLOR_TABLE = [(255, 80, 80), (80, 255, 120), (90, 140, 255), (80, 255, 255),
               (255, 90, 255), (255, 255, 90), (240, 240, 240), (255, 165, 0),
               (160, 90, 255), (140, 255, 60), (255, 150, 200)]

BG = QtGui.QColor(0, 0, 0)
GRID = QtGui.QColor(38, 38, 38)
BORDER = QtGui.QColor(90, 90, 90)
FG = QtGui.QColor(210, 210, 210)
C_GREY = QtGui.QColor(150, 150, 150)

# 中文字体候选：板上默认 sans-serif 是 Noto Sans（只有拉丁字母），
# 不显式指定的话中文会显示成 □□□ 方块。
CJK_FAMILIES = ("Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC",
                "Source Han Sans CN", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
                "Noto Sans CJK JP", "Noto Sans CJK TC", "Droid Sans Fallback")
CJK_FONT_FILES = ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                  "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc")


def pick_cjk_font():
    """挑一个真正带汉字字形的字体族，返回族名（找不到就返回 None）。"""
    db = QtGui.QFontDatabase
    have = set(db().families())
    for fam in CJK_FAMILIES:
        if fam in have:
            return fam
    for path in CJK_FONT_FILES:                     # 字体在磁盘上但 Qt 没登记，手动加载
        if os.path.exists(path):
            fid = db.addApplicationFont(path)
            if fid >= 0:
                fams = db.applicationFontFamilies(fid)
                if fams:
                    return fams[0]
    return None


# ==================== 对外状态接口（给网页大屏用）====================
def publish_state(**kw):
    """把最新判定结果写进全局，HTTP 线程读它。"""
    kw["ts"] = int(time.time() * 1000)
    with _STATE_LOCK:
        _STATE.clear()
        _STATE.update(kw)


def _state_json():
    with _STATE_LOCK:
        d = dict(_STATE)
    if not d:
        d = {"ok": True, "online": False, "person_detected": False,
             "fall_detected": False, "state_text": "等待数据",
             "person_count": 0, "fall_count": 0, "points": 0, "clusters": 0}
        d["ts"] = int(time.time() * 1000)
    # 3 秒没更新说明程序卡了/没在跑
    d["stale"] = (int(time.time() * 1000) - d.get("ts", 0)) > 3000
    d["ok"] = True
    return d


def serve_status(port):
    """极简只读接口：GET /status 返回 JSON。跨域放开，网页在 8090 也能取。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        server_version = "MMWaveStatus/1.0"

        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path in ("/status", "/api/status", "/"):
                body = json.dumps(_state_json(), ensure_ascii=False).encode("utf-8")
                self._send(200, body)
            else:
                self._send(404, b'{"ok":false,"error":"not found"}')

        def do_OPTIONS(self):                    # 预检请求
            self._send(204, b"")

        def log_message(self, fmt, *args):
            pass                                 # 别刷屏

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="status-http")
    t.start()
    print(f"[lite] 状态接口 http://0.0.0.0:{port}/status （供网页大屏轮询）", flush=True)
    return srv


# ==================== 采集进程 ====================
def capture_worker(pair_q, stop_evt, shm, seq, hw_stats=None):
    """在原程序的采集/解析/同步线程里取数。

    图像走共享内存（每帧 640x480x4 ≈ 1.2 MB，序列化太贵），
    队列里只传点云坐标（每帧几十~几百个点，很小）。
    """
    # 本地调试用：PC 上没有雷达/摄像头时用假硬件（子进程是 spawn，必须自己装一遍）
    if os.environ.get("MMWAVE_SYNTH_HW") == "1":
        try:
            import fake_hw
            fake_hw.install()
        except ImportError as e:
            print(f"[采集] 装假硬件失败（继续用真硬件）：{e}", flush=True)

    cam_buf = np.frombuffer(shm, dtype=np.uint8)
    cam4 = cam_buf.reshape(CAM_H, CAM_W, 4)

    # ---- 摄像头采集：按设备真实帧率读，读一帧就发一帧 ----
    # 原程序的 CameraCapture.run 往同步队列投递时用的是 put(timeout=0.2)：
    # 队列一满（同步层只按雷达 10 Hz 消费），每次投递就白等 0.2 秒，
    # 于是 30 fps 的摄像头实际只跑到 12 fps 左右 —— 这就是"摄像头也卡"的根因。
    # 这里换成「不阻塞投递 + 满了丢最旧」，摄像头就能跑满自己的帧率。
    def _publish(frame):
        # 直接让 OpenCV 写进共享内存：BGR -> BGRA 一次搞定，
        # 目标布局正是 Qt 的 Format_RGB32（小端 B,G,R,0xFF），UI 侧零转换。
        # 之前用 4 次跨步 numpy 赋值，在这块板子上每帧要 20~30 ms，把摄像头拖到 15 fps。
        cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA, dst=cam4)
        with seq.get_lock():
            seq.value += 1

    def _fast_camera_run(self):
        if not self.cap.isOpened():
            print("无法打开摄像头", flush=True)
            return
        print("摄像头采集中...按 Q 退出", flush=True)
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            cv2.flip(frame, 1, frame)               # 原地翻转，省一次分配
            try:
                _publish(frame)                     # 显示通道：满速
            except Exception:
                pass
            ts = int(time.time() * 1000)
            try:                                    # 同步通道：有空位就投，满了丢最旧
                V.frame_queue.put_nowait({'timestamp': ts, 'frame': frame})
            except queue.Full:
                try:
                    V.frame_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    V.frame_queue.put_nowait({'timestamp': ts, 'frame': frame})
                except queue.Full:
                    pass

    V.CameraCapture.run = _fast_camera_run

    # ---- 雷达侧硬件计数：分清"没目标"和"没数据" ----
    # 雷达是 10 帧/秒一直发的，但探测不到目标时帧里是 0 个点，
    # 原程序的 extract_head 要求头部体正好 40 个 hex 字符，0 点帧只有 32 个，
    # 于是这类帧会被整帧丢掉。所以界面上"没有点云"有两种完全不同的原因：
    #   A. 雷达在发、只是范围内没人     -> 计数在涨，有点的包是 0
    #   B. 串口根本没数据               -> 计数不动
    # 数一下 jiexi_targets 被调用多少次、其中多少次有真点，就能分开这两种情况。
    if hw_stats is not None:
        _orig_jiexi = V.RadarCapture.jiexi_targets

        def _counted_jiexi(self, pretty_hex, current_frame_timestamp):
            out = _orig_jiexi(self, pretty_hex, current_frame_timestamp)
            hw_stats[0] += 1                    # 雷达发出的包数
            if out:
                hw_stats[1] += 1                # 其中带真点的包数
                hw_stats[2] = len(out)
            return out

        V.RadarCapture.jiexi_targets = _counted_jiexi

    camera = V.CameraCapture(V.camera_images_save_dir)
    # 雷达打不开（没插 / 权限不够）时不能连累摄像头 ——
    # 摄像头预览照常显示，点云面板走"无目标"状态。
    radar = None
    sync = None
    try:
        radar = V.RadarCapture(V.radar_raw_data_save_path,
                               V.radar_points_data_save_path,
                               V.radar_points_data_save_dir)
        sync = V.SyncFrameCollect(V.align_data_save_dir, V.frame_queue,
                                  V.radar_queue, V.sync_frame_queue,
                                  V.sync_radar_queue,
                                  max_time_diff=V.max_time_diff)
    except Exception as e:
        print(f"[采集] 雷达打不开（{e}），本次只显示摄像头。"
              f"请检查串口权限：sudo usermod -aG dialout $USER 或 ls -l 串口设备", flush=True)

    camera.start()
    if radar is not None and sync is not None:
        radar.start()
        sync.start()
    print("[采集] 已启动", flush=True)
    try:
        t_start = time.time()
        radar_only = False
        while not stop_evt.is_set():
            if sync is None:
                time.sleep(0.2)
                continue
            # 摄像头打不开 / 没出帧时，同步队列永远凑不齐一对，
            # 原来这里会一直 continue，雷达数据被闷在队列里 —— 界面点云 0 fps。
            # 所以 3 秒还没见到摄像头出帧，就直接按雷达自己的节奏送点云。
            if not radar_only and (time.time() - t_start) > 3.0 and seq.value == 0:
                radar_only = True
                print("[采集] 摄像头没有出帧，改为只送雷达数据（界面照常显示点云）",
                      flush=True)
            try:
                if radar_only:
                    rd = V.radar_queue.get(timeout=0.3)
                else:
                    fd = V.sync_frame_queue.get(timeout=0.3)
                    rd = V.sync_radar_queue.get(timeout=0.3)
            except queue.Empty:
                continue
            # 兜底：万一摄像头采集循环没接管成功，至少按同步帧节奏也能出画面
            if (not radar_only and seq.value == 0
                    and getattr(fd.get('frame'), 'shape', None) == (CAM_H, CAM_W, 3)):
                cam_buf[:] = cv2.cvtColor(fd['frame'], cv2.COLOR_BGR2RGB).reshape(-1)
                with seq.get_lock():
                    seq.value += 1
            pts = [(p['x'], p['y'], p['z'], p['v'], p['SNR'])
                   for p in rd['frame'] if p['y'] >= 0]
            try:
                # 连时间戳一起送出去，界面侧能算出"这一帧数据到屏幕用了多久"
                pair_q.put((pts, rd.get('timestamp')), timeout=1.0)
            except queue.Full:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        print("[采集] 正在退出", flush=True)
        try:
            V.stop_all(camera, radar, sync)
        except Exception:
            pass


class _Buf:
    """一块透明小位图缓冲：numpy 批量写点，再一次性贴到屏幕上。

    用 RGBA8888 + 全透明底，贴上去不会盖住背景网格。
    颜色可以是单个 (r,g,b[,a]) 元组，也可以是逐点的 (N,4) 数组（3D 按 z 上色用）。
    """

    def __init__(self, rect, scale=SCALE):
        self.w = max(8, rect.width() // scale)
        self.h = max(8, rect.height() // scale)
        self.arr = np.zeros((self.h, self.w, 4), dtype=np.uint8)
        self.qimg = QtGui.QImage(self.arr.data, self.w, self.h, self.w * 4,
                                 QtGui.QImage.Format_RGBA8888)

    def clear(self):
        self.arr[:] = 0

    def plot(self, u, v, color, umin, umax, vmin, vmax, cross=True):
        if len(u) == 0:
            return
        w, h = self.w, self.h
        gx = np.clip(((np.asarray(u, float) - umin) / (umax - umin) * (w - 1)).astype(np.int32), 2, w - 3)
        gy = np.clip(((np.asarray(v, float) - vmin) / (vmax - vmin) * (h - 1)).astype(np.int32), 2, h - 3)
        gy = h - 1 - gy

        if isinstance(color, np.ndarray):
            vals = color.astype(np.uint8)
        else:
            vals = np.empty((len(gx), 4), dtype=np.uint8)
            vals[:, 0] = color[0]
            vals[:, 1] = color[1]
            vals[:, 2] = color[2]
            vals[:, 3] = color[3] if len(color) > 3 else 255

        self.arr[gy, gx] = vals
        if cross:                                   # 十字膨胀，屏上看着像圆点
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                self.arr[gy + dy, gx + dx] = vals


class LiteDashboard(QtWidgets.QWidget):
    """三块独立区域：摄像头 / 状态 / 点云画布，各自按自己的节奏刷新。"""

    def __init__(self, pair_q, win_frame_num=3, cam_shm=None, cam_seq=None, hw_stats=None):
        super().__init__()
        self.pair_q = pair_q
        self.hw = hw_stats
        self._hw_mark = (0, 0)
        self.win_frame_num = win_frame_num
        self.cam_seq = cam_seq
        self._cam_frame = None
        if cam_shm is not None:
            self._cam_frame = np.frombuffer(cam_shm, dtype=np.uint8).reshape(CAM_H, CAM_W, 4)
        self.hist = []          # 最近 win_frame_num 帧的点云
        self._frame_no = 0      # 用于「聚类隔帧做」
        self._labels = None
        # 有无人的去抖计数（MMWAVE_PERSON_HOLD > 1 时才用得上）
        self._person_on = 0
        self._person_off = 0
        self._person_state = False

        self.ui_font = pick_cjk_font() or ""
        if self.ui_font:
            self.setFont(QtGui.QFont(self.ui_font, 10))
        else:
            print("[lite] 警告：没找到中文字体，界面文字可能显示成方块", flush=True)
        self.setWindowTitle("Radar Visualizer (K1 极速版)")
        self.setFixedSize(W, H)

        # 三块独立 QLabel，绝对定位（保持原程序的布局比例）
        # 注意：这里刻意不用 setStyleSheet —— Qt 的样式表会在每次重绘时走
        # QStyleSheetStyle，在这块板子上每帧多花十几毫秒。用调色板设背景更省。
        self.cam_label = QtWidgets.QLabel(self)
        self.cam_label.setGeometry(CAM_RECT)
        self.cam_label.setAutoFillBackground(True)

        self.status_label = QtWidgets.QLabel(self)
        self.status_label.setGeometry(STATUS_RECT)
        if self.ui_font:
            self.status_label.setFont(QtGui.QFont(self.ui_font, 10))
        self.status_label.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self._status_text = None

        # 状态区自己画（和点云画布一个思路：每次从黑底整块重画）。
        # 用 QLabel 显示文字时，Qt 的擦背景/重绘顺序一变就会出现
        # "新状态盖在旧状态上、文字糊成一团"的问题，自己画就不可能叠。
        self.status_canvas = QtGui.QImage(STATUS_RECT.width(), STATUS_RECT.height(),
                                          QtGui.QImage.Format_RGB32)
        self.status_canvas.fill(BG)

        self.plot_label = QtWidgets.QLabel(self)
        self.plot_label.setGeometry(PLOT_POS)
        self.plot_label.setAutoFillBackground(True)

        # 只有两块「贴满整块位图」的标签能走不透明重绘：
        # Qt 就不会在贴图前先擦一遍背景，这块板子上每次能省几毫秒。
        for _w in (self.cam_label, self.plot_label, self.status_label):
            _w.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
            _w.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)

        self.canvas = QtGui.QImage(CANVAS_W, CANVAS_H, QtGui.QImage.Format_RGB32)
        self.bg = self._build_bg()

        self.buf_xy = _Buf(XY_RECT)
        self.buf_yz = _Buf(YZ_RECT)
        self.buf_xz = _Buf(XZ_RECT)
        self.buf_3d = _Buf(V3_RECT)

        self._cam_seq_seen = -1
        self._cam_qimg = None
        self._cam_t = 0.0
        self._cam_seq_mark = 0
        self._last_data_t = 0.0
        self._sec = (0.0, 0.0, 0.0)     # 最近一帧的 聚类/栅格/合成 耗时
        self._age_ms = 0.0              # 最新雷达数据到现在的年龄
        self._win_span_ms = 0.0         # 滑动窗口里最旧到最新差多少（拖影长度）
        self._pk_rate = 0.0             # 雷达包率 / 有点包率
        self._hit_rate = 0.0
        self.fps_n = 0
        self.cam_n = 0
        self.fps_t0 = time.time()

        # 两条独立刷新通道：摄像头跟相机节奏，点云跟雷达节奏
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_visualization)
        self.timer.start(UI_INTERVAL_MS)

        self.cam_timer = QtCore.QTimer()
        self.cam_timer.timeout.connect(self.update_camera)
        self.cam_timer.start(CAM_INTERVAL_MS)

        # 心跳：每 2 秒打印统计；雷达长时间不出数据时把界面刷成"无目标"
        self.hb_timer = QtCore.QTimer()
        self.hb_timer.timeout.connect(self._heartbeat)
        self.hb_timer.start(2000)

        # 先画一次空状态，避免启动瞬间界面是花的
        self._draw_empty()

        print(f"[lite] 分块自绘 | 点云 {UI_INTERVAL_MS} ms | 摄像头 {CAM_INTERVAL_MS} ms "
              f"| 最多 {MAX_POINTS} 点", flush=True)

    def _radar_rate(self, span):
        """雷达包率 / 有点包率（每秒）。用来区分"没目标"和"没数据"。"""
        if self.hw is None:
            return None, None
        pk, hit = int(self.hw[0]), int(self.hw[1])
        mk_pk, mk_hit = self._hw_mark
        self._hw_mark = (pk, hit)
        if span <= 0:
            return 0.0, 0.0
        return (pk - mk_pk) / span, (hit - mk_hit) / span

    def _heartbeat(self):
        now = time.time()
        span = now - self.fps_t0
        pkt_rate, hit_rate = self._radar_rate(span)      # 只取一次差值，避免被后续调用吃掉
        self._pk_rate = pkt_rate if pkt_rate is not None else 0.0
        self._hit_rate = hit_rate if hit_rate is not None else 0.0
        # 1.5 秒没有新点云 -> 按原因显示空状态（摄像头照常刷）
        if self._last_data_t and now - self._last_data_t > 1.5:
            if "无目标" not in str(self._status_text):
                if pkt_rate is not None and pkt_rate < 1.0:
                    reason = "串口没有数据（检查雷达连线/供电）"
                else:
                    reason = "探测范围内无目标（雷达 %.0f 帧/秒在发）" % (pkt_rate or 0)
                self._draw_empty(reason)
        self._tick_fps(now)

    # ---------------- 静态背景 ----------------
    def _build_bg(self):
        img = QtGui.QImage(CANVAS_W, CANVAS_H, QtGui.QImage.Format_RGB32)
        p = QtGui.QPainter(img)
        p.fillRect(0, 0, CANVAS_W, CANVAS_H, BG)

        for rect, title, xlabel, ylabel in (
                (XY_RECT, "XY View", "X(m)", "Y(m)"),
                (YZ_RECT, "YZ View", "Y(m)", "Z(m)"),
                (XZ_RECT, "XZ View", "X(m)", "Z(m)"),
                (V3_RECT, "3D View", "", "")):
            p.setPen(QtGui.QPen(BORDER))
            p.drawRect(rect)
            p.setPen(QtGui.QPen(GRID))
            for i in range(1, 10):
                x = rect.left() + rect.width() * i / 10.0
                y = rect.top() + rect.height() * i / 10.0
                p.drawLine(int(x), rect.top(), int(x), rect.bottom())
                p.drawLine(rect.left(), int(y), rect.right(), int(y))
            p.setPen(QtGui.QPen(FG))
            p.setFont(QtGui.QFont(self.ui_font or "DejaVu Sans", 10))
            p.drawText(rect.left() + 3, rect.top() - 7, title)
            p.setFont(QtGui.QFont(self.ui_font or "DejaVu Sans", 7))
            if xlabel:
                p.drawText(rect.left() + rect.width() // 2 - 12, rect.bottom() + 14, xlabel)
                p.drawText(rect.left() - 24, rect.center().y(), ylabel)

        # 3D 视图参考地面网格
        p.setPen(QtGui.QPen(GRID))
        for gx in np.linspace(-3, 3, 7):
            a = self._proj3d(np.array([gx]), np.array([0.0]), np.array([-1.5]))[0]
            b = self._proj3d(np.array([gx]), np.array([6.0]), np.array([-1.5]))[0]
            p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        for gy in np.linspace(0, 6, 7):
            a = self._proj3d(np.array([-3.0]), np.array([gy]), np.array([-1.5]))[0]
            b = self._proj3d(np.array([3.0]), np.array([gy]), np.array([-1.5]))[0]
            p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))
        p.end()
        return img

    def _proj3d(self, X, Y, Z):
        """简易轴测投影：把 (x,y,z) 投到 3D 视图矩形里的像素坐标。"""
        cx = V3_RECT.center().x()
        cy = V3_RECT.center().y() + 30
        sx = cx + (X * 48.0 - Y * 32.0)
        sy = cy - (Z * 62.0 + Y * 24.0)
        return np.column_stack([sx, sy])

    # ---------------- 摄像头通道（独立于雷达节奏） ----------------
    def update_camera(self):
        if self._cam_frame is None or self.cam_seq is None:
            return
        seq = self.cam_seq.value
        if seq == self._cam_seq_seen:
            return                                    # 没有新画面就不重画
        self._cam_seq_seen = seq
        f = self._cam_frame
        t = time.perf_counter()
        if self._cam_qimg is None:
            # 缓冲就是 Format_RGB32 布局，直接引用，零转换
            self._cam_qimg = QtGui.QImage(f.data, CAM_W, CAM_H, CAM_W * 4,
                                          QtGui.QImage.Format_RGB32)
        self.cam_label.setPixmap(QtGui.QPixmap.fromImage(self._cam_qimg))
        self.cam_n += 1
        self._cam_t += time.perf_counter() - t

    # ---------------- 取数：只从队列拿，不阻塞 ----------------
    def data_process(self):
        got = None
        for _ in range(8):                     # 一次把积压的都取完，只留最新的
            try:
                got = self.pair_q.get_nowait()
            except queue.Empty:
                break
        if got is None:
            return None

        # 兼容两种载荷：新的 (点列表, 时间戳) 和旧的纯点列表
        if isinstance(got, tuple) and len(got) == 2:
            got, ts = got
        else:
            ts = None
        if ts is not None:
            self._age_ms = max(0.0, time.time() * 1000 - ts)   # 最新数据已经等了多久

        self.hist.append((got, ts))
        if len(self.hist) > self.win_frame_num:
            self.hist = self.hist[-self.win_frame_num:]
        if len(self.hist) < self.win_frame_num:
            return None

        # 窗口里最旧那帧有多旧 = 画面上"拖影"的长度
        stamps = [t for _, t in self.hist if t]
        if len(stamps) >= 2:
            self._win_span_ms = max(0.0, (stamps[-1] - stamps[0]))
        else:
            self._win_span_ms = 0.0

        pts = []
        for frame_pts, _ts in self.hist:
            pts.extend(frame_pts)

        arr = np.asarray(pts, dtype=float).reshape(-1, 5) if pts else np.zeros((0, 5))
        if len(arr) > MAX_POINTS:
            idx = np.linspace(0, len(arr) - 1, MAX_POINTS).astype(np.int64)
            arr = arr[idx]

        # 第二个字段只是「有新数据」的标记，帧率探针靠它区分定时器空转和真画了一帧
        return arr, True

    # ---------------- 点云通道 ----------------
    def update_visualization(self):
        got = self.data_process()
        if got is None:
            return
        arr = got[0]
        t0 = time.perf_counter()

        if len(arr) == 0:
            self._draw_empty()
            return

        X, Y, Z = arr[:, 0], arr[:, 1], arr[:, 2]
        # 过滤前的原始统计，一并放进状态接口：
        # 现场调阈值时（"站着却是无人" / "没人却报有人"）靠它看清到底是什么点。
        raw_n = len(arr)
        raw_bbox = [round(float(v), 2) for v in
                    (np.min(X), np.max(X), np.min(Y), np.max(Y),
                     np.min(Z), np.max(Z))]
        # 近场门限：y 是到雷达的距离。实测空场地上 15~29 个杂波点全在
        # y<0.6 m，把判据和画面都搞脏了，所以这里统一剔掉。
        if MIN_RANGE > 0.0:
            keep = Y >= MIN_RANGE
            if not keep.any():
                self._person_on = 0
                self._person_off = 0
                self._person_state = False
                self._draw_empty(raw=(raw_n, raw_bbox))
                return
            arr = arr[keep]
            X, Y, Z = arr[:, 0], arr[:, 1], arr[:, 2]
        pts3 = arr[:, :3]

        # 聚类隔帧做：DBSCAN 在板上要约 15 ms，隔帧做省一半。
        # 点云每帧照常重画，只是簇的颜色晚 100 ms，肉眼看不出差别。
        self._frame_no += 1
        labels = None
        if self._labels is None or self._labels.shape[0] != len(arr) \
                or self._frame_no % DBSCAN_EVERY == 0:
            labels = DBSCAN(eps=DBSCAN_EPS, min_samples=MIN_SAMPLES).fit(pts3).labels_
            self._labels = labels
        else:
            labels = self._labels
        uniq = [int(l) for l in np.unique(labels) if l != -1]
        t_db = time.perf_counter()

        # 1) 三张 2D 投影
        self.buf_xy.clear()
        self.buf_yz.clear()
        self.buf_xz.clear()
        for l in uniq:
            m = labels == l
            c = COLOR_TABLE[l % len(COLOR_TABLE)]
            self.buf_xy.plot(X[m], Y[m], c, -3, 3, 0, 6)
            self.buf_yz.plot(Y[m], Z[m], c, 0, 6, -2, 2)
            self.buf_xz.plot(X[m], Z[m], c, -3, 3, -3, 3)

        # 2) 3D：按 z 上色 + 地面投影
        self.buf_3d.clear()
        mask = labels != -1
        if mask.any():
            gx, gy = self._proj3d(X[mask], Y[mask], Z[mask]).T
            self.buf_3d.plot(gx, gy, self._colors_by_z(Z[mask]),
                             V3_RECT.left(), V3_RECT.right(),
                             V3_RECT.bottom(), V3_RECT.top())
            sx, sy = self._proj3d(X[mask], Y[mask], np.full(int(mask.sum()), -1.5)).T
            self.buf_3d.plot(sx, sy, (90, 90, 90, 110),
                             V3_RECT.left(), V3_RECT.right(),
                             V3_RECT.bottom(), V3_RECT.top(), cross=False)
        t_plot = time.perf_counter()

        # 3) 合成点云画布（只有 730x780，不含摄像头那块）
        p = QtGui.QPainter(self.canvas)
        p.drawImage(0, 0, self.bg)
        p.drawImage(XY_RECT, self.buf_xy.qimg)
        p.drawImage(YZ_RECT, self.buf_yz.qimg)
        p.drawImage(XZ_RECT, self.buf_xz.qimg)
        p.drawImage(V3_RECT, self.buf_3d.qimg)

        # XY 视图上的簇中心：红=疑似跌倒，绿=正常
        fall_count = 0
        centers = []
        p.setPen(QtCore.Qt.NoPen)
        for l in uniq:
            m = labels == l
            c = pts3[m][:, :2].mean(axis=0)
            centers.append(c)
            zc = Z[m]
            zmax = float(np.max(zc))
            fall = (zmax < -0.4) and ((zmax - float(np.min(zc))) < 0.5)
            if fall:
                fall_count += 1
            p.setBrush(QtGui.QColor(255, 60, 60) if fall else QtGui.QColor(60, 255, 60))
            cx = XY_RECT.left() + int(np.clip((c[0] + 3) / 6.0, 0, 1) * (XY_RECT.width() - 1))
            cy = XY_RECT.bottom() - int(np.clip(c[1] / 6.0, 0, 1) * (XY_RECT.height() - 1))
            p.drawEllipse(QtCore.QPoint(cx, cy), 6, 6)
        p.end()

        self.plot_label.setPixmap(QtGui.QPixmap.fromImage(self.canvas))

        # 4) 状态文字（只在内容变化时才重设，省一次整块重绘）
        if centers:
            ca = np.asarray(centers, float).reshape(-1, 2)
            in_range = ((ca[:, 0] >= -3) & (ca[:, 0] <= 3) &
                        (ca[:, 1] >= 0) & (ca[:, 1] <= 6))
            count = int(np.count_nonzero(in_range))
        else:
            count = 0

        target_detected = len(X) > PERSON_MIN
        zmax, zmin = float(np.max(Z)), float(np.min(Z))
        # 去抖：噪点是一帧有一帧无，真人是持续存在的。
        # 连续 PERSON_HOLD 帧都满足才认为"有人"，连续同样多帧不满足才认为"没人"。
        # 默认 1 = 不去抖，和原程序一致。
        if PERSON_HOLD > 1:
            if target_detected:
                self._person_on += 1
                self._person_off = 0
            else:
                self._person_off += 1
                self._person_on = 0
            if self._person_on >= PERSON_HOLD:
                self._person_state = True
            elif self._person_off >= PERSON_HOLD:
                self._person_state = False
            target_detected = self._person_state
        if FALL_BY_CLUSTER:
            # 与"实时跌倒人数"同一套判据：有簇被判成跌倒才算跌倒，
            # 这样不会出现"人数 1、跌倒人数 0、状态却疑似跌倒"
            fall_detected = target_detected and fall_count > 0
        else:
            fall_detected = (zmax < 0.6) and ((zmax - zmin) < 0.4) and target_detected

        if not target_detected:
            status, scol = "状态：无目标", (150, 150, 150)
            state_en = "none"
        elif fall_detected:
            status, scol = "状态：检测到目标 - 疑似跌倒", (255, 60, 60)
            state_en = "fall"
        else:
            status, scol = "状态：检测到目标 - 正常活动", (60, 255, 60)
            state_en = "normal"

        self._set_status(count, fall_count, len(X), len(uniq), status, scol)

        # 给网页大屏的接口
        publish_state(online=True, person_detected=bool(target_detected),
                      fall_detected=bool(fall_detected), state=state_en,
                      state_text={"none": "无目标", "normal": "正常活动",
                                  "fall": "疑似跌倒"}[state_en],
                      person_count=count, fall_count=fall_count,
                      points=len(X), clusters=len(uniq),
                      bbox=[round(float(v), 2) for v in
                            (np.min(X), np.max(X), np.min(Y), np.max(Y), zmin, zmax)],
                      raw_points=raw_n, raw_bbox=raw_bbox,
                      fps=round(self.fps_n / max(time.time() - self.fps_t0, 1e-6), 1),
                      delay_ms=round(self._age_ms, 0),
                      radar_pkt_rate=round(self._pk_rate, 1),
                      radar_hit_rate=round(self._hit_rate, 1))

        self.fps_n += 1
        self._last_data_t = time.time()
        self._sec = ((t_db - t0) * 1000, (t_plot - t_db) * 1000, (time.perf_counter() - t_plot) * 1000)

    def _draw_empty(self, reason="雷达暂无点云", raw=None):
        """雷达暂时没有点云时，也要把图表和状态刷成空状态。

        雷达是 10 帧/秒一直在发的，没目标时帧里就是 0 个点（这种帧原程序会整帧丢掉），
        所以这里要说清是"没人"还是"没连线"。
        raw=(点数, 包围盒) 是过滤前的原始统计，放在接口里方便现场调阈值。
        """
        p = QtGui.QPainter(self.canvas)
        p.drawImage(0, 0, self.bg)
        p.end()
        self.plot_label.setPixmap(QtGui.QPixmap.fromImage(self.canvas))
        self._set_status(0, 0, 0, 0, "状态：无目标", (150, 150, 150), reason)
        # 空帧也算画了一帧：否则没人时统计里"点云 0.0 fps"，看着像程序卡死了
        self.fps_n += 1
        # 没点云也要把状态发出去：网页据此显示"无人"或"雷达离线"
        publish_state(online=self._pk_rate > 0.5, person_detected=False,
                      fall_detected=False, state="none",
                      state_text="无目标" if self._pk_rate > 0.5 else "雷达离线",
                      person_count=0, fall_count=0, points=0, clusters=0,
                      bbox=[0.0] * 6,
                      raw_points=(raw[0] if raw else 0),
                      raw_bbox=(raw[1] if raw else [0.0] * 6),
                      fps=round(self.fps_n / max(time.time() - self.fps_t0, 1e-6), 1),
                      delay_ms=round(self._age_ms, 0),
                      radar_pkt_rate=round(self._pk_rate, 1),
                      radar_hit_rate=round(self._hit_rate, 1))

    def _set_status(self, count, fall_count, npts, nclus, status, scol, note=""):
        """整块重画状态区：先黑底，再三行字。内容没变就不重贴，省一次重绘。"""
        key = (count, fall_count, npts, nclus, status, note)
        if key == self._status_text:
            return
        self._status_text = key

        img = self.status_canvas
        w, h = img.width(), img.height()
        p = QtGui.QPainter(img)
        p.fillRect(0, 0, w, h, BG)                  # 每次从黑底开始，绝不叠字
        p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        fam = self.ui_font or "DejaVu Sans"

        p.setFont(QtGui.QFont(fam, 13))
        p.setPen(QtGui.QColor(210, 210, 210))
        p.drawText(12, 34, f"范围内人数：{count}      实时跌倒人数：{fall_count}")
        p.drawText(12, 68, f"点数：{npts}      簇数：{nclus}")

        p.setFont(QtGui.QFont(fam, 24, QtGui.QFont.Bold))
        p.setPen(QtGui.QColor(*scol))
        p.drawText(12, 140, status)

        if note:
            p.setFont(QtGui.QFont(fam, 11))
            p.setPen(QtGui.QColor(130, 130, 130))
            p.drawText(12, 180, note)
        p.end()

        self.status_label.setPixmap(QtGui.QPixmap.fromImage(img))

    def _tick_fps(self, now=None):
        """心跳里统一打印，雷达有没有数据都能看到帧率。"""
        now = now or time.time()
        span = now - self.fps_t0
        if span < 1.5:
            return
        src = 0.0
        if self.cam_seq is not None:
            cur = self.cam_seq.value
            src = (cur - self._cam_seq_mark) / span
            self._cam_seq_mark = cur
        pkt_rate, hit_rate = self._pk_rate, self._hit_rate
        if PROFILE:
            c1, c2, c3 = self._sec
            radar = ""
            if self.hw is not None:
                radar = f" | 雷达 {pkt_rate:.0f} 包/秒（有点 {hit_rate:.0f}）"
            print(f"[lite] 点云 {self.fps_n / span:.1f} fps | 摄像头显示 "
                  f"{self.cam_n / span:.1f} fps / 源头 {src:.1f} fps"
                  f" | 聚类 {c1:.0f} | 栅格 {c2:.0f} | 合成 {c3:.0f}"
                  f" | 数据延迟 {self._age_ms:.0f} ms"
                  f" | 窗口跨度 {self._win_span_ms:.0f} ms{radar}", flush=True)
        self.fps_n = 0
        self.cam_n = 0
        self._cam_t = 0.0
        self.fps_t0 = now

    @staticmethod
    def _colors_by_z(Z):
        """与原程序 3D 视图一致的 z→颜色映射（灰→红→黄→绿），返回 (N,4) uint8。"""
        z = np.asarray(Z, float)
        n = len(z)
        out = np.zeros((n, 4), dtype=np.uint8)
        out[:, 3] = 255
        if n == 0:
            return out
        zmax = float(np.max(z))

        low = z < -1.0
        out[low] = (128, 128, 128, 255)

        m1 = (~low) & (z < -0.5)
        if m1.any():
            t = (z[m1] + 1.0) / 0.5
            out[m1, 0] = (0.5 + t * 0.5) * 255
            out[m1, 1] = (0.5 - t * 0.5) * 255
            out[m1, 2] = (0.5 - t * 0.5) * 255

        m2 = (z >= -0.5) & (z < -0.2)
        if m2.any():
            t = (z[m2] + 0.5) / 0.3
            out[m2, 0] = 255
            out[m2, 1] = t * 255
            out[m2, 2] = 0

        m3 = z >= -0.2
        if m3.any():
            denom = (zmax + 0.2) if (zmax + 0.2) > 1e-6 else 1e-6
            t = (z[m3] + 0.2) / denom
            out[m3, 0] = (1.0 - t) * 255
            out[m3, 1] = 255
            out[m3, 2] = 0
        return out


def main():
    # 滑动窗口默认 2 帧（原程序是 3 帧）。
    # 实测（板上真雷达，10 包/秒，前方有目标）：
    #   3 帧 -> 数据延迟中位 92 ms，窗口跨度 216 ms（拖影明显）、点云 9 fps、摄像头 19 fps
    #   2 帧 -> 数据延迟中位 46 ms，窗口跨度 100 ms、点云 10 fps、摄像头 24 fps
    #   1 帧 -> 数据延迟中位 51 ms，窗口跨度 0 ms、但点云只有单帧的点（约 11 个），偏稀
    # 所以默认取 2：延迟砍一半，点密度还比单帧翻倍。
    # 想要最跟手就 MMWAVE_WIN_FRAME=1；想要和原程序完全一致的密度就 =3。
    win_frame = int(os.environ.get("MMWAVE_WIN_FRAME", 2))

    app = QtWidgets.QApplication(sys.argv)
    fam = pick_cjk_font()
    if fam:
        app.setFont(QtGui.QFont(fam, 10))
        print(f"[lite] 界面字体 {fam}", flush=True)
    else:
        print("[lite] 警告：系统里没有中文字体，文字会显示成方块", flush=True)

    cam_shm = mp.RawArray('B', CAM_W * CAM_H * 4)      # RGB32：B,G,R,0xFF
    hw_stats = mp.RawArray('l', 3)                     # [雷达包数, 有点包数, 上帧点数]
    cam_seq = mp.Value('l', 0)
    stop_evt = mp.Event()

    # 独立采集进程只在 Linux（fork + 管道）上启用。
    # Windows 上是 spawn，而且受限环境可能不允许命名管道，这时退回线程模式
    # （功能一样，只是本地调试时 UI 会和采集抢 GIL，板上不会）。
    use_process = USE_PROC
    pair_q = None
    if use_process:
        try:
            pair_q = mp.Queue(maxsize=8)
        except Exception as e:
            print(f"[lite] 独立采集进程不可用（{e}），退回线程模式", flush=True)
            use_process = False
    if not use_process:
        pair_q = queue.Queue(maxsize=8)

    if use_process:
        worker = mp.Process(target=capture_worker,
                            args=(pair_q, stop_evt, cam_shm, cam_seq, hw_stats), daemon=True)
    else:
        import threading
        worker = threading.Thread(target=capture_worker,
                                  args=(pair_q, stop_evt, cam_shm, cam_seq, hw_stats), daemon=True)
    worker.start()

    # 状态接口（网页大屏用）：启动就先发一份"等待数据"，避免页面首屏报错
    if HTTP_PORT > 0:
        publish_state(online=False, person_detected=False, fall_detected=False,
                      state="none", state_text="等待数据", person_count=0, fall_count=0,
                      points=0, clusters=0, fps=0.0, delay_ms=0,
                      radar_pkt_rate=0.0, radar_hit_rate=0.0)
        try:
            serve_status(HTTP_PORT)
        except OSError as e:
            print(f"[lite] 状态接口起不来（端口 {HTTP_PORT}）：{e}", flush=True)

    win = LiteDashboard(pair_q, win_frame_num=win_frame, cam_shm=cam_shm, cam_seq=cam_seq,
                        hw_stats=hw_stats)
    win.show()
    try:
        sys.exit(app.exec_())
    finally:
        stop_evt.set()
        worker.join(timeout=3)
        if use_process and worker.is_alive():
            worker.terminate()
        print("程序已结束", flush=True)


if __name__ == "__main__":
    main()
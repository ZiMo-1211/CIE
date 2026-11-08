# -*- coding: utf-8 -*-
"""本地调试启动器：在 PC 上不用雷达也能跑毫米波程序。

用法（在 D:\\2026嵌入式\\代码\\毫米波 目录下）：

    python _local_debug\\local_launcher.py                          # 默认跑极速版
    python _local_debug\\local_launcher.py --entry video_real12.py   # 跑原版
    python _local_debug\\local_launcher.py --seconds 10 --snapshot out.png
    python _local_debug\\local_launcher.py --people 3               # 3 个人，加压
    python _local_debug\\local_launcher.py --real-cam --real-serial  # 用真硬件

--seconds  跑够 N 秒自动截图并退出（无人值守调试用）
--snapshot 截图存到哪个文件（用 Qt 自己 grab，不依赖桌面权限）
"""
import argparse
import os
import runpy
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 让 entry 能 import mkframe / fake_hw / radar_utils
for p in (ROOT, os.path.join(ROOT, "_k1_deploy"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

ENTRIES = ("video_real12.py", "video_real_k1.py", "video_real_k1_lite.py")


def _install_autoclose(seconds, snapshot):
    """在主线程里接管 QApplication.exec_：到点截图再退出。

    截图必须发生在 GUI 线程（子线程 grab() 会碰 OpenGL 上下文直接崩），
    所以这里用 QTimer 而不是后台线程。
    """
    from PyQt5 import QtCore, QtWidgets

    if getattr(QtWidgets.QApplication, "_dsh_autoclose", False):
        return
    QtWidgets.QApplication._dsh_autoclose = True

    orig_exec = QtWidgets.QApplication.exec_

    def exec_with_autoclose(self):
        def _finish():
            widgets = [w for w in self.topLevelWidgets() if w.isVisible()]
            if snapshot and widgets:
                w = max(widgets, key=lambda x: x.width() * x.height())
                ok = w.grab().save(snapshot)
                print(f"[调试] 截图 {w.width()}x{w.height()} -> {snapshot} "
                      f"{'保存成功' if ok else '保存失败'}", flush=True)
            print(f"[调试] {seconds:g} 秒到，自动退出", flush=True)
            self.quit()

        QtCore.QTimer.singleShot(int(seconds * 1000), _finish)
        try:
            return orig_exec(self)
        except TypeError:
            # PyQt5 里 QApplication.exec_ 是静态方法，不接受 self
            return orig_exec()

    QtWidgets.QApplication.exec_ = exec_with_autoclose


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--entry", default="video_real_k1_lite.py", help=f"要跑的入口，可选 {ENTRIES}")
    ap.add_argument("--seconds", type=float, default=0, help="跑多少秒后自动截图退出（0=一直跑）")
    ap.add_argument("--snapshot", default="", help="截图的保存路径")
    ap.add_argument("--people", type=int, default=1, help="假雷达造几个人（加压用）")
    ap.add_argument("--hz", type=float, default=10.0, help="假雷达出帧频率")
    ap.add_argument("--real-cam", action="store_true", help="用真摄像头（默认用假摄像头）")
    ap.add_argument("--real-serial", action="store_true", help="用真雷达串口（默认用假串口）")
    ap.add_argument("--profile", action="store_true", help="打印 fps 与耗时")
    ap.add_argument("--win", default="", help="窗口尺寸，如 1400x800")
    ap.add_argument("--proc", action="store_true", help="极速版强制用独立采集进程")
    ap.add_argument("--no-proc", action="store_true", help="极速版把采集放回主进程（对照）")
    args = ap.parse_args()

    entry = os.path.basename(args.entry) if args.entry not in ENTRIES else args.entry
    # 入口可能在根目录，也可能在 v16/ 里，依次找
    path = None
    for d in (ROOT, os.path.join(ROOT, "v16"), os.path.join(ROOT, "_k1_deploy")):
        cand = os.path.join(d, entry)
        if os.path.exists(cand):
            path = cand
            break
    if path is None:
        print(f"找不到入口文件：{entry}（在 {ROOT} 和 v16/ 下都没找到）")
        return 2
    # 入口所在目录加进 sys.path，保证能 import radar_utils 等
    entry_dir = os.path.dirname(path)
    if entry_dir not in sys.path:
        sys.path.insert(0, entry_dir)

    # Qt 绑定锁死（这个项目踩过 PyQt5/PyQt6 混用导致段子的坑）
    os.environ["QT_API"] = "pyqt5"
    os.environ["PYQTGRAPH_QT_LIB"] = "PyQt5"

    os.environ["FAKE_PEOPLE"] = str(args.people)
    os.environ["FAKE_RADAR_HZ"] = str(args.hz)
    if args.profile:
        os.environ["MMWAVE_PROFILE"] = "1"
    if args.win:
        os.environ["MMWAVE_WIN"] = args.win
    if args.proc:
        os.environ["MMWAVE_PROC"] = "1"
    if args.no_proc:
        os.environ["MMWAVE_PROC"] = "0"

    synth = not (args.real_cam and args.real_serial)
    if synth:
        import fake_hw

        fake_hw.install(serial_on=not args.real_serial, camera=not args.real_cam)
        # 极速版的采集进程是独立进程（Windows 上是 spawn），子进程要自己再装一遍
        os.environ["MMWAVE_SYNTH_HW"] = "1" if synth else "0"

    if args.seconds > 0:
        _install_autoclose(args.seconds, args.snapshot)

    print(f"[调试] 运行 {entry}（{args.seconds:g}s 后自动退出）" if args.seconds
          else f"[调试] 运行 {entry}", flush=True)
    os.chdir(ROOT)
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit:
        pass
    print("[调试] 已退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
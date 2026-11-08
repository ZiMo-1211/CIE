# -*- coding: utf-8 -*-
"""统一帧率探针：用同一套量法测任意一个版本的界面帧率。

原程序自己没有帧率统计，各版本的 profile 口径也不完全一样，
这个探针直接包住可视化类的 update_visualization，数真实刷新次数。

用法（板上和 PC 都能用）：
    python3 _local_debug/fps_probe.py --entry video_real12.py --seconds 15
    python3 _local_debug/fps_probe.py --entry video_real_k1_lite.py --seconds 15 --snapshot a.png
"""
import argparse
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "v16"), os.path.join(ROOT, "_k1_deploy"), HERE):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)


def load_entry(entry):
    path = None
    for d in (ROOT, os.path.join(ROOT, "v16"), os.path.join(ROOT, "_k1_deploy")):
        cand = os.path.join(d, entry)
        if os.path.exists(cand):
            path = cand
            break
    if path is None:
        raise SystemExit(f"找不到入口 {entry}")
    if os.path.dirname(path) not in sys.path:
        sys.path.insert(0, os.path.dirname(path))
    spec = importlib.util.spec_from_file_location("entry_mod", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["entry_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def patch_visualizers(mod, stats):
    """包住可视化类，分别统计「定时器调用」和「真正拿到新数据的帧」。"""
    patched = []
    for name in dir(mod):
        obj = getattr(mod, name)
        if not isinstance(obj, type):
            continue
        fn = getattr(obj, "update_visualization", None)
        if fn is None or getattr(fn, "_probe", False):
            continue

        def make(orig):
            def wrapper(self, *a, **kw):
                t = time.perf_counter()
                try:
                    return orig(self, *a, **kw)
                finally:
                    dt = time.perf_counter() - t
                    stats["n"] += 1
                    stats["t"] += dt
                    stats["max"] = max(stats["max"], dt)

            wrapper._probe = True
            return wrapper

        setattr(obj, "update_visualization", make(fn))
        patched.append(name)

    # data_process 返回「有新数据」才算真正画了一帧。
    # 各版本返回的最后一个字段都是图像：有图像 = 有新帧。
    for name in dir(mod):
        obj = getattr(mod, name)
        if not isinstance(obj, type):
            continue
        fn = getattr(obj, "data_process", None)
        if fn is None or getattr(fn, "_probe", False):
            continue

        def make_dp(orig):
            def wrapper(self, *a, **kw):
                res = orig(self, *a, **kw)
                try:
                    has = res is not None and res[-1] is not None
                except Exception:
                    has = res is not None
                if has:
                    stats["frames"] += 1
                return res

            wrapper._probe = True
            return wrapper

        setattr(obj, "data_process", make_dp(fn))
        patched.append(name + ".data_process")
    return patched


def install_autoclose(seconds, snapshot, stats):
    from PyQt5 import QtCore, QtWidgets

    orig_exec = QtWidgets.QApplication.exec_

    def exec_with_autoclose(self):
        t0 = time.time()

        def _finish():
            span = time.time() - t0
            if snapshot:
                ws = [w for w in self.topLevelWidgets() if w.isVisible()]
                if ws:
                    w = max(ws, key=lambda x: x.width() * x.height())
                    w.grab().save(snapshot)
                    print(f"[探针] 截图 {w.width()}x{w.height()} -> {snapshot}", flush=True)
            n = stats["n"]
            f = stats["frames"]
            if n:
                line = (f"[探针] 真实数据帧 {f} / {span:.1f} s = {f / span:.1f} fps"
                        f"  | 定时器调用 {n / span:.1f} 次/s"
                        f"  | 单帧平均 {stats['t'] / n * 1000:.1f} ms"
                        f"  | 最大 {stats['max'] * 1000:.1f} ms")
                print(line, flush=True)
            else:
                print(f"[探针] {span:.1f} s 内一次都没刷新（界面没有数据）", flush=True)
            self.quit()

        QtCore.QTimer.singleShot(int(seconds * 1000), _finish)
        try:
            return orig_exec(self)
        except TypeError:
            return orig_exec()

    QtWidgets.QApplication.exec_ = exec_with_autoclose


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", default="video_real_k1_lite.py")
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--snapshot", default="")
    ap.add_argument("--real-cam", action="store_true")
    ap.add_argument("--real-serial", action="store_true")
    ap.add_argument("--people", type=int, default=1)
    ap.add_argument("--hz", type=float, default=10.0)
    args = ap.parse_args()

    os.environ["QT_API"] = "pyqt5"
    os.environ["PYQTGRAPH_QT_LIB"] = "PyQt5"
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["FAKE_PEOPLE"] = str(args.people)
    os.environ["FAKE_RADAR_HZ"] = str(args.hz)

    if not (args.real_cam and args.real_serial):
        import fake_hw

        fake_hw.install(serial_on=not args.real_serial, camera=not args.real_cam)
        os.environ["MMWAVE_SYNTH_HW"] = "1"

    mod = load_entry(args.entry)
    stats = {"n": 0, "t": 0.0, "max": 0.0, "frames": 0}
    patched = patch_visualizers(mod, stats)
    print(f"[探针] {args.entry}  可视化类{patched}  跑 {args.seconds:g} 秒", flush=True)

    install_autoclose(args.seconds, args.snapshot, stats)
    try:
        mod.main()
    except SystemExit:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
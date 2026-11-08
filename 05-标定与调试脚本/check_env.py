# -*- coding: utf-8 -*-
"""检查本地调试环境：Python 版本 + 毫米波程序需要的第三方包是否齐全。"""
import importlib
import sys

print("Python", sys.version.split()[0])
print("解释器", sys.executable)

NEED = {
    "numpy": "numpy",
    "cv2": "opencv-python",
    "PyQt5": "PyQt5",
    "pyqtgraph": "pyqtgraph",
    "OpenGL": "PyOpenGL",
    "sklearn": "scikit-learn",
    "matplotlib": "matplotlib",
    "serial": "pyserial",
}

missing = []
for mod, pipname in NEED.items():
    try:
        m = importlib.import_module(mod)
        print("  有  %-12s %-12s (pip: %s)" % (mod, getattr(m, "__version__", "?"), pipname))
    except Exception as e:
        print("  缺  %-12s %-12s (pip: %s)  %s" % (mod, "", pipname, type(e).__name__))
        missing.append(pipname)

print()
if missing:
    print("需要安装：", " ".join(missing))
else:
    print("依赖齐全，可以直接本地调试。")

try:
    from PyQt5 import QtCore

    print("PyQt5 自带 Qt 版本:", QtCore.QT_VERSION_STR)
except Exception as e:
    print("PyQt5 不可用:", e)

try:
    import PyQt6  # noqa: F401

    print("注意：环境里同时存在 PyQt6，运行时必须设 QT_API=pyqt5 / PYQTGRAPH_QT_LIB=PyQt5")
except ImportError:
    print("没有 PyQt6，不存在 Qt 绑定混用问题。")
#!/usr/bin/env bash
# 在 K1 板子上「原生」运行毫米波程序（不进 Docker，直接用系统 Python）
#
#   bash run_native.sh           原版 video_real12.py
#   bash run_native.sh fast      渲染优化版
#   bash run_native.sh lite      极速版（整屏自绘 + 采集独立进程）← 推荐
#
# 依赖（系统 Python，用 apt 装）：
#   sudo apt-get install -y python3-numpy python3-opencv python3-pyqt5 \
#        python3-pyqtgraph python3-opengl python3-sklearn python3-matplotlib python3-serial
#
# 界面显示方式可以用 QT_QPA_PLATFORM 切换，原生环境比 Docker 多一个选择：
#   QT_QPA_PLATFORM=xcb      走 XWayland（默认，兼容性最好）
#   QT_QPA_PLATFORM=wayland  走原生 Wayland（需要 qtwayland5，通常能省一层开销）
set -u
cd "$(dirname "$(readlink -f "$0")")"

export QT_API=pyqt5
export PYQTGRAPH_QT_LIB=PyQt5
export MPLBACKEND=Agg
export DISPLAY="${DISPLAY:-:0}"

ENTRY="${MMWAVE_ENTRY:-video_real12.py}"
case "${1:-}" in
    fast)
        ENTRY=video_real_k1.py
        echo ">>> 版本：渲染优化版（pyqtgraph 界面，只改渲染）"
        ;;
    lite)
        ENTRY=video_real_k1_lite.py
        echo ">>> 版本：极速版（整屏自绘 + 采集独立进程）"
        ;;
    [a-z]*.py)
        ENTRY="$1"
        echo ">>> 版本：$ENTRY"
        ;;
esac

# ---- 环境自检：缺包就直接告诉你怎么装 ----
python3 - "$ENTRY" <<'PY'
import sys
need = {"numpy": "python3-numpy", "cv2": "python3-opencv", "PyQt5": "python3-pyqt5",
        "pyqtgraph": "python3-pyqtgraph", "OpenGL": "python3-opengl",
        "sklearn": "python3-sklearn", "matplotlib": "python3-matplotlib",
        "serial": "python3-serial"}
missing = []
for mod, pkg in need.items():
    try:
        __import__(mod)
    except Exception:
        missing.append(pkg)
if missing:
    print(">>> 缺少依赖，先执行：")
    print("    sudo apt-get install -y " + " ".join(missing))
    sys.exit(1)

# 极速版用 multiprocessing，确认 Qt 绑定没被 pyqtgraph 拽到 PyQt6
import os
try:
    import PyQt6  # noqa: F401
    if os.environ.get("QT_API") != "pyqt5":
        print(">>> 警告：环境里同时有 PyQt6，已强制 QT_API=pyqt5 / PYQTGRAPH_QT_LIB=PyQt5")
except ImportError:
    pass
PY
[ $? -ne 0 ] && exit 1

# ---- 放行本地 X 连接（xcb 方式需要）----
if command -v xhost >/dev/null 2>&1; then
    DISPLAY="$DISPLAY" xhost +local: >/dev/null 2>&1 || true
fi

# ---- 自动探测雷达串口 ----
SERIAL_REAL="${MMWAVE_SERIAL:-}"
if [ -z "$SERIAL_REAL" ]; then
    echo ">>> 自动探测毫米波串口 ..."
    SERIAL_REAL=$(python3 pick_serial.py 2>/dev/null | tail -1)
    SERIAL_REAL="${SERIAL_REAL:-/dev/ttyACM0}"
fi
echo ">>> 串口 $SERIAL_REAL"

# ---- 自动探测摄像头 ----
# 注意别兜底成 0：VideoCapture(0) 在 K1 上不是摄像头，会报
# GStreamer "no element 0" 然后整个摄像链路起不来。探测不到就留空，
# 交给 launcher.py 自己扫 /dev/video*（它也会拒绝非 /dev/ 开头的值）。
CAM="${MMWAVE_CAMERA:-}"
if [ -z "$CAM" ]; then
    CAM=$(python3 pick_camera.py 2>/dev/null | tail -1)
    case "$CAM" in
        /dev/*) ;;
        *)      CAM="" ;;
    esac
fi
if [ -n "$CAM" ]; then
    echo ">>> 摄像头 $CAM"
else
    echo ">>> 摄像头 未探测到（界面只显示点云，dev/video* 能用了会自动认）"
fi

export MMWAVE_SERIAL="$SERIAL_REAL"
export MMWAVE_CAMERA="$CAM"
export MMWAVE_ENTRY="$ENTRY"

# ---- 判定参数（现场实测调出来的默认值，想改就自己 export 覆盖）----
# 原程序是 len(X)>10 就算有人，实测空场地上雷达正前方 0.17~0.50 m 处
# 恒定有 15~29 个近场杂波点（桌面/支架/旁边设备），于是没人也一直报"有人 + 跌倒"。
#   MMWAVE_MIN_RANGE     扔掉 0.6 m 以内的点（那些杂波全在这里面）
#   MMWAVE_PERSON_MIN    0.6 m 以外还要有 30 个点才算有人
#   MMWAVE_PERSON_HOLD   连续 5 帧（约 0.5 秒）都满足才翻转状态，滤掉单帧噪点
export MMWAVE_MIN_RANGE="${MMWAVE_MIN_RANGE:-0.6}"
export MMWAVE_PERSON_MIN="${MMWAVE_PERSON_MIN:-30}"
export MMWAVE_PERSON_HOLD="${MMWAVE_PERSON_HOLD:-5}"
echo ">>> 判定：近场门限 ${MMWAVE_MIN_RANGE} m / 最少点数 ${MMWAVE_PERSON_MIN} / 去抖 ${MMWAVE_PERSON_HOLD} 帧"

echo ">>> 原生启动 $ENTRY（QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-默认}）"
exec python3 launcher.py
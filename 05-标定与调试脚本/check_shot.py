# -*- coding: utf-8 -*-
"""分析一张界面截图：各区域是否真的画出了东西（本地调试和板上都能用）。

    python check_shot.py shot.png

按 1400x800 的布局切区域（自动按实际分辨率缩放），输出非黑像素数和颜色统计。
"""
import sys

import cv2
import numpy as np

BASE_W, BASE_H = 1400, 800

# (名字, x, y, w, h) —— 与界面布局一致
REGIONS = [
    ("摄像头", 10, 30, 640, 480),
    ("XY 图", 662, 34, 348, 360),
    ("YZ 图", 1022, 34, 368, 360),
    ("XZ 图", 662, 424, 348, 330),
    ("3D 视图", 1022, 424, 368, 330),
]


def main(path):
    im = cv2.imread(path)
    if im is None:
        print(f"读不到图片：{path}")
        return 1
    h, w = im.shape[:2]
    sx, sy = w / BASE_W, h / BASE_H
    b = im[:, :, 0].astype(int)
    g = im[:, :, 1].astype(int)
    r = im[:, :, 2].astype(int)
    nonzero = (im.sum(axis=2) > 60)

    print(f"{path}  {w}x{h}  非黑像素 {int(nonzero.sum())} ({nonzero.mean() * 100:.1f}%)")
    print(f"  绿 {(g > 120) & (r < 100) & (b < 110)}".replace(" True", "") if False else
          f"  绿点 {(int(((g > 120) & (r < 100) & (b < 110)).sum()))}"
          f"  红点 {(int(((r > 120) & (g < 80) & (b < 80)).sum()))}"
          f"  蓝点 {(int(((b > 120) & (r < 80) & (g < 110)).sum()))}"
          f"  亮点 {(int(((r > 180) & (g > 180) & (b > 180)).sum()))}")

    for name, x, y, rw, rh in REGIONS:
        x0, y0 = int(x * sx), int(y * sy)
        x1, y1 = int((x + rw) * sx), int((y + rh) * sy)
        x1, y1 = min(x1, w), min(y1, h)
        if x1 <= x0 or y1 <= y0:
            continue
        sub = nonzero[y0:y1, x0:x1]
        sub_im = im[y0:y1, x0:x1]
        mean = sub_im.reshape(-1, 3).mean(axis=0)[::-1]  # BGR -> RGB
        flag = "有内容" if sub.mean() > 0.02 else "空"
        print(f"  {name:8s} 区域 {x1 - x0:4d}x{y1 - y0:<4d} 非黑 {int(sub.sum()):7d} "
              f"({sub.mean() * 100:5.1f}%) 平均RGB=({mean[0]:.0f},{mean[1]:.0f},{mean[2]:.0f})  {flag}")

    # 底部状态栏是否有文字（状态那一行的非黑像素）
    y0 = int(560 * sy)
    y1 = min(int(660 * sy), h)
    status = nonzero[y0:y1, int(10 * sx):int(900 * sx)]
    print(f"  状态文字区 非黑 {int(status.sum())}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "shot.png"))
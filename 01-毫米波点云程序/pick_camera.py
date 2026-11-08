#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动挑出真正能出图的摄像头节点（K1 上 /dev/video0 不是摄像头）。

打印第一个能 read() 到画面的设备路径；没有则什么也不打印。
"""
import glob
import sys

import cv2


def main():
    devs = sorted(glob.glob("/dev/video[0-9]*"),
                  key=lambda s: int(s.replace("/dev/video", "")))
    for d in devs:
        cap = cv2.VideoCapture(d, cv2.CAP_V4L2)
        try:
            if cap.isOpened():
                ok, frame = cap.read()
                if ok and frame is not None and frame.size:
                    print(d)
                    return 0
        finally:
            cap.release()
    return 1


if __name__ == "__main__":
    sys.exit(main())
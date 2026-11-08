#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动挑出真正输出毫米波报文的串口。

依次打开 /dev/ttyACM* 和 /dev/ttyUSB*，用正确的波特率（默认 2000000）读 1 秒，
谁的字节流里出现包头 DCFFEEFF 就打印谁。找不到则返回非 0。
"""
import glob
import os
import sys
import time

import serial

BAUD = int(os.environ.get("MMWAVE_BAUD", "2000000"))
HEADER = bytes.fromhex("DCFFEEFF")


def try_port(dev):
    try:
        ser = serial.Serial(port=dev, baudrate=BAUD, bytesize=8, parity="N",
                            stopbits=1, timeout=0.2)
    except Exception:
        return False, 0
    try:
        time.sleep(0.2)
        ser.reset_input_buffer()
        data = b""
        t0 = time.time()
        while time.time() - t0 < 1.0:
            chunk = ser.read(4096)
            if chunk:
                data += chunk
                if HEADER in data:
                    return True, len(data)
    finally:
        ser.close()
    return False, len(data)


def main():
    devs = sorted(glob.glob("/dev/ttyACM*"),
                  key=lambda s: int(s.replace("/dev/ttyACM", ""))) + \
           sorted(glob.glob("/dev/ttyUSB*"),
                  key=lambda s: int(s.replace("/dev/ttyUSB", "")))
    if not devs:
        print("", end="")
        return 1
    for d in devs:
        ok, n = try_port(d)
        print(f"  检查 {d}: {n} 字节, 包头={'有' if ok else '无'}", file=sys.stderr)
        if ok:
            print(d)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
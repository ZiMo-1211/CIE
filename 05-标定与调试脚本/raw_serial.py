#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最原始的串口字节测试：不管协议，只看有没有字节进来。

    python3 raw_serial.py /dev/ttyACM0        # 默认 2000000
    python3 raw_serial.py /dev/ttyACM0 115200 # 指定波特率
"""
import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
baud = int(sys.argv[2]) if len(sys.argv) > 2 else 2000000

print(f"打开 {port} @ {baud} ...")
try:
    ser = serial.Serial(port=port, baudrate=baud, bytesize=8, parity="N",
                        stopbits=1, timeout=0.2, rtscts=False, dsrdtr=False)
except Exception as e:
    print(f"  打开失败：{e}")
    sys.exit(1)

print(f"  is_open={ser.is_open}  DTR={ser.dtr} RTS={ser.rts}")

# 给设备一点时间稳定
time.sleep(0.5)
ser.reset_input_buffer()
try:
    ser.dtr = True
    ser.rts = True
except Exception:
    pass

data = b""
t0 = time.time()
while time.time() - t0 < 3.0:
    chunk = ser.read(4096)
    if chunk:
        data += chunk
    if len(data) > 200000:
        break
ser.close()

print(f"3 秒共收到 {len(data)} 字节")
if data:
    print(f"  前 48 字节: {data[:48].hex()}")
    print(f"  含包头 DCFFEEFF: {'dcffeeff' in data.hex()}")
    print(f"  含点云结束 C4FEDDFF: {'c4feddff' in data.hex()}")
else:
    print("  （一个字节都没收到）")
sys.exit(0 if data else 2)
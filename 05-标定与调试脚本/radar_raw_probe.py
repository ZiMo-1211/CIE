# -*- coding: utf-8 -*-
"""抓雷达串口原始字节，离线用原程序的解析器逐帧分析：到底发了多少帧、丢了多少帧、为什么丢。

    python3 radar_raw_probe.py /dev/ttyACM0 20
"""
import os
import sys
import time

import serial

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import video_real12 as V

dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

print(f"抓 {dev} {dur:.0f} 秒原始字节（2000000 bps）...")
ser = serial.Serial(dev, 2000000, timeout=0.2)
buf = bytearray()
t0 = time.time()
last_report = t0
while time.time() - t0 < dur:
    n = ser.in_waiting
    if n:
        buf += ser.read(n)
    else:
        time.sleep(0.002)
    if time.time() - last_report > 5:
        print(f"  {time.time() - t0:4.1f}s  累计 {len(buf)} 字节", flush=True)
        last_report = time.time()
ser.close()
raw = bytes(buf)
el = time.time() - t0
print(f"共 {len(raw)} 字节 / {el:.1f} 秒 = {len(raw) / el / 1024:.1f} KB/s")

HEAD = b"\xdc\xff\xee\xff"
STOP = b"\xc4\xfe\xdd\xff"
n_head = raw.count(HEAD)
n_stop = raw.count(STOP)
print(f"包头 {HEAD.hex()} 出现 {n_head} 次 = {n_head / el:.1f} 次/秒")
print(f"包尾 {STOP.hex()} 出现 {n_stop} 次")
print()

# 按包头切帧，用原程序的解析器逐帧试
obj = V.RadarCapture.__new__(V.RadarCapture)      # 只用解析方法，不需要初始化串口
pos = []
i = raw.find(HEAD)
while i >= 0:
    pos.append(i)
    i = raw.find(HEAD, i + 1)
print(f"按包头切出 {len(pos)} 帧（平均每帧 {(len(raw) / max(len(pos), 1)):.0f} 字节）")
print()

ok = 0
empty = 0
fail_head = 0
fail_pnum = 0
fails = []
counts = []
samples = []
for k, p in enumerate(pos):
    end = pos[k + 1] if k + 1 < len(pos) else len(raw)
    seg = raw[p:end]
    pretty = " ".join(f"{b:02X}" for b in seg)
    # 注意：原程序的 extract_head 期望「无空格」的 hex 串，
    # jiexi_targets 内部会自己去掉空格，所以这里直接交给 jiexi_targets。
    try:
        pts = obj.jiexi_targets(pretty, "t")
    except Exception as e:
        fail_pnum += 1
        if len(fails) < 3:
            fails.append(f"帧{k + 1}: 解析异常 {type(e).__name__}: {e}")
        continue
    if pts is None:
        fail_head += 1
        if len(fails) < 4:
            fails.append(f"帧{k + 1}: 结构不合法（{len(seg)} 字节）")
        if len(samples) < 2:
            samples.append(seg[:80].hex(" "))
        continue
    counts.append(len(pts))
    ok += 1
    if len(pts) == 0:
        empty += 1

print("== 逐帧解析结果 ==")
print(f"  成功解析: {ok} / {len(pos)} = {ok / max(len(pos), 1) * 100:.0f}%")
print(f"    有有效点: {ok - empty}")
print(f"    解析出 0 点: {empty}")
print(f"  结构不合法: {fail_head}")
print(f"  解析异常/序号不匹配: {fail_pnum}")
for f in fails:
    print("   ", f)
if samples:
    print(f"   不合法帧样例（前80字节）: {samples[0]}")
if counts:
    import statistics
    print(f"  每帧点数: 最少 {min(counts)} / 最多 {max(counts)} / "
          f"中位 {int(statistics.median(counts))} / 平均 {sum(counts) / len(counts):.1f}")
    print(f"  有点帧数（≥10 点）: {sum(1 for c in counts if c >= 10)}")
print()
print(f"结论：雷达 {n_head / el:.1f} 帧/秒，解析出 {ok / el:.1f} 帧/秒，"
      f"其中每帧平均 {sum(counts) / max(len(counts), 1):.1f} 个点")
if sum(counts) / max(len(counts), 1) < 10:
    print("  → 雷达在正常发帧，但几乎探不到点：前方没有目标，或目标在探测范围外")
elif n_head / max(el, 1) > 2 * ok / max(el, 1):
    print("  ⚠ 解析器丢帧严重 —— 程序侧问题")
else:
    print("  ✅ 雷达 + 解析都正常，点云应该有显示")
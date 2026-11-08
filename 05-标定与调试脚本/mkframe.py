# -*- coding: utf-8 -*-
"""按原程序（video_real12.py）的协议格式编码完整报文。

原程序的解析规则（逐条对应，见 video_real12.py）：

  extract_head   r'DCFFEEFF(.*?)D3FFEEFF'，中间必须正好 40 个 hex（20 字节）
  jiexi_head     head 字节序反转后，bits[-74:-64]（10 位）必须 == bits[-94:-84]，
                 这个 10 位值就是点数 points_num
  extract_bpoint r'CBFEDDFF(.*?)C4FEDDFF'，中间按 40 hex 一段切成点
  check_point_number  第 i 个点的点号必须等于 i（从 0 开始）
  jiexi_bpoint   body 字节序反转后：
                   point_number = uint16(bits[-10:])          10 位
                   snr          = uint16(bits[-25:-10])       15 位
                   distance     = uint32(bits[-64:-32]) / 100 米
                   speed        = int16 (bits[-79:-64]) / 100 米/秒   （原值 - 16384）
                   azimuth      = int16 (bits[-94:-79]) / 100 度      （原值 - 16384）
                   elevation    = int16(bits[-111:-96]) / 100 度       （原值 - 16384）

本模块用同样的位布局编码，并自带一个"用原程序逻辑解回来"的自检。
"""
import math
import struct

HEADER = bytes.fromhex("DCFFEEFF")
HEAD_END = bytes.fromhex("D3FFEEFF")
POINT_START = bytes.fromhex("CBFEDDFF")
POINT_STOP = bytes.fromhex("C4FEDDFF")

# 原程序的过滤阈值（video_real12.py 里的 r_action_min / X_lim / Y_lim / Z_lim）
R_ACTION_MIN = 0.05
X_LIM, Y_LIM, Z_LIM = 3.0, 6.0, 3.0


def _bits_to_bytes(bits):
    return int("".join(bits), 2).to_bytes(20, "big")


def _put(bits, start, length, value):
    s = format(int(value) & ((1 << length) - 1), "0%db" % length)
    bits[start:start + length] = list(s)


def encode_head(n_points):
    """20 字节头部：两个 10 位字段都填点数。"""
    bits = ["0"] * 160
    _put(bits, 86, 10, n_points)   # bits[-74:-64]
    _put(bits, 66, 10, n_points)   # bits[-94:-84]
    return _bits_to_bytes(bits)[::-1]


def encode_point(idx, dist, az, el, speed, snr):
    """一个点：20 字节。dist 米、az/el 度、speed 米/秒。"""
    bits = ["0"] * 160
    _put(bits, 150, 10, idx)                                # point_number
    _put(bits, 135, 15, max(0, min(32767, int(snr))))       # SNR
    _put(bits, 96, 32, max(0, int(round(dist * 100))))      # 距离（厘米）
    _put(bits, 81, 15, int(round(speed * 100)) + 16384)     # 速度
    _put(bits, 66, 15, int(round(az * 100)) + 16384)        # 方位角
    _put(bits, 49, 15, int(round(el * 100)) + 16384)        # 俯仰角
    return _bits_to_bytes(bits)[::-1]


def sph2cart(r, a, v):
    arad, vrad = math.radians(a), math.radians(v)
    return (r * math.cos(vrad) * math.sin(arad),
            r * math.cos(vrad) * math.cos(arad),
            r * math.sin(vrad))


def points_to_polar(xyz):
    """直角坐标 → (距离, 方位角, 俯仰角)，用于把想要的 xyz 反算成极坐标。"""
    x, y, z = xyz
    r = math.sqrt(x * x + y * y + z * z)
    if r < 1e-9:
        return 0.0, 0.0, 0.0
    az = math.degrees(math.atan2(x, y))
    el = math.degrees(math.asin(max(-1.0, min(1.0, z / r))))
    return r, az, el


def build_frame(points):
    """points: [(idx, x, y, z, speed, snr), ...] → 一条完整报文。"""
    body = b"".join(
        encode_point(idx, *points_to_polar((x, y, z)), speed=speed, snr=snr)
        for idx, x, y, z, speed, snr in points
    )
    return HEADER + encode_head(len(points)) + HEAD_END + POINT_START + body + POINT_STOP


# ---------------- 自检：用原程序的解析逻辑解回来 ----------------
def _big_to_little_endian(hex_str):
    if len(hex_str) % 2:
        hex_str = "0" + hex_str
    return "".join(reversed([hex_str[i:i + 2] for i in range(0, len(hex_str), 2)]))


def _hex_to_bit(hex_str):
    return "".join(f"{int(c, 16):04b}" for c in hex_str)


def _int16(bin_str):
    num = int(bin_str, 2)
    if num >= 2 ** 15:
        num -= 2 ** 16
    return num - 16384


def parse_like_original(frame):
    """完全照抄 video_real12.py 的解析步骤（含断言），返回点列表。"""
    import re
    hexs = frame.hex().upper()
    m = re.findall(r"DCFFEEFF(.*?)D3FFEEFF", hexs)
    assert len(m) == 1 and len(m[0]) == 40, f"头部不合法：{m}"
    head_bits = _hex_to_bit(_big_to_little_endian(m[0]))
    a = int(head_bits[-74:-64], 2)
    b = int(head_bits[-94:-84], 2)
    assert a == b, f"BK点数({a}) != raw_number({b})"
    n = a

    c = re.findall(r"CBFEDDFF(.*?)C4FEDDFF", hexs)
    assert len(c) == 1, f"点云段数量异常：{len(c)}"
    chunks = [c[0][i:i + 40] for i in range(0, len(c[0]), 40) if len(c[0][i:i + 40]) == 40]
    assert len(chunks) == n, f"点数不符：头部 {n}，实际 {len(chunks)}"

    out = []
    for i, ch in enumerate(chunks):
        bits = _hex_to_bit(_big_to_little_endian(ch))
        pn = int(bits[-10:], 2)
        assert pn == i, f"点号不匹配：期望 {i}，实际 {pn}"
        snr = int(bits[-25:-10], 2)
        dist = int(bits[-64:-32], 2) / 100
        speed = _int16(bits[-79:-64]) / 100
        az = _int16(bits[-94:-79]) / 100
        el = _int16(bits[-111:-96]) / 100
        x, y, z = sph2cart(dist, az, el)
        out.append(dict(i=i, x=x, y=y, z=z, v=speed, snr=snr))
    return out


def _selftest():
    import random
    random.seed(0)
    ok = 0
    for trial in range(200):
        n = random.randint(1, 30)
        pts = []
        for i in range(n):
            x = random.uniform(-2.0, 2.0)
            y = random.uniform(0.4, 5.0)
            z = random.uniform(-1.0, 2.0)
            pts.append((i, x, y, z, random.uniform(-2, 2), random.randint(100, 20000)))
        frame = build_frame(pts)
        got = parse_like_original(frame)
        assert len(got) == len(pts), (len(got), len(pts))
        for g, (i, x, y, z, v, snr) in zip(got, pts):
            # 极坐标量化到 1 cm / 0.01°，允许 2 cm 误差
            assert abs(g["x"] - x) < 0.02 and abs(g["y"] - y) < 0.02 and abs(g["z"] - z) < 0.02, \
                (g, x, y, z)
            assert abs(g["v"] - v) < 0.011, (g["v"], v)
            assert g["snr"] == snr, (g["snr"], snr)
        ok += 1
    print(f"自检通过：{ok}/200 条报文，坐标误差 < 2 cm，速度误差 < 1 cm/s，SNR 精确")


if __name__ == "__main__":
    _selftest()
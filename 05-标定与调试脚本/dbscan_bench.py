#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测 DBSCAN 在 K1 上各种参数/算法组合的耗时（2D 与 3D）。"""
import time

import numpy as np
from sklearn.cluster import DBSCAN


def bench(pts, eps, min_samples, algorithm, leaf_size=30):
    ts = []
    for _ in range(8):
        t0 = time.perf_counter()
        DBSCAN(eps=eps, min_samples=min_samples, algorithm=algorithm,
               leaf_size=leaf_size).fit(pts).labels_
        ts.append((time.perf_counter() - t0) * 1000)
    return float(np.median(ts))


rng = np.random.default_rng(0)


def blob(n, dim):
    """造一团有结构的点：3 个人形簇 + 少量噪声。"""
    parts = []
    for cx in (-1.0, 0.0, 1.0):
        m = n // 3
        parts.append(rng.normal(loc=[cx] + [2.0] + [0.5] * (dim - 2), scale=0.15, size=(m, dim)))
    rest = n - sum(len(p) for p in parts)
    if rest > 0:
        parts.append(rng.normal(0, 2.0, size=(rest, dim)))
    return np.vstack(parts)


for n in (58, 174, 300):
    p2 = blob(n, 2)
    p3 = blob(n, 3)
    print(f"\n=== {n} 点 ===")
    for algo in ('auto', 'kd_tree', 'ball_tree', 'brute'):
        t2 = bench(p2, 0.4, 20, algo)
        t3 = bench(p3, 0.5, 30, algo)
        print(f"  {algo:<10} 2D {t2:7.1f} ms   3D {t3:7.1f} ms")

# leaf_size 影响
p2 = blob(174, 2)
print("\n=== 174 点 2D，kd_tree 的 leaf_size ===")
for ls in (8, 16, 30, 60):
    print(f"  leaf_size={ls:<3} {bench(p2, 0.4, 20, 'kd_tree', ls):7.1f} ms")

# 点数线性度（kd_tree）
print("\n=== kd_tree 随点数变化（2D, eps=0.4, min=20）===")
for n in (30, 60, 120, 174, 300, 600, 1200):
    print(f"  {n:>5} 点  {bench(blob(n, 2), 0.4, 20, 'kd_tree'):7.1f} ms")
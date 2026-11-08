# -*- coding: utf-8 -*-
"""毫米波点云后处理工具。

原工程里 `from radar_utils import cluster_filter` 引用了本模块，但仓库中缺少该文件，
导致 video_real12.py 在 import 阶段直接 ModuleNotFoundError。这里补齐一个实现：
对 3D 点云做 DBSCAN 聚类去噪，返回去掉噪声点后的点集（形状 (N, 3)）。
"""
import numpy as np
from sklearn.cluster import DBSCAN

# 聚类参数：毫米波点云在 0.5m 邻域内、至少若干个点才认为是真实目标
EPS = 0.5
MIN_SAMPLES = 8


def cluster_filter(points, eps=EPS, min_samples=MIN_SAMPLES):
    """对点云做 DBSCAN 聚类并去除噪声点。

    参数
    ----
    points : array-like, (N, 3) 或 (N, >3)
        点云坐标，至少包含 x, y, z 三列（额外列会被保留）。
    eps : float
        邻域半径（米）。
    min_samples : int
        成为核心点的最少邻居数。点很少时会自动下调，避免整帧被当成噪声丢掉。

    返回
    ----
    np.ndarray  (M, 3)  —— 去噪后的 x, y, z；无有效点时返回 shape 为 (0, 3) 的数组。
    """
    pts = np.asarray(points, dtype=float)
    if pts.size == 0:
        return np.zeros((0, 3), dtype=float)

    xyz = pts[:, :3]
    n = xyz.shape[0]

    # 点太少时 DBSCAN 会把一切判为噪声，自适应降低 min_samples
    ms = int(max(3, min(min_samples, max(3, n // 8))))
    if n < ms:
        return xyz.copy()

    labels = DBSCAN(eps=eps, min_samples=ms).fit(xyz).labels_
    mask = labels != -1
    if not np.any(mask):
        # 全是噪声：一帧里可能只有零星反光点，直接返回原始点，避免画面全空
        return xyz.copy()
    return xyz[mask]


def cluster_centers(points, eps=EPS, min_samples=MIN_SAMPLES):
    """返回每个聚类的中心坐标（用于人数统计等）。"""
    pts = np.asarray(points, dtype=float)
    if pts.size == 0:
        return np.zeros((0, 3), dtype=float)
    xyz = pts[:, :3]
    n = xyz.shape[0]
    ms = int(max(3, min(min_samples, max(3, n // 8))))
    if n < ms:
        return np.zeros((0, 3), dtype=float)
    labels = DBSCAN(eps=eps, min_samples=ms).fit(xyz).labels_
    centers = [xyz[labels == lb].mean(axis=0) for lb in set(labels) if lb != -1]
    if not centers:
        return np.zeros((0, 3), dtype=float)
    return np.array(centers)
# -*- coding: utf-8 -*-
"""從手繪影像萃取輪廓，變成可以落地的圖形點列。

手打點列等於中間隔了一層人的判讀；直接描圖才忠於原稿。
流程針對「白底黑線的手繪外框」設計：

  二值化 → 膨脹把手繪的小缺口補起來 → 從邊框灌水找出外部背景
  → 內部＝不是筆畫也不是外部 → 侵蝕回原本粗細 → 取最大連通塊
  → find_contours 取最長的一條 → Douglas-Peucker 簡化

為什麼要先膨脹再侵蝕：手繪線條常常沒有真正閉合，差幾個像素。不補起來，
灌水會從缺口漏進去，整張圖變成一片背景，什麼都抓不到。
"""
import numpy as np
from PIL import Image
from scipy import ndimage
from skimage import measure


def _binary(path, thresh=None):
    im = Image.open(path).convert("L")
    a = np.asarray(im, dtype=np.float64)
    if thresh is None:
        # 手繪稿是白底黑線，兩群分得很開；取中位數與最小值之間偏暗的位置
        thresh = (np.percentile(a, 50) + a.min()) / 2.0
        thresh = max(60.0, min(200.0, thresh))
    return a < thresh, a.shape


def _silhouette(ink, close_px):
    st = np.ones((3, 3), bool)
    thick = ndimage.binary_dilation(ink, st, iterations=close_px)
    # 從影像邊框灌水：碰得到邊框的空白＝外部
    free = ~thick
    seed = np.zeros_like(free)
    seed[0, :] = seed[-1, :] = seed[:, 0] = seed[:, -1] = True
    seed &= free
    outside = ndimage.binary_propagation(seed, mask=free, structure=st)
    solid = ~outside
    if close_px:
        solid = ndimage.binary_erosion(solid, st, iterations=close_px,
                                       border_value=1)
    solid = ndimage.binary_fill_holes(solid)
    lab, n = ndimage.label(solid)
    if n == 0:
        raise RuntimeError("找不到封閉輪廓：線條可能斷太多，試著調高 close_px")
    sizes = ndimage.sum(solid, lab, range(1, n + 1))
    return lab == (int(np.argmax(sizes)) + 1)


def _rdp(pts, eps):
    """Douglas-Peucker：保留轉折、砍掉直線上的贅點。"""
    if len(pts) < 3:
        return list(pts)
    a, b = np.asarray(pts[0]), np.asarray(pts[-1])
    ab = b - a
    L = np.hypot(*ab)
    P = np.asarray(pts)
    if L < 1e-9:
        d = np.hypot(*(P - a).T)
    else:
        d = np.abs(np.cross(np.broadcast_to(ab, P.shape), P - a)) / L
    i = int(np.argmax(d))
    if d[i] <= eps:
        return [pts[0], pts[-1]]
    return _rdp(pts[:i + 1], eps)[:-1] + _rdp(pts[i:], eps)


def _smooth(pts, k=3):
    """輕度移動平均：把描圖的鋸齒磨掉，但不動大轉折。"""
    P = np.asarray(pts, dtype=float)
    n = len(P)
    out = np.empty_like(P)
    for i in range(n):
        idx = [(i + j) % n for j in range(-k, k + 1)]
        out[i] = P[idx].mean(axis=0)
    return out


def trace(path, close_px=6, target_pts=64, smooth_k=3):
    """回傳 (正規化點列, 診斷資訊)。點列 x 向右、y 向上、長邊為 1、置中、封閉。"""
    ink, shape = _binary(path)
    solid = _silhouette(ink, close_px)
    cs = measure.find_contours(solid.astype(float), 0.5)
    if not cs:
        raise RuntimeError("find_contours 找不到輪廓")
    c = max(cs, key=len)                       # (row, col)
    c = _smooth(c, smooth_k)
    # 二分找 eps，把點數壓到目標附近——點太多會被巷弄雜訊帶偏，太少畫不出耳朵
    lo, hi = 0.05, max(shape) / 6.0
    pts = c.tolist()
    for _ in range(28):
        eps = (lo + hi) / 2.0
        r = _rdp(c.tolist(), eps)
        if len(r) > target_pts:
            lo = eps
        else:
            hi = eps
        pts = r
        if abs(len(r) - target_pts) <= 2:
            break
    P = np.asarray(pts, dtype=float)
    xy = np.column_stack([P[:, 1], -P[:, 0]])   # 影像列往下 → 圖形 y 往上
    mn, mx = xy.min(axis=0), xy.max(axis=0)
    k = float((mx - mn).max()) or 1.0
    xy = (xy - (mn + mx) / 2.0) / k
    out = [tuple(map(float, p)) for p in xy]
    if out[0] != out[-1]:
        out.append(out[0])
    info = {"影像尺寸": "%dx%d" % (shape[1], shape[0]),
            "筆畫像素": int(ink.sum()),
            "輪廓原始點數": len(c), "簡化後點數": len(out),
            "缺口補償(px)": close_px,
            "寬高比": round(float((mx[0] - mn[0]) / max(mx[1] - mn[1], 1e-9)), 3)}
    return out, info


def preview(pts, path, size=520, pad=30):
    """把萃取結果畫出來，肉眼確認描對了沒有。"""
    from PIL import ImageDraw
    im = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(im)
    s = size - pad * 2
    d.line([(size / 2 + x * s, size / 2 - y * s) for x, y in pts],
           fill=(214, 0, 28), width=4, joint="curve")
    for x, y in pts:
        cx, cy = size / 2 + x * s, size / 2 - y * s
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(20, 30, 50))
    im.save(path)
    return path

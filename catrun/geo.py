# -*- coding: utf-8 -*-
"""經緯度的基本運算。台中都在 24°N 附近，等距近似的誤差在市區尺度可忽略。"""
import math

R = 6371008.8


def hav(a, b):
    """兩點球面距離（公尺）。a、b 為 (lat, lon)。"""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def enu(origin, p):
    """以 origin 為原點換成公尺平面座標 (東, 北)。"""
    lat0 = math.radians(origin[0])
    return (math.radians(p[1] - origin[1]) * R * math.cos(lat0),
            math.radians(p[0] - origin[0]) * R)


def enu_inv(origin, xy):
    lat0 = math.radians(origin[0])
    return (origin[0] + math.degrees(xy[1] / R),
            origin[1] + math.degrees(xy[0] / (R * math.cos(lat0))))


def path_len(pts):
    return sum(hav(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def resample(pts, n):
    """把折線重新取樣成 n 個等間距點——比對形狀時兩邊點數要一致才有意義。"""
    if len(pts) < 2:
        return list(pts) * n
    segs = [hav(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(segs)
    if total <= 0:
        return [pts[0]] * n
    out, target, acc, i = [], 0.0, 0.0, 0
    step = total / (n - 1)
    for k in range(n):
        target = step * k
        while i < len(segs) and acc + segs[i] < target:
            acc += segs[i]
            i += 1
        if i >= len(segs):
            out.append(pts[-1])
            continue
        t = 0.0 if segs[i] == 0 else (target - acc) / segs[i]
        a, b = pts[i], pts[i + 1]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def bearing(a, b):
    y = math.sin(math.radians(b[1] - a[1])) * math.cos(math.radians(b[0]))
    x = (math.cos(math.radians(a[0])) * math.sin(math.radians(b[0]))
         - math.sin(math.radians(a[0])) * math.cos(math.radians(b[0]))
         * math.cos(math.radians(b[1] - a[1])))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def turn(prev_b, next_b):
    """轉彎方向與角度：Cue Sheet 要靠這個寫「左轉／右轉／迴轉」。"""
    d = (next_b - prev_b + 540) % 360 - 180
    if abs(d) < 25:
        return "直行", d
    if abs(d) > 150:
        return "迴轉", d
    return ("右轉" if d > 0 else "左轉"), d

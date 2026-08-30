# -*- coding: utf-8 -*-
"""累計爬升。規格書 2-3 要求 ≤ 50 m / 10 km。

用 Open-Meteo 的免費高程 API（每次最多 100 點），落地快取。取不到就回 None
——寧可在報告上寫「未取得」，也不要塞一個假數字進去讓人以為驗過了。
"""
import hashlib
import json
import os

import requests

from .geo import resample

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "cache")
URL = "https://api.open-meteo.com/v1/elevation"


def profile(pts, n=180):
    """回傳 (取樣點, 高程列表) 或 (取樣點, None)。"""
    sample = resample(pts, n)
    key = hashlib.sha1(json.dumps([[round(a, 5), round(b, 5)] for a, b in sample]
                                  ).encode()).hexdigest()
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "elev_" + key + ".json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return sample, json.load(f)
    out = []
    try:
        for i in range(0, len(sample), 100):
            chunk = sample[i:i + 100]
            r = requests.get(URL, timeout=45, params={
                "latitude": ",".join("%.5f" % a for a, _ in chunk),
                "longitude": ",".join("%.5f" % b for _, b in chunk)})
            r.raise_for_status()
            out.extend(r.json()["elevation"])
    except Exception:      # noqa - 沒有高程資料不該讓整個規劃失敗
        return sample, None
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return sample, out


def climb(elev, smooth=5.0):
    """累計爬升。

    門檻 5 公尺是刻意的：高程來源是 90 公尺網格的 DEM，每點有 ±2~3 公尺抖動，
    沿線取 180 點，門檻設太低會把雜訊全部累加成假爬升（同一條平路可以灌到
    上百公尺）。5 公尺是路跑界計算總爬升的慣用門檻。
    """
    if not elev:
        return None
    gain, ref = 0.0, elev[0]
    for v in elev[1:]:
        if v - ref >= smooth:
            gain += v - ref
            ref = v
        elif v < ref:
            ref = v
    return round(gain, 1)

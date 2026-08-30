# -*- coding: utf-8 -*-
"""規格書第二節的五大構面評分。

每個構面回傳 0~1，再乘上規格書的權重（30/25/20/15/10）。
子項一律把「原始數字」也留在報告裡——只給一個總分，看的人沒辦法判斷哪裡該改。
"""
from .config import (CLIMB_LIMIT_PER_10KM, DISTRICTS, GREENWAY_HINTS, LEVELS,
                     MAP_SCALE_DENOM, SIGNAL_LIMIT_PER_KM, SUPPLY_GAP_KM,
                     TRANSIT_WALK_M, WEIGHTS)
from .fitter import fidelity
from .geo import hav


def _band(v, good, bad):
    """good 以內給 1，bad 以外給 0，中間線性。"""
    if good == bad:
        return 1.0 if v <= good else 0.0
    if good < bad:
        return max(0.0, min(1.0, (bad - v) / (bad - good)))
    return max(0.0, min(1.0, (v - bad) / (good - bad)))


def greenway_ratio(route):
    tot = sum(e["length"] for e in route.edges) or 1.0
    g = 0.0
    for e in route.edges:
        nm = e.get("name") or ""
        if (e["hw"] in ("footway", "path", "pedestrian", "cycleway", "living_street")
                or any(k in nm for k in GREENWAY_HINTS)):
            g += e["length"]
    return g / tot


def signals_on_route(net, route, radius=35.0):
    """沿線紅綠燈數。用 KD-tree 找路線節點附近的號誌，同一個只算一次。"""
    if net.sig_tree is None:
        return 0
    hit = set()
    for p in route.pts:
        for i in net.sig_tree.query_ball_point(
                (p[1] * net._kx, p[0] * net._ky), radius):
            if hav(p, net.signals[i]) <= radius:
                hit.add(i)
    return len(hit)


def _nearby(route, items, radius):
    """回傳 (距離路線 radius 內的項目, 每項最近里程位置)。"""
    out = []
    for it in items:
        pt = it[0]
        best, bestd = None, 1e18
        acc = 0.0
        for i in range(len(route.pts) - 1):
            d = hav(pt, route.pts[i])
            if d < bestd:
                bestd, best = d, acc
            acc += hav(route.pts[i], route.pts[i + 1])
        if bestd <= radius:
            out.append((bestd, best, it))
    out.sort(key=lambda x: x[1])
    return out


def score(net, route, level_key, district_key, elev_gain=None):
    W = WEIGHTS
    lv = LEVELS[level_key]
    dis = DISTRICTS[district_key]
    det = {}

    # ── 1. 圖形還原度與辨識性（30%）──
    fid, mean_err = fidelity(route, route.target, route.place["size_m"])
    closure_m = hav(route.pts[0], route.pts[-1])
    retrace = route.retrace_ratio()
    # 視覺可讀性：縮到 1:25000 時輪廓長邊有幾公釐（40 mm 以上一眼認得出）
    mm = route.place["size_m"] / MAP_SCALE_DENOM * 1000.0
    read = _band(mm, 40.0, 12.0)
    shape = (0.50 * fid + 0.20 * _band(closure_m, 30.0, 400.0)
             + 0.15 * _band(retrace, 0.05, 0.40) + 0.15 * read)
    det["shape"] = {
        "貼合度": round(fid, 3), "平均偏離(m)": round(mean_err, 1),
        "起終點相距(m)": round(closure_m, 1), "折返路段佔比": round(retrace, 3),
        "1:25000 圖上長邊(mm)": round(mm, 1),
    }

    # ── 2. 道路安全性與通行品質（25%）──
    sf = route.safety_mean()
    gw = greenway_ratio(route)
    sig = signals_on_route(net, route)
    sig_per_km = sig / max(route.km, 0.1)
    dead = sum(1 for n in route.seq if net.G.degree(n) == 1)
    dark = sum(e["length"] for e in route.edges if e.get("lit") == "no")
    safety = (0.45 * sf + 0.25 * _band(sig_per_km, SIGNAL_LIMIT_PER_KM, 4.0)
              + 0.20 * min(1.0, gw / 0.35) + 0.10 * _band(dead, 0, 6))
    det["safety"] = {
        "路段安全加權均值": round(sf, 3), "綠園道/人行道佔比": round(gw, 3),
        "沿線紅綠燈(處)": sig, "每公里停等(次)": round(sig_per_km, 2),
        "上限(次/km)": SIGNAL_LIMIT_PER_KM,
        "行經無出口節點": dead, "無照明路段(m)": round(dark),
    }

    # ── 3. 里程與難度分級（20%）──
    km = route.km
    if lv["min_km"] <= km <= lv["max_km"]:
        dist_s = 1.0
    else:
        off = (lv["min_km"] - km) if km < lv["min_km"] else (km - lv["max_km"])
        dist_s = _band(off, 0.0, 3.0)
    if elev_gain is None:
        climb_s, climb_txt = 0.75, "未取得（高程 API 無回應）"
        limit = None
    else:
        limit = CLIMB_LIMIT_PER_10KM * km / 10.0
        climb_s = _band(elev_gain, limit, limit * 3 + 30)
        climb_txt = "%.1f m" % elev_gain
    distance = 0.70 * dist_s + 0.30 * climb_s
    det["distance"] = {
        "里程(km)": round(km, 2), "級距": lv["label"],
        "級距範圍": "%.0f–%.0f km" % (lv["min_km"], lv["max_km"]),
        "累計爬升": climb_txt,
        "爬升上限(m)": (round(limit, 1) if limit else "—"),
    }

    # ── 4. 區域特性與地標串聯（15%）──
    lats = [p[0] for p in route.pts]
    lons = [p[1] for p in route.pts]
    h = hav((min(lats), lons[0]), (max(lats), lons[0]))
    w = hav((lats[0], min(lons)), (lats[0], max(lons)))
    aspect = (w / h) if h else 1.0
    lo, hi = dis["aspect"]
    if lo <= aspect <= hi:
        asp_s = 1.0
    else:
        off = (lo - aspect) if aspect < lo else (aspect - hi)
        asp_s = _band(off, 0.0, 0.9)
    marks = _nearby(route, net.landmarks, 180.0)
    # 規格書 4：每條路線設定 2~3 個核心地標
    mk_s = _band(abs(min(len(marks), 3) - 3), 0, 3)
    district_s = 0.40 * asp_s + 0.35 * min(1.0, gw / 0.30) + 0.25 * mk_s
    det["district"] = {
        "行政區": dis["name"], "適配風格": dis["style"],
        "路線寬高比": round(aspect, 2),
        "建議寬高比": "%.2f–%.2f" % (lo, hi),
        "沿線地標(180m內)": len(marks),
    }

    # ── 5. 補給與交通可達性（10%）──
    start = route.pts[0]
    tr = sorted(((hav(start, t[0]), t) for t in net.transit), key=lambda x: x[0])
    t_d = tr[0][0] if tr else 9999.0
    t_name = ("%s（%s）" % (tr[0][1][1], tr[0][1][2])) if tr else "—"
    sup = _nearby(route, net.supply, 220.0)
    if sup:
        pos = [0.0] + [s[1] for s in sup] + [route.length_m]
        gap = max(pos[i + 1] - pos[i] for i in range(len(pos) - 1)) / 1000.0
    else:
        gap = route.km
    logi = 0.55 * _band(t_d, TRANSIT_WALK_M, 1500.0) + 0.45 * _band(gap, SUPPLY_GAP_KM, 6.0)
    det["logistics"] = {
        "起點最近大眾運輸": t_name, "距離(m)": round(t_d),
        "步行 5 分鐘門檻(m)": TRANSIT_WALK_M,
        "沿線補給點": len(sup), "最大補給空窗(km)": round(gap, 2),
        "空窗上限(km)": SUPPLY_GAP_KM,
    }

    parts = {"shape": shape, "safety": safety, "distance": distance,
             "district": district_s, "logistics": logi}
    total = sum(W[k] * parts[k] for k in W) * 100.0
    return {
        "total": round(total, 1),
        "parts": {k: round(v * 100, 1) for k, v in parts.items()},
        "weights": {k: int(v * 100) for k, v in W.items()},
        "detail": det,
        "landmarks": [(round(d), round(m / 1000.0, 2), it[1], it[2], it[0])
                      for d, m, it in marks[:6]],
        "supply": [(round(d), round(m / 1000.0, 2), it[1]) for d, m, it in sup],
    }


LABEL = {"shape": "圖形還原度與辨識性", "safety": "道路安全性與通行品質",
         "distance": "里程與難度分級", "district": "區域特性與地標串聯",
         "logistics": "補給與交通可達性"}

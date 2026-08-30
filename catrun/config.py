# -*- coding: utf-8 -*-
"""規格書裡的常數集中在這裡：改規格只改這個檔，演算法不用動。

依據：《台中市圖形路跑路線規劃規格書》
"""

# ── 二、評選維度權重（規格書第二節）──
WEIGHTS = {
    "shape": 0.30,      # 圖形還原度與辨識性
    "safety": 0.25,     # 道路安全性與通行品質
    "distance": 0.20,   # 里程與難度分級
    "district": 0.15,   # 區域特性與地標串聯
    "logistics": 0.10,  # 補給與交通可達性
}

# ── 里程分級（規格書 2-3）──
LEVELS = {
    "standard":  {"label": "標準大眾級（休閒跑）", "min_km": 5.0,  "max_km": 8.0},
    "challenge": {"label": "挑戰級（進階跑）",     "min_km": 10.0, "max_km": 15.0},
    "flagship":  {"label": "全區長程級（旗艦線）", "min_km": 20.0, "max_km": 22.0},
}
CLIMB_LIMIT_PER_10KM = 50.0      # 累計爬升 ≤ 50 m / 10 km
SIGNAL_LIMIT_PER_KM = 1.5        # 紅綠燈停等 ≤ 1.5 次 / km
SUPPLY_GAP_KM = 3.0              # 每 2~3 km 至少一處補給／公廁
MAP_SCALE_DENOM = 25000          # 縮到 1:25000 仍需可辨識

# ── 2-2 車流與路幅分級：跑起來舒不舒服，決定於這張表 ──
# 分數 0~1。綠園道、河濱步道最高；無人行道的快速幹道最低。
# 規格書明列「避免環中路、文心路快車道旁」→ primary/trunk 一律壓低。
ROAD_SAFETY = {
    "footway": 1.00, "path": 1.00, "pedestrian": 1.00, "steps": 0.20,
    "cycleway": 0.95, "living_street": 0.90, "track": 0.75,
    "residential": 0.80, "service": 0.72, "unclassified": 0.70,
    "tertiary": 0.55, "tertiary_link": 0.55,
    "secondary": 0.35, "secondary_link": 0.35,
    "primary": 0.20, "primary_link": 0.20,
}
# 這些不收：跑者不該上去，收進來只會讓演算法找到「最短但最危險」的線
EXCLUDE_HIGHWAY = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "construction", "proposed", "raceway", "bus_guideway", "escape",
}
# 綠園道／河濱：規格書 2-2 明列「優先採用」，區域特性也給分
GREENWAY_HINTS = ("園道", "綠道", "步道", "自行車道", "河濱", "堤防",
                  "草悟道", "美術園道", "柳川", "綠川", "筏子溪", "旱溪")

# ── 2-4 各區路網特性與適配圖形風格 ──
# 前六區沿用規格書原文，其餘 23 區依實地特徵補寫。定義本身放在
# catrun/data/districts.json（由 tools_gen_districts.py 從 OSM 真實邊界產生），
# 不寫死在程式裡——邊界資料會更新，而且 29 個區塞在這裡沒人讀得下去。
#
# 兩個 bbox 是刻意分開的：
#   bbox        行政區真實範圍，圖形的「中心」只在這裡面搜尋，路線才會落在該區
#   fetch_bbox  路網下載範圍，往外擴。中區只有 1.3×1.4 公里，比 5 公里路線需要的
#               圖形還小，不擴的話路線必然溢出去卻沒有路網可走
def _load_districts():
    import json
    import os
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "districts.json")
    with open(p, encoding="utf-8") as f:
        rows = json.load(f)
    out = {}
    for d in rows:
        out[d["key"]] = {
            "name": d["name"], "osm": d["osm"],
            "bbox": tuple(d["bbox"]), "fetch_bbox": tuple(d["fetch_bbox"]),
            "style": d["style"], "traits": d["traits"],
            "aspect": tuple(d["aspect"]),
            "recommended": d.get("recommended", True),
            "group": d.get("group", "其他"),
            "span_km": tuple(d.get("span_km", (0, 0))),
        }
    return out


DISTRICTS = _load_districts()

# ── 五、補給與交通（規格書 2-5）──
TRANSIT_WALK_M = 400.0     # 起終點距大眾運輸節點步行 5 分鐘 ≈ 400 m

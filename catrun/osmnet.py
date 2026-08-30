# -*- coding: utf-8 -*-
"""OSM 路網擷取與建圖。

規格書 3-1 要求「吸附至實際路網的標準導航軌跡」，所以一定要用真實路網，
不能自己畫線。資料來源 OpenStreetMap（Overpass API），抓下來就落地快取——
同一區重跑第二次不再打 API，沒網路的時候也還能規劃。
"""
import hashlib
import json
import math
import os
import time

import networkx as nx
import requests
from scipy.spatial import cKDTree

from .config import EXCLUDE_HIGHWAY, GREENWAY_HINTS, ROAD_SAFETY
from .geo import hav

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "cache")
ENDPOINTS = ("https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter")
# 一定要帶 User-Agent：預設的 python-requests 會被 Overpass 前面的 Apache
# 直接擋掉回 406，錯誤訊息完全看不出原因（查了很久才發現）。
HEADERS = {"User-Agent": "catrun/1.0 (Taichung shape-run route planner)",
           "Accept": "application/json"}


def _cache_path(q):
    return os.path.join(CACHE, hashlib.sha1(q.encode("utf-8")).hexdigest() + ".json")


def overpass(q, refresh=False):
    """打 Overpass 並落地快取。兩個端點輪替，單邊掛掉不至於整個系統不能用。"""
    os.makedirs(CACHE, exist_ok=True)
    p = _cache_path(q)
    if os.path.exists(p) and not refresh:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    last = None
    for ep in ENDPOINTS:
        for attempt in range(3):
            try:
                r = requests.post(ep, data={"data": q}, timeout=300,
                                  headers=HEADERS)
                if r.status_code == 200:
                    d = r.json()
                    with open(p, "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False)
                    return d
                last = "HTTP %s" % r.status_code
            except Exception as e:      # noqa - 換一個端點再試，不要直接炸掉
                last = repr(e)
            time.sleep(2 + attempt * 3)
    raise RuntimeError("Overpass 取不到資料：%s（可在有網路時先預抓快取）" % last)


def _q_roads(bbox):
    s, w, n, e = bbox
    bad = "|".join(sorted(EXCLUDE_HIGHWAY))
    box = "%s,%s,%s,%s" % (s, w, n, e)
    return ("[out:json][timeout:280];"
            'way["highway"]["highway"!~"^(' + bad + ')$"]'
            '["access"!~"^(private|no)$"](' + box + ");"
            "out body; >; out skel qt;")


def _q_poi(bbox):
    s, w, n, e = bbox
    box = "(%s,%s,%s,%s);" % (s, w, n, e)
    sel = [
        'node["highway"="traffic_signals"]',
        'node["highway"="crossing"]',
        'node["shop"="convenience"]',
        'node["amenity"="toilets"]',
        'node["amenity"="drinking_water"]',
        'node["railway"="station"]',
        'node["railway"="tram_stop"]',
        'node["public_transport"="station"]',
        'node["highway"="bus_stop"]',
        'way["leisure"="park"]',
        'node["tourism"~"attraction|museum|artwork"]',
        'node["historic"]',
        'way["tourism"~"attraction|museum"]',
        'way["historic"]',
        'node["amenity"~"theatre|place_of_worship|university|library"]',
        'way["amenity"~"theatre|university"]',
    ]
    return "[out:json][timeout:280];(" + "".join(x + box for x in sel) + ");out center tags;"


def _safety(tags):
    """這條路跑起來安不安全，0~1。規格書 2-2 的「車流與路幅分級」就是靠這個落地。"""
    hw = tags.get("highway", "")
    base = ROAD_SAFETY.get(hw, 0.5)
    name = tags.get("name", "") or ""
    # 綠園道／河濱步道：規格書列為優先採用，實際跑起來也確實最舒服
    if any(k in name for k in GREENWAY_HINTS):
        base = max(base, 0.95)
    if tags.get("foot") in ("designated", "yes"):
        base = min(1.0, base + 0.05)
    if tags.get("sidewalk") in ("both", "left", "right", "yes"):
        base = min(1.0, base + 0.08)
    if tags.get("sidewalk") == "no":
        base = max(0.05, base - 0.12)
    # 車道數多＝路幅寬、車速快，跑者體感差
    try:
        lanes = int(str(tags.get("lanes", "0")).split(";")[0])
        if lanes >= 4:
            base = max(0.05, base - 0.10)
    except ValueError:
        pass
    return round(min(1.0, max(0.02, base)), 3)


class RoadNet:
    """一個行政區的可跑路網（含沿線號誌與補給點）。"""

    def __init__(self, key, cfg, refresh=False):
        self.key, self.cfg = key, cfg
        self.G = nx.Graph()
        self.coord = {}
        # 抓取範圍用 fetch_bbox（往外擴過），搜尋中心才用 bbox（行政區真實範圍）。
        # 小區不擴的話，路線一定溢出行政區卻沒有路網可走。
        fb = cfg.get("fetch_bbox") or cfg["bbox"]
        self._load_roads(fb, refresh)
        self._load_poi(fb, refresh)
        self._index()

    def _load_roads(self, bbox, refresh):
        d = overpass(_q_roads(bbox), refresh)
        nodes, ways = {}, []
        for el in d.get("elements", []):
            if el["type"] == "node":
                nodes[el["id"]] = (el["lat"], el["lon"])
            elif el["type"] == "way" and el.get("nodes"):
                ways.append(el)
        self.coord = nodes
        for w in ways:
            t = w.get("tags", {}) or {}
            hw = t.get("highway", "")
            if hw in EXCLUDE_HIGHWAY:
                continue
            sf = _safety(t)
            name = t.get("name") or t.get("name:zh") or ""
            lit = t.get("lit", "")
            nd = [n for n in w["nodes"] if n in nodes]
            for a, b in zip(nd, nd[1:]):
                if a == b:
                    continue
                length = hav(nodes[a], nodes[b])
                if length <= 0:
                    continue
                # 加權長度：危險的路要「感覺比較長」，最短路徑才會自己繞開它。
                # 係數 1.6 是刻意調的——太小等於不管安全，太大會為了一小段綠地繞遠路。
                cost = length * (1.0 + 1.6 * (1.0 - sf))
                if not self.G.has_edge(a, b) or self.G[a][b]["cost"] > cost:
                    self.G.add_edge(a, b, length=length, cost=cost, name=name,
                                    hw=hw, safety=sf, lit=lit, wid=w["id"])
        # 只留最大連通塊：孤島路段會讓路徑規劃無解
        if self.G.number_of_nodes():
            big = max(nx.connected_components(self.G), key=len)
            self.G = self.G.subgraph(big).copy()

    def _load_poi(self, bbox, refresh):
        d = overpass(_q_poi(bbox), refresh)
        self.signals, self.crossings, self.supply = [], [], []
        self.transit, self.landmarks = [], []
        for el in d.get("elements", []):
            t = el.get("tags", {}) or {}
            if el["type"] == "node":
                pt = (el.get("lat"), el.get("lon"))
            else:
                c = el.get("center") or {}
                pt = (c.get("lat"), c.get("lon"))
            if pt[0] is None:
                continue
            name = t.get("name") or t.get("name:zh") or ""
            if t.get("highway") == "traffic_signals":
                self.signals.append(pt)
            elif t.get("highway") == "crossing":
                self.crossings.append((pt, t.get("crossing", ""), name))
            if (t.get("shop") == "convenience"
                    or t.get("amenity") in ("toilets", "drinking_water")):
                label = name or {"toilets": "公廁",
                                 "drinking_water": "飲水機"}.get(t.get("amenity"), "便利商店")
                self.supply.append((pt, label))
            if (t.get("railway") in ("station", "tram_stop")
                    or t.get("public_transport") == "station"
                    or t.get("highway") == "bus_stop"):
                kind = ("台鐵車站" if t.get("railway") == "station"
                        else "捷運站" if t.get("railway") == "tram_stop" else "公車站")
                self.transit.append((pt, name or kind, kind))
            if name and (t.get("tourism") or t.get("historic")
                         or t.get("amenity") in ("theatre", "university", "library")
                         or t.get("leisure") == "park"):
                kind = (t.get("tourism") or t.get("historic")
                        or t.get("amenity") or t.get("leisure"))
                self.landmarks.append((pt, name, kind))

    def _index(self):
        """建空間索引。座標先換成公尺再進 KD-tree。

        原本直接餵 (經度, 緯度) 的度數，等於把經度差與緯度差當成等價——但在台中
        1 度經度只有 1 度緯度的 0.913 倍，「最近的節點」會被算偏。誤差不大，卻會
        讓吸附結果與正確版本不一致（跟 JS 引擎對照時才抓到這件事）。
        """
        self.ids = list(self.G.nodes())
        pts = [self.coord[n] for n in self.ids]
        if not pts:
            self.tree = self.sig_tree = None
            self._kx = self._ky = 1.0
            return
        lat0 = sum(p[0] for p in pts) / len(pts)
        self._ky = 111319.49
        self._kx = math.cos(math.radians(lat0)) * self._ky
        self.tree = cKDTree([(p[1] * self._kx, p[0] * self._ky) for p in pts])
        self.sig_tree = (cKDTree([(p[1] * self._kx, p[0] * self._ky)
                                  for p in self.signals]) if self.signals else None)

    def nearest(self, pt, k=1):
        if self.tree is None:
            return None
        _, i = self.tree.query((pt[1] * self._kx, pt[0] * self._ky), k=k)
        if k == 1:
            return self.ids[int(i)]
        return [self.ids[int(x)] for x in i]

    def xy(self, nid):
        return self.coord[nid]

    def stats(self):
        return {"nodes": self.G.number_of_nodes(), "edges": self.G.number_of_edges(),
                "signals": len(self.signals), "supply": len(self.supply),
                "transit": len(self.transit), "landmarks": len(self.landmarks)}

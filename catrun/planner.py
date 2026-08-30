# -*- coding: utf-8 -*-
"""把各模組串成一次完整規劃：抓路網 → 落地圖形 → 調里程 → 評分 → 產出。"""
import os
import time

from . import elevation, export, fitter, shapes
from .config import DISTRICTS, LEVELS
from .osmnet import RoadNet

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
_NET_CACHE = {}


def get_net(district, refresh=False, log=None):
    """同一個行政區的路網在行程內只建一次——建圖比規劃本身還花時間。"""
    if district in _NET_CACHE and not refresh:
        return _NET_CACHE[district]
    if log:
        log("讀取 %s 路網…" % DISTRICTS[district]["name"])
    t0 = time.time()
    net = RoadNet(district, DISTRICTS[district], refresh=refresh)
    if log:
        st = net.stats()
        log("路網完成：節點 %d、路段 %d、號誌 %d、補給點 %d、地標 %d（%.1fs）"
            % (st["nodes"], st["edges"], st["signals"], st["supply"],
               st["landmarks"], time.time() - t0))
    _NET_CACHE[district] = net
    return net


def plan(district, shape_key, level="standard", refresh=False, log=None,
         center=None, save=True, outdir=None):
    log = log or (lambda *_: None)
    if district not in DISTRICTS:
        raise KeyError("沒有這個行政區：%s" % district)
    if level not in LEVELS:
        raise KeyError("沒有這個級距：%s" % level)
    net = get_net(district, refresh, log)
    sh = shapes.get(shape_key)
    base = shapes.normalised(shape_key)
    lv = LEVELS[level]
    target_km = (lv["min_km"] + lv["max_km"]) / 2.0

    log("落地圖形「%s」，目標 %.1f km …" % (sh["name"], target_km))
    t0 = time.time()
    centers = [center] if center else None
    route = fitter.search(net, base, target_km, centers=centers, log=log)
    if route is None:
        raise RuntimeError("這個區域找不到可用的路網配置，換個行政區或圖形再試")
    route = fitter.fit_band(net, base, route, lv["min_km"], lv["max_km"], log=log)
    log("路線完成 %.2f km（%.1fs）" % (route.km, time.time() - t0))

    log("查詢高程…")
    _, elev = elevation.profile(route.pts)
    gain = elevation.climb(elev)

    log("評分…")
    from . import scoring
    sc = scoring.score(net, route, level, district, gain)
    cues = export.cue_sheet(route)
    notes = export.safety_notes(net, route)
    marks = sc["landmarks"]

    meta = {
        "title": "%s %s 圖形路跑路線" % (DISTRICTS[district]["name"], sh["name"]),
        "shape": shape_key, "shape_name": sh["name"],
        "district": district, "district_name": DISTRICTS[district]["name"],
        "level": level, "level_label": lv["label"],
        "km": round(route.km, 2), "start": route.pts[0],
        "climb_m": gain, "score": sc["total"],
    }
    sub = ("%s ・ %.2f km ・ 綜合評分 %.1f 分 ・ 起終點 %.5f, %.5f"
           % (lv["label"], route.km, sc["total"], route.pts[0][0], route.pts[0][1]))
    img = export.sketch(net, route, meta["title"], sub)

    paths = {}
    if save:
        slug = "%s_%s_%s" % (district, shape_key, level)
        paths = export.save_all(outdir or OUT, slug, route, net, meta, sc,
                                cues, notes, marks, img)
        log("已輸出：%s" % "、".join(os.path.basename(v) for v in paths.values()))
    return {"route": route, "net": net, "meta": meta, "score": sc, "cues": cues,
            "notes": notes, "marks": marks, "img": img, "paths": paths}

def plan_best(districts, shape_key, level="standard", refresh=False, log=None,
              save=True):
    """同一個圖形在多個行政區各排一次，依綜合評分排名。

    這才是「哪裡最適合畫這隻貓」的答案——一個圖形好不好，很大一部分取決於
    當地路網的密度與走向，不是圖形本身。失敗的區不中斷整批。
    """
    log = log or (lambda *_: None)
    rows = []
    for d in districts:
        log("── %s ──" % DISTRICTS[d]["name"])
        try:
            r = plan(d, shape_key, level, refresh=refresh, log=log, save=save)
            rows.append(r)
            log("%s：%.1f 分（%.2f km）" % (DISTRICTS[d]["name"],
                                          r["score"]["total"], r["meta"]["km"]))
        except Exception as e:      # noqa - 某一區沒解不該讓整批停掉
            log("%s 失敗：%r" % (DISTRICTS[d]["name"], e))
    rows.sort(key=lambda x: -x["score"]["total"])
    return rows

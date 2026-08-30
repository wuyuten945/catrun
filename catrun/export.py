# -*- coding: utf-8 -*-
"""規格書第三節「路線產出標準」：GPX / KML、Cue Sheet、安全提醒、地標打卡點。"""
import io
import os
import xml.sax.saxutils as sx

from PIL import Image, ImageDraw, ImageFont

from .geo import bearing, enu, hav, turn


def _esc(s):
    return sx.escape(str(s or ""))


# ────────────────────────── 軌跡檔 ──────────────────────────
def gpx(route, name, waypoints=()):
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<gpx version="1.1" creator="catrun" xmlns="http://www.topografix.com/GPX/1/1">',
         "<metadata><name>%s</name></metadata>" % _esc(name)]
    for lat, lon, t, d in waypoints:
        L.append('<wpt lat="%.6f" lon="%.6f"><name>%s</name><desc>%s</desc></wpt>'
                 % (lat, lon, _esc(t), _esc(d)))
    L.append("<trk><name>%s</name><trkseg>" % _esc(name))
    for lat, lon in route.pts:
        L.append('<trkpt lat="%.6f" lon="%.6f"/>' % (lat, lon))
    L += ["</trkseg></trk>", "</gpx>"]
    return "\n".join(L)


def kml(route, name, waypoints=()):
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
         "<name>%s</name>" % _esc(name),
         '<Style id="r"><LineStyle><color>ff2000d6</color><width>4</width></LineStyle></Style>']
    for lat, lon, t, d in waypoints:
        L.append("<Placemark><name>%s</name><description>%s</description>"
                 "<Point><coordinates>%.6f,%.6f,0</coordinates></Point></Placemark>"
                 % (_esc(t), _esc(d), lon, lat))
    L.append("<Placemark><name>%s</name><styleUrl>#r</styleUrl><LineString>"
             "<tessellate>1</tessellate><coordinates>" % _esc(name))
    L.append(" ".join("%.6f,%.6f,0" % (lon, lat) for lat, lon in route.pts))
    L += ["</coordinates></LineString></Placemark>", "</Document></kml>"]
    return "\n".join(L)


# ────────────────────────── Cue Sheet ──────────────────────────
def cue_sheet(route, min_seg=45.0):
    """路名循序導航表：合併同名路段，在路名改變處記轉彎方向與累計里程。

    min_seg：短於這個距離的碎段不獨立成一列，否則穿巷弄時會列出幾十列
    「行駛 12 公尺」，反而看不到重點。
    """
    rows, acc = [], 0.0
    cur_name = None
    seg_start = 0.0
    seg_pts = []
    for i, e in enumerate(route.edges):
        nm = e.get("name") or "無名巷弄"
        if cur_name is None:
            cur_name, seg_start, seg_pts = nm, acc, [route.pts[i]]
        elif nm != cur_name:
            length = acc - seg_start
            if length >= min_seg:
                rows.append({"km": round(seg_start / 1000.0, 2),
                             "road": cur_name, "len_m": round(length),
                             "pts": seg_pts + [route.pts[i]]})
                cur_name, seg_start, seg_pts = nm, acc, [route.pts[i]]
            else:
                cur_name = nm      # 太短就併進下一段
        else:
            seg_pts.append(route.pts[i])
        acc += e["length"]
    if cur_name is not None:
        rows.append({"km": round(seg_start / 1000.0, 2), "road": cur_name,
                     "len_m": round(acc - seg_start), "pts": seg_pts + [route.pts[-1]]})
    # 轉彎方向：用前一段末尾與這一段開頭的方位角差
    out = []
    for i, r in enumerate(rows):
        if i == 0:
            act = "出發"
        else:
            p = rows[i - 1]["pts"]
            a = bearing(p[max(0, len(p) - 3)], p[-1])
            b = bearing(r["pts"][0], r["pts"][min(2, len(r["pts"]) - 1)])
            act = turn(a, b)[0]
        out.append({"seq": i + 1, "km": r["km"], "action": act,
                    "road": r["road"], "len_m": r["len_m"],
                    "lat": r["pts"][0][0], "lon": r["pts"][0][1]})
    return out


# ────────────────────────── 安全提醒 ──────────────────────────
def safety_notes(net, route):
    """規格書 3-3：無號誌斑馬線、夜間照明較暗巷弄。"""
    notes = []
    acc, dark_from, dark_len = 0.0, None, 0.0
    for i, e in enumerate(route.edges):
        if e.get("lit") == "no":
            if dark_from is None:
                dark_from = acc
            dark_len += e["length"]
        else:
            if dark_from is not None and dark_len >= 120:
                notes.append({"type": "夜間照明", "km": round(dark_from / 1000.0, 2),
                              "text": "無路燈標記路段約 %d 公尺，夜跑請帶頭燈"
                                      % round(dark_len)})
            dark_from, dark_len = None, 0.0
        acc += e["length"]
    if dark_from is not None and dark_len >= 120:
        notes.append({"type": "夜間照明", "km": round(dark_from / 1000.0, 2),
                      "text": "無路燈標記路段約 %d 公尺，夜跑請帶頭燈" % round(dark_len)})
    # 無號誌斑馬線
    for pt, kind, name in net.crossings:
        if kind in ("traffic_signals", "signals"):
            continue
        acc, best, bestd = 0.0, None, 1e18
        for i in range(len(route.pts) - 1):
            d = hav(pt, route.pts[i])
            if d < bestd:
                bestd, best = d, acc
            acc += hav(route.pts[i], route.pts[i + 1])
        if bestd <= 25:
            notes.append({"type": "無號誌路口", "km": round(best / 1000.0, 2),
                          "text": "無號誌斑馬線／穿越點%s，過馬路請停看"
                                  % (("（%s）" % name) if name else "")})
    # 主要幹道路段
    acc = 0.0
    for e in route.edges:
        if e["hw"] in ("primary", "secondary") and e["length"] >= 200:
            notes.append({"type": "幹道路段", "km": round(acc / 1000.0, 2),
                          "text": "%s 為主要幹道（%d 公尺），車流較快請走人行道"
                                  % (e.get("name") or "此路段", round(e["length"]))})
        acc += e["length"]
    notes.sort(key=lambda x: x["km"])
    # 同型別相鄰的合併掉，不然一條路會列出十幾筆
    dedup, last = [], None
    for n in notes:
        if last and last["type"] == n["type"] and abs(last["km"] - n["km"]) < 0.35:
            continue
        dedup.append(n)
        last = n
    return dedup


# ────────────────────────── 圖 ──────────────────────────
def _font(sz, bold=False):
    for p in ("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc",
              "C:/Windows/Fonts/msjh.ttc"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def sketch(net, route, title, sub, W=1600, pad=90, show_net=True):
    """把路網畫成底圖、路線疊上去。不用線上圖磚，沒網路也畫得出來。"""
    allpts = list(route.pts) + list(route.target)
    lats = [p[0] for p in allpts]
    lons = [p[1] for p in allpts]
    o = ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
    h_m = hav((min(lats), o[1]), (max(lats), o[1]))
    w_m = hav((o[0], min(lons)), (o[0], max(lons)))
    TOP = 130                      # 標題列高度，畫布要扣掉才不會被壓到
    H = int(W * 0.86)
    # 兩個方向都要塞得下，取較嚴格的那個縮放；先前只看長邊，圖會被切掉
    s = min((W - pad * 2) / max(w_m, 1.0), (H - TOP - pad) / max(h_m, 1.0))
    span = max(w_m, h_m) * 1.15

    def P(p):
        x, y = enu(o, p)
        return (W / 2 + x * s, TOP + (H - TOP) / 2 - y * s)

    im = Image.new("RGB", (W, H), (252, 252, 250))
    d = ImageDraw.Draw(im, "RGBA")
    if show_net:
        half = span / 2 * 1.15
        for a, b, e in net.G.edges(data=True):
            pa, pb = net.coord[a], net.coord[b]
            if (abs(hav((pa[0], o[1]), (o[0], o[1]))) > half
                    or abs(hav((o[0], pa[1]), (o[0], o[1]))) > half):
                continue
            col = (206, 210, 218) if e["hw"] not in ("primary", "secondary") else (196, 186, 176)
            wdt = 3 if e["hw"] in ("primary", "secondary") else 1
            d.line([P(pa), P(pb)], fill=col, width=wdt)
    d.line([P(p) for p in route.target], fill=(150, 190, 230, 190), width=5)
    d.line([P(p) for p in route.pts], fill=(255, 255, 255, 210), width=15)
    d.line([P(p) for p in route.pts], fill=(214, 0, 28), width=8)
    st = P(route.pts[0])
    d.ellipse([st[0] - 15, st[1] - 15, st[0] + 15, st[1] + 15],
              fill=(255, 255, 255), outline=(0, 120, 45), width=6)
    d.rectangle([0, 0, W, 120], fill=(255, 255, 255))
    d.text((30, 18), title, fill=(20, 20, 20), font=_font(44, True))
    d.text((30, 74), sub, fill=(70, 70, 70), font=_font(24))
    d.text((30, H - 34), "底圖：OpenStreetMap 貢獻者（ODbL）　淺藍＝目標圖形　紅＝實際路網軌跡",
           fill=(120, 120, 120), font=_font(20))
    return im


def report_md(meta, sc, cues, notes, marks):
    from .scoring import LABEL
    L = ["# %s" % meta["title"], "",
         "| 項目 | 內容 |", "|---|---|",
         "| 圖形 | %s |" % meta["shape_name"],
         "| 行政區 | %s |" % meta["district_name"],
         "| 級距 | %s |" % meta["level_label"],
         "| 里程 | %.2f km |" % meta["km"],
         "| 起終點 | %.6f, %.6f |" % (meta["start"][0], meta["start"][1]),
         "| 綜合評分 | **%.1f / 100** |" % sc["total"], "",
         "## 一、評分（依規格書第二節權重）", "",
         "| 構面 | 權重 | 得分 |", "|---|---|---|"]
    for k, v in sc["parts"].items():
        L.append("| %s | %d%% | %.1f |" % (LABEL[k], sc["weights"][k], v))
    L.append("")
    for k, dd in sc["detail"].items():
        L.append("**%s**" % LABEL[k])
        L.append("")
        L.append(" ／ ".join("%s：%s" % (a, b) for a, b in dd.items()))
        L.append("")
    L += ["## 二、路名循序導航表（Cue Sheet）", "",
          "| # | 累計 km | 動作 | 路名 | 該段長度 |", "|---|---|---|---|---|"]
    for c in cues:
        L.append("| %d | %.2f | %s | %s | %d m |"
                 % (c["seq"], c["km"], c["action"], c["road"], c["len_m"]))
    L += ["", "## 三、安全提醒", ""]
    if notes:
        L += ["| 累計 km | 類別 | 提醒 |", "|---|---|---|"]
        L += ["| %.2f | %s | %s |" % (n["km"], n["type"], n["text"]) for n in notes]
    else:
        L.append("沿線未偵測到需特別標記的路段。")
    L += ["", "## 四、地標打卡點", ""]
    if marks:
        L += ["| 累計 km | 地標 | 類型 | 距路線 |", "|---|---|---|---|"]
        L += ["| %.2f | %s | %s | %d m |" % (m[1], m[2], m[3], m[0]) for m in marks[:3]]
    else:
        L.append("沿線 180 公尺內未找到具名地標，建議手動指定。")
    L += ["", "---", "",
          "資料來源：OpenStreetMap 貢獻者（ODbL）；高程 Open-Meteo。",
          "本表由圖形路跑路線規劃系統自動產生，實跑前請再以現場狀況確認。"]
    return "\n".join(L)


def save_all(outdir, slug, route, net, meta, sc, cues, notes, marks, img):
    os.makedirs(outdir, exist_ok=True)
    wpts = [(m[4][0], m[4][1], m[2], "地標") for m in marks[:3] if len(m) > 4]
    paths = {}
    for ext, txt in (("gpx", gpx(route, meta["title"], wpts)),
                     ("kml", kml(route, meta["title"], wpts))):
        p = os.path.join(outdir, "%s.%s" % (slug, ext))
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(txt)
        paths[ext] = p
    p = os.path.join(outdir, "%s_報告.md" % slug)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(report_md(meta, sc, cues, notes, marks))
    paths["md"] = p
    p = os.path.join(outdir, "%s_cue.csv" % slug)
    with io.open(p, "w", encoding="utf-8-sig") as f:
        f.write("序,累計km,動作,路名,該段長度m,緯度,經度\n")
        for c in cues:
            f.write("%d,%.2f,%s,%s,%d,%.6f,%.6f\n"
                    % (c["seq"], c["km"], c["action"], c["road"], c["len_m"],
                       c["lat"], c["lon"]))
    paths["csv"] = p
    p = os.path.join(outdir, "%s_地圖.png" % slug)
    img.save(p)
    paths["png"] = p
    return paths

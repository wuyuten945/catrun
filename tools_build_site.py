# -*- coding: utf-8 -*-
"""產生 catrun.ego-intl.com 的靜態站內容。

公開站是純靜態的：所有路線離線先算好，網站只負責呈現與下載。
理由——排一條路線要 7~10 秒 CPU 與一份 160 MB 的路網，放上免費主機
會慢到不能用，而且開放給任何人觸發等於把 Overpass 當公共提款機。

輸出到 site/：
    index.html / app.js / styles.css   （前端，另外手寫）
    data/index.json                    （全部路線的摘要，首頁一次載入）
    data/route/<slug>.json             （單條路線的導航表、提醒、地標）
    data/shapes.json                   （圖形庫）
    img/<slug>.jpg  gpx/<slug>.gpx  kml/<slug>.kml
"""
import io
import json
import os
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image                                     # noqa: E402
from catrun import export, planner, shapes                # noqa: E402
from catrun.config import DISTRICTS, LEVELS, WEIGHTS      # noqa: E402
from catrun.scoring import LABEL                          # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site")
# 旗艦級 21 km 在單一行政區畫不下（圖形長邊要 4 公里以上），本版只收前兩級
LEVELS_USED = ["standard", "challenge"]
SHAPES_USED = ["cat_hand", "cat_sit", "cat_face", "cat_paw", "cat_curl"]


def ensure(*parts):
    p = os.path.join(SITE, *parts)
    os.makedirs(p, exist_ok=True)
    return p


def save_jpg(img, path, width=1180, q=74):
    im = img.convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(path, "JPEG", quality=q, optimize=True, progressive=True)


def main():
    ensure("data", "route")
    ensure("img")
    ensure("gpx")
    ensure("kml")
    index = {"generated": time.strftime("%Y-%m-%d"),
             "weights": WEIGHTS, "labels": LABEL,
             "districts": {k: {"name": v["name"], "style": v["style"],
                               "traits": v["traits"]} for k, v in DISTRICTS.items()},
             "levels": {k: {"label": v["label"], "min": v["min_km"], "max": v["max_km"]}
                        for k, v in LEVELS.items() if k in LEVELS_USED},
             "routes": [], "failed": []}
    with io.open(os.path.join(SITE, "data", "shapes.json"), "w", encoding="utf-8") as f:
        json.dump({k: {"name": shapes.SHAPES[k]["name"],
                       "note": shapes.SHAPES[k]["note"],
                       "pts": shapes.normalised(k)} for k in SHAPES_USED},
                  f, ensure_ascii=False)

    t0 = time.time()
    for d in DISTRICTS:
        print("\n════ %s ════" % DISTRICTS[d]["name"], flush=True)
        for s in SHAPES_USED:
            for lv in LEVELS_USED:
                slug = "%s-%s-%s" % (d, s.replace("cat_", ""), lv)
                try:
                    r = planner.plan(d, s, lv, log=lambda *a: None, save=False)
                except Exception as e:      # noqa - 單一組合無解不中斷整批
                    print("  ✗ %-28s %r" % (slug, e), flush=True)
                    index["failed"].append({"slug": slug, "err": str(e)})
                    continue
                sc, meta = r["score"], r["meta"]
                save_jpg(r["img"], os.path.join(SITE, "img", slug + ".jpg"))
                marks = r["marks"]
                wp = [(m[4][0], m[4][1], m[2], "地標") for m in marks[:3]]
                for ext, txt in (("gpx", export.gpx(r["route"], meta["title"], wp)),
                                 ("kml", export.kml(r["route"], meta["title"], wp))):
                    with io.open(os.path.join(SITE, ext, slug + "." + ext),
                                 "w", encoding="utf-8") as f:
                        f.write(txt)
                with io.open(os.path.join(SITE, "data", "route", slug + ".json"),
                             "w", encoding="utf-8") as f:
                    json.dump({"slug": slug, "detail": sc["detail"], "cues": r["cues"],
                               "notes": r["notes"],
                               "marks": [[m[0], m[1], m[2], m[3]] for m in marks[:3]],
                               "supply": sc["supply"][:12]}, f, ensure_ascii=False)
                index["routes"].append({
                    "slug": slug, "district": d, "shape": s, "level": lv,
                    "title": meta["title"], "km": meta["km"], "climb": meta["climb_m"],
                    "start": [round(meta["start"][0], 6), round(meta["start"][1], 6)],
                    "total": sc["total"], "parts": sc["parts"],
                    "cues": len(r["cues"]), "notes": len(r["notes"]),
                    "mark": (marks[0][2] if marks else ""),
                })
                print("  ✓ %-28s %5.2f km  %5.1f 分" % (slug, meta["km"], sc["total"]),
                      flush=True)
        planner._NET_CACHE.clear()      # 一次只留一區的路網，否則 6 區會吃掉 1 GB
    index["routes"].sort(key=lambda x: -x["total"])
    with io.open(os.path.join(SITE, "data", "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    print("\n完成 %d 條、失敗 %d 條，耗時 %.1f 分"
          % (len(index["routes"]), len(index["failed"]), (time.time() - t0) / 60))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise

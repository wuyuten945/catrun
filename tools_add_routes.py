# -*- coding: utf-8 -*-
"""替尚未收錄的行政區各產生一條現成路線，併進公開站的路線庫。

用手繪蹲坐貓（cat_hand）＋標準大眾級：那是使用者自己的原稿，也是目前分數
最高的圖形，拿來當每個區的代表最合適。

只補「還沒有路線」的區——已經有 60 條的原本六區不重跑，省時間也避免既有
路線被換掉（分享出去的連結會失效）。
"""
import io
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image                                      # noqa: E402
from catrun import export, planner                         # noqa: E402
from catrun.config import DISTRICTS, LEVELS                # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, "site")
SHAPE, LEVEL = "cat_hand", "standard"


def save_jpg(img, path, width=1180, q=74):
    im = img.convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(path, "JPEG", quality=q, optimize=True, progressive=True)


def main():
    ip = os.path.join(SITE, "data", "index.json")
    with io.open(ip, encoding="utf-8") as f:
        index = json.load(f)
    have = set(r["district"] for r in index["routes"])
    # 跳過不建議的區（和平區實測爬升 1322 m，上限 44 m，超標 30 倍）。
    # 路網照樣提供給「自己排」，但一條這種軌跡不該躺在公開路線庫裡當推薦。
    todo = [k for k in DISTRICTS
            if k not in have
            and DISTRICTS[k].get("recommended", True)
            and os.path.exists(os.path.join(SITE, "net", k + ".json"))]
    skipped = [DISTRICTS[k]["name"] for k in DISTRICTS
               if not DISTRICTS[k].get("recommended", True)]
    if skipped:
        print("不列入路線庫：%s" % "、".join(skipped))
    print("路線庫已有 %d 區 %d 條；要補 %d 區"
          % (len(have), len(index["routes"]), len(todo)), flush=True)
    if not todo:
        print("沒有要補的區")
        return

    t0 = time.time()
    for n, d in enumerate(todo, 1):
        slug = "%s-%s-%s" % (d, SHAPE.replace("cat_", ""), LEVEL)
        print("[%d/%d] %s" % (n, len(todo), DISTRICTS[d]["name"]), flush=True)
        try:
            r = planner.plan(d, SHAPE, LEVEL, log=lambda *a: None, save=False)
        except Exception as e:      # noqa - 山區之類排不出來的區就跳過，不中斷整批
            print("   ✗ %r" % (e,), flush=True)
            index.setdefault("failed", []).append({"slug": slug, "err": str(e)})
            planner._NET_CACHE.clear()
            planner._NET_ORDER.clear()
            continue
        sc, meta = r["score"], r["meta"]
        save_jpg(r["img"], os.path.join(SITE, "img", slug + ".jpg"))
        marks = r["marks"]
        wp = [(m[4][0], m[4][1], m[2], "地標") for m in marks[:3]]
        for ext, txt in (("gpx", export.gpx(r["route"], meta["title"], wp)),
                         ("kml", export.kml(r["route"], meta["title"], wp))):
            with io.open(os.path.join(SITE, ext, slug + "." + ext), "w",
                         encoding="utf-8") as f:
                f.write(txt)
        with io.open(os.path.join(SITE, "data", "route", slug + ".json"), "w",
                     encoding="utf-8") as f:
            json.dump({"slug": slug, "detail": sc["detail"], "cues": r["cues"],
                       "notes": r["notes"],
                       "marks": [[m[0], m[1], m[2], m[3]] for m in marks[:3]],
                       "supply": sc["supply"][:12]}, f, ensure_ascii=False)
        index["routes"].append({
            "slug": slug, "district": d, "shape": SHAPE, "level": LEVEL,
            "title": meta["title"], "km": meta["km"], "climb": meta["climb_m"],
            "start": [round(meta["start"][0], 6), round(meta["start"][1], 6)],
            "total": sc["total"], "parts": sc["parts"],
            "cues": len(r["cues"]), "notes": len(r["notes"]),
            "mark": (marks[0][2] if marks else ""),
        })
        print("   ✓ %.2f km  %.1f 分" % (meta["km"], sc["total"]), flush=True)
        planner._NET_CACHE.clear()
        planner._NET_ORDER.clear()

    # 篩選選單要能看到新的區
    index["districts"] = {k: {"name": v["name"], "style": v["style"],
                              "traits": v["traits"], "group": v.get("group", "其他")}
                          for k, v in DISTRICTS.items()
                          if any(r["district"] == k for r in index["routes"])}
    index["routes"].sort(key=lambda x: -x["total"])
    index["generated"] = time.strftime("%Y-%m-%d")
    with io.open(ip, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    print("\n路線庫共 %d 條、涵蓋 %d 區，耗時 %.1f 分"
          % (len(index["routes"]), len(index["districts"]), (time.time() - t0) / 60))


if __name__ == "__main__":
    main()

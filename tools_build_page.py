# -*- coding: utf-8 -*-
"""把 8 條路線重跑一次並匯出成給網頁用的 JSON（含縮圖 base64）。

網頁是自足的單檔，圖必須內嵌，所以這裡順便把地圖壓成 JPEG。
"""
import base64
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image                                    # noqa: E402
from catrun import shapes                                # noqa: E402
from catrun.config import DISTRICTS, LEVELS, WEIGHTS     # noqa: E402
from catrun.planner import plan                          # noqa: E402
from catrun.scoring import LABEL                         # noqa: E402

JOBS = [("wuri", "cat_sit"), ("xitun", "cat_sit"), ("nantun", "cat_face"),
        ("east", "cat_face"), ("west", "cat_paw"), ("south", "cat_paw"),
        ("wuri", "cat_curl"), ("west", "cat_curl")]
# 原稿描圖跨區比較（使用者指定的五區）
HAND = [("south", "cat_hand"), ("nantun", "cat_hand"), ("xitun", "cat_hand"),
        ("west", "cat_hand"), ("wuri", "cat_hand")]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def thumb(path, width=1180, q=76):
    im = Image.open(path).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def main():
    out = {"weights": WEIGHTS, "labels": LABEL,
           "districts": {k: {"name": v["name"], "style": v["style"],
                             "traits": v["traits"]} for k, v in DISTRICTS.items()},
           "shapes": {k: {"name": v["name"], "note": v["note"],
                          "pts": shapes.normalised(k)} for k in shapes.SHAPES
                      for v in [shapes.SHAPES[k]]},
           "routes": []}
    def one(d, s):
        r = plan(d, s, "standard", log=lambda *a: None)
        slug = "%s_%s_standard" % (d, s)
        return {
            "slug": slug, "district": d, "shape": s,
            "title": r["meta"]["title"], "km": r["meta"]["km"],
            "climb": r["meta"]["climb_m"], "start": r["meta"]["start"],
            "level": LEVELS["standard"]["label"],
            "total": r["score"]["total"], "parts": r["score"]["parts"],
            "detail": r["score"]["detail"], "cues": r["cues"],
            "notes": r["notes"],
            "marks": [[m[0], m[1], m[2], m[3]] for m in r["marks"][:3]],
            "img": thumb(os.path.join(OUT, slug + "_地圖.png")),
        }

    out["hand"] = []
    for d, s in HAND:
        print("→(描圖)", d, flush=True)
        x = one(d, s)
        out["hand"].append(x)
        print("   %.2f km  %.1f 分" % (x["km"], x["total"]), flush=True)
    out["hand"].sort(key=lambda x: -x["total"])
    out["trace_png"] = thumb(os.path.join(OUT, "cat_hand_描圖.png"), width=560, q=88)
    out["source_png"] = thumb(r"C:/Users/User/Desktop/S__19505160.jpg", width=560, q=88)

    for d, s in JOBS:
        print("→", d, s, flush=True)
        x = one(d, s)
        out["routes"].append(x)
        print("   %.2f km  %.1f 分" % (x["km"], x["total"]), flush=True)
    out["routes"].sort(key=lambda x: -x["total"])
    p = os.path.join(OUT, "page_data.json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("寫出", p, "%.1f MB" % (os.path.getsize(p) / 1e6))


if __name__ == "__main__":
    main()

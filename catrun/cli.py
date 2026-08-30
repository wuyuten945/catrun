# -*- coding: utf-8 -*-
"""命令列介面：python -m catrun ..."""
import argparse
import sys

from .config import DISTRICTS, LEVELS
from .planner import plan, plan_best
from .shapes import SHAPES


def _log(*a):
    print("  ", *a, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="catrun", description="台中市圖形路跑路線規劃系統（貓咪）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("plan", help="規劃一條路線")
    p.add_argument("--district", "-d", default="wuri", choices=list(DISTRICTS))
    p.add_argument("--shape", "-s", default="cat_sit", choices=list(SHAPES))
    p.add_argument("--level", "-l", default="standard", choices=list(LEVELS))
    p.add_argument("--center", help="指定中心 lat,lng（不給就自動搜尋）")
    p.add_argument("--refresh", action="store_true", help="重抓 OSM 資料")

    b = sub.add_parser("batch", help="一次規劃多條（規格書共 8 條）")
    b.add_argument("--level", "-l", default="standard", choices=list(LEVELS))
    b.add_argument("--refresh", action="store_true")

    tr = sub.add_parser("trace", help="從手繪影像描出圖形並登錄")
    tr.add_argument("image")
    tr.add_argument("--key", required=True, help="圖形代號，例如 cat_hand")
    tr.add_argument("--name", required=True, help="顯示名稱")
    tr.add_argument("--note", default="", help="說明")
    tr.add_argument("--points", type=int, default=64, help="簡化後目標點數")
    tr.add_argument("--close", type=int, default=6, help="手繪缺口補償像素")

    bs = sub.add_parser("best", help="同一圖形跨多區規劃並排名")
    bs.add_argument("--shape", "-s", required=True)
    bs.add_argument("--districts", "-d", required=True,
                    help="逗號分隔，例如 wuri,south,nantun,west,xitun")
    bs.add_argument("--level", "-l", default="standard", choices=list(LEVELS))

    sub.add_parser("list", help="列出可用的行政區、圖形與級距")

    a = ap.parse_args(argv)
    if a.cmd == "list":
        print("行政區：")
        for k, v in DISTRICTS.items():
            print("  %-8s %s ── %s" % (k, v["name"], v["style"]))
        print("圖形：")
        for k, v in SHAPES.items():
            print("  %-9s %s ── %s" % (k, v["name"], v["note"]))
        print("級距：")
        for k, v in LEVELS.items():
            print("  %-10s %s %.0f–%.0f km" % (k, v["label"], v["min_km"], v["max_km"]))
        return 0

    if a.cmd == "plan":
        c = None
        if a.center:
            lat, lng = a.center.split(",")
            c = (float(lat), float(lng))
        r = plan(a.district, a.shape, a.level, refresh=a.refresh, log=_log, center=c)
        print("\n%s：%.1f 分（%.2f km）" % (r["meta"]["title"], r["score"]["total"], r["meta"]["km"]))
        for k, v in r["score"]["parts"].items():
            print("   %-10s %5.1f" % (k, v))
        return 0

    if a.cmd == "trace":
        import json
        import os
        from .trace import preview, trace
        pts, info = trace(a.image, close_px=a.close, target_pts=a.points)
        for k, v in info.items():
            print("  %s = %s" % (k, v))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(root, "data", "shapes")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, a.key + ".json"), "w", encoding="utf-8") as f:
            json.dump({"key": a.key, "name": a.name, "note": a.note,
                       "aspect": info["寬高比"], "pts": pts}, f, ensure_ascii=False)
        png = os.path.join(root, "out", a.key + "_描圖.png")
        preview(pts, png)
        print("已登錄圖形 %s，預覽：%s" % (a.key, png))
        return 0

    if a.cmd == "best":
        ds = [x.strip() for x in a.districts.split(",") if x.strip()]
        bad = [x for x in ds if x not in DISTRICTS]
        if bad:
            print("沒有這些行政區：%s" % "、".join(bad))
            return 1
        rows = plan_best(ds, a.shape, a.level, log=_log)
        print("" + chr(10) + "=== %s 跨區排名 ===" % a.shape)
        print("%-8s %8s %7s %7s %7s %7s %7s %7s"
              % ("行政區", "里程", "總分", "圖形", "安全", "里程", "區域", "補給"))
        for r in rows:
            p = r["score"]["parts"]
            print("%-8s %6.2fkm %7.1f %7.1f %7.1f %7.1f %7.1f %7.1f"
                  % (r["meta"]["district_name"], r["meta"]["km"], r["score"]["total"],
                     p["shape"], p["safety"], p["distance"], p["district"],
                     p["logistics"]))
        return 0

    if a.cmd == "batch":
        # 規格書一、共 8 條路線；預設把 4 種貓形分配到適配的行政區
        jobs = [("wuri", "cat_sit"), ("xitun", "cat_sit"), ("nantun", "cat_face"),
                ("east", "cat_face"), ("west", "cat_paw"), ("south", "cat_paw"),
                ("wuri", "cat_curl"), ("west", "cat_curl")]
        rows = []
        for d, s in jobs:
            print("\n=== %s / %s ===" % (d, s), flush=True)
            try:
                r = plan(d, s, a.level, refresh=a.refresh, log=_log)
                rows.append((d, s, r["meta"]["km"], r["score"]["total"]))
            except Exception as e:      # noqa - 一條失敗不該中斷整批
                print("   失敗：%r" % (e,))
                rows.append((d, s, 0, 0))
        print("\n=== 批次結果 ===")
        for d, s, km, sc in rows:
            print("  %-7s %-9s %5.2f km  %5.1f 分" % (d, s, km, sc))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

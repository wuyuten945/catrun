# -*- coding: utf-8 -*-
"""把六個行政區的路網匯出成瀏覽器可直接使用的靜態檔。

公開站要讓使用者自己上傳圖形、當場排路線，演算法就得搬進瀏覽器；
瀏覽器沒辦法打 Overpass（會被當成公共提款機，也慢），所以路網要事先
壓成緊湊格式放成靜態檔，使用者選到哪一區才下載哪一區。

每區一個 net/<key>.json，內含 base64 的路網本體與路名表、沿線設施。

為什麼把二進位 base64 塞進 JSON 而不是分開放 .bin：靜態主機不壓縮
application/octet-stream，raw .bin 要下載 1.3 MB；包進 JSON 之後主機會自動
Brotli 壓縮，實際傳輸約 0.7 MB。base64 撐大的 33% 壓回去還有剩。

.bin 格式（little-endian）：
  magic "CRG2"                          4
  n_nodes, n_edges                      u32 × 2
  lat0, lon0                            f64 × 2      座標原點
  nodes[n_nodes]  lat_off, lon_off      u32 × 2      百萬分之一度
  edges[n_edges]  a, b                  u32 × 2      節點索引
                  len_dm                u16          公寸
                  safety_milli          u16          安全分 ×1000
                  name_id               u16          路名索引（0=無名）
                  flags                 u16          bit0-4 道路分級、bit5-6 照明
"""
import base64
import io
import json
import os
import struct
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from catrun import planner                                  # noqa: E402
from catrun.config import DISTRICTS, ROAD_SAFETY            # noqa: E402

# 道路分級的索引表。JS 端要靠它判斷綠園道比例與幹道提醒，順序不能亂動——
# 動了就得重新匯出所有區，否則舊檔會被解讀成別的道路類型。
HW = ["footway", "path", "pedestrian", "steps", "cycleway", "living_street",
      "track", "residential", "service", "unclassified", "tertiary",
      "tertiary_link", "secondary", "secondary_link", "primary", "primary_link"]
HW_IDX = {h: i for i, h in enumerate(HW)}
LIT = {"": 0, "yes": 1, "no": 2}

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(ROOT, "site", "net")


def export(key, log=print):
    net = planner.get_net(key)
    G = net.G
    ids = {n: i for i, n in enumerate(G.nodes())}
    lat0 = min(net.coord[x][0] for x in ids)
    lon0 = min(net.coord[x][1] for x in ids)

    buf = bytearray(b"CRG2")
    buf += struct.pack("<II", len(ids), G.number_of_edges())
    buf += struct.pack("<dd", lat0, lon0)
    for x in ids:
        la, lo = net.coord[x]
        buf += struct.pack("<II", int(round((la - lat0) * 1e6)),
                           int(round((lo - lon0) * 1e6)))
    names = [""]
    nidx = {"": 0}
    over = 0
    for a, b, d in G.edges(data=True):
        nm = d.get("name") or ""
        if nm not in nidx:
            nidx[nm] = len(names)
            names.append(nm)
        length = d["length"]
        if length > 6553.5:
            over += 1
        flags = HW_IDX.get(d.get("hw", ""), 31) | (LIT.get(d.get("lit", ""), 0) << 5)
        buf += struct.pack("<IIHHHH", ids[a], ids[b],
                           min(65535, int(round(length * 10))),
                           int(round(d["safety"] * 1000)),
                           min(65535, nidx[nm]), flags)
    if over:
        log("   ⚠ %d 段超過 6553.5 公尺被截斷（長度欄位是 u16 公寸）" % over)

    s, w, n, e = net.cfg["bbox"]
    side = {
        "key": key, "name": DISTRICTS[key]["name"],
        "style": DISTRICTS[key]["style"], "traits": DISTRICTS[key]["traits"],
        "aspect": DISTRICTS[key]["aspect"],
        "bbox": [s, w, n, e],
        "nodes": len(ids), "edges": G.number_of_edges(),
        "hw": HW,
        "names": names,
        "signals": [[round(p[0], 6), round(p[1], 6)] for p in net.signals],
        "crossings": [[round(p[0][0], 6), round(p[0][1], 6), p[1], p[2]]
                      for p in net.crossings],
        "supply": [[round(p[0][0], 6), round(p[0][1], 6), p[1]] for p in net.supply],
        "transit": [[round(p[0][0], 6), round(p[0][1], 6), p[1], p[2]]
                    for p in net.transit],
        "landmarks": [[round(p[0][0], 6), round(p[0][1], 6), p[1], p[2]]
                      for p in net.landmarks],
    }
    return bytes(buf), side


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    index = {}
    failed, t0 = [], time.time()
    for n, key in enumerate(DISTRICTS, 1):
        cfg = DISTRICTS[key]
        if not cfg.get("recommended", True):
            # 公開站不收：和平區實測爬升 1322 m（上限 44 m）、補給 0 分，路線不可用，
            # 而它一個檔就 7.47 MB（23 萬節點）佔全部的 14%。
            # 本機工具不受影響——那邊走 Overpass 快取，不看 site/net。
            print("── [%d/%d] %s：不建議規劃，公開站不收" % (n, len(DISTRICTS), cfg["name"]),
                  flush=True)
            continue
        print("── [%d/%d] %s" % (n, len(DISTRICTS), cfg["name"]), flush=True)
        try:
            blob, side = export(key)
        except Exception as e:      # noqa - 一區失敗不該中斷整批，最後統一回報
            print("   ✗ 失敗：%r" % (e,), flush=True)
            failed.append((key, cfg["name"], str(e)))
            planner._NET_CACHE.clear()
            planner._NET_ORDER.clear()
            continue
        side["bin"] = base64.b64encode(blob).decode()
        side["recommended"] = cfg.get("recommended", True)
        side["group"] = cfg.get("group", "其他")
        p = os.path.join(OUTDIR, key + ".json")
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(side, f, ensure_ascii=False)
        index[key] = {"name": side["name"], "style": side["style"],
                      "traits": side["traits"], "bbox": side["bbox"],
                      "nodes": side["nodes"], "edges": side["edges"],
                      "recommended": side["recommended"],
                      "group": side["group"],
                      "kb": round(os.path.getsize(p) / 1024)}
        print("   %d 節點、%d 路段 → %.2f MB（主機會再 Brotli 壓縮）"
              % (side["nodes"], side["edges"], os.path.getsize(p) / 1e6), flush=True)
        planner._NET_CACHE.clear()
        planner._NET_ORDER.clear()
        time.sleep(3)       # 對 Overpass 客氣一點，連續 29 個大查詢容易被限流
    with io.open(os.path.join(OUTDIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"hw": HW, "districts": index}, f, ensure_ascii=False)
    tot = sum(v["kb"] for v in index.values())
    print("\n六區合計 %.1f MB（使用者一次只會下載一區）" % (tot / 1024))


if __name__ == "__main__":
    main()

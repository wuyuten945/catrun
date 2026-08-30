# -*- coding: utf-8 -*-
"""把一區的路網匯出成瀏覽器好讀的緊湊二進位格式。

給 JS 版效能實測用，也是日後真的要把演算法搬進瀏覽器時的資料格式雛形。

格式（全部 little-endian）：
    magic  "CRG1"                      4 bytes
    n_nodes, n_edges                   uint32 × 2
    lat0, lon0                         float64 × 2   （座標原點）
    nodes  : lat_off, lon_off          uint32 × 2 × n_nodes（百萬分之一度）
    edges  : a, b                      uint32 × 2
             len_dm                    uint16（公寸，上限 6553.5 m）
             safety_milli              uint16（安全分 ×1000）
"""
import base64
import io
import json
import os
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from catrun import shapes                       # noqa: E402
from catrun.config import DISTRICTS             # noqa: E402
from catrun.planner import get_net              # noqa: E402


def export(key):
    net = get_net(key)
    ids = {n: i for i, n in enumerate(net.G.nodes())}
    lat0 = min(net.coord[x][0] for x in ids)
    lon0 = min(net.coord[x][1] for x in ids)
    out = bytearray(b"CRG1")
    out += struct.pack("<II", len(ids), net.G.number_of_edges())
    out += struct.pack("<dd", lat0, lon0)
    for x in ids:
        la, lo = net.coord[x]
        out += struct.pack("<II", int(round((la - lat0) * 1e6)),
                           int(round((lo - lon0) * 1e6)))
    for a, b, d in net.G.edges(data=True):
        out += struct.pack("<IIHH", ids[a], ids[b],
                           min(65535, int(round(d["length"] * 10))),
                           int(round(d["safety"] * 1000)))
    s, w, n, e = net.cfg["bbox"]
    meta = {"key": key, "name": DISTRICTS[key]["name"], "bbox": [s, w, n, e],
            "nodes": len(ids), "edges": net.G.number_of_edges()}
    return bytes(out), meta


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "nantun"
    blob, meta = export(key)
    root = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(root, "out", "graph_%s.bin" % key)
    with open(p, "wb") as f:
        f.write(blob)
    pj = os.path.join(root, "out", "graph_%s.json" % key)
    with io.open(pj, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "shape": shapes.normalised("cat_hand"),
                   "b64": base64.b64encode(blob).decode()}, f)
    print("%s：%d 節點、%d 路段" % (meta["name"], meta["nodes"], meta["edges"]))
    print("  二進位 %.2f MB → base64 %.2f MB" % (len(blob) / 1e6,
                                                os.path.getsize(pj) / 1e6))
    print("  寫出：%s" % pj)

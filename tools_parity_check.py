# -*- coding: utf-8 -*-
"""產生 JS↔Python 對照測試的基準值。

跑法：
    python tools_parity_check.py      產生 out/parity_expected.json 與 out/parity_gray.json
    node tools_parity_check.js        用那份基準檢查 JS 引擎

任何一邊改了演算法都要重跑這兩支。兩套實作漂移是這個架構最大的風險——
公開站算 82 分、本機工具算 79 分，沒有人會知道哪個是對的。
"""
import io
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np                                  # noqa: E402
from PIL import Image                               # noqa: E402

from catrun import shapes                           # noqa: E402
from catrun.planner import plan                     # noqa: E402
from catrun.trace import trace                      # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
IMG = r"C:/Users/User/Desktop/S__19505160.jpg"
OUT = os.path.join(ROOT, "out")


def main():
    os.makedirs(OUT, exist_ok=True)
    print("1. 描圖基準")
    pts, info = trace(IMG, close_px=6, target_pts=64)
    print("   點數 %d、寬高比 %.3f" % (len(pts), info["寬高比"]))

    # 把灰階影像原樣交給 JS，兩邊才是吃同一份輸入。
    # 灰階值就是 0~255，用 base64 存 Uint8 即可——存成浮點數 JSON 會膨脹到好幾 MB，
    # 大到讓人想刪掉它，結果測試就跑不動了（真的發生過）。
    import base64
    a = np.asarray(Image.open(IMG).convert("L"), dtype=np.uint8)
    with io.open(os.path.join(OUT, "parity_gray.json"), "w", encoding="utf-8") as f:
        json.dump({"w": int(a.shape[1]), "h": int(a.shape[0]),
                   "b64": base64.b64encode(a.tobytes()).decode()}, f)
    print("   灰階影像 %dx%d 已輸出（%.0f KB）"
          % (a.shape[1], a.shape[0],
             os.path.getsize(os.path.join(OUT, "parity_gray.json")) / 1024))

    print("2. 規劃基準（南區 × cat_hand × 標準級）")
    t0 = time.time()
    r = plan("south", "cat_hand", "standard", log=lambda *a2: None, save=False)
    secs = round(time.time() - t0, 1)
    sc = r["score"]
    print("   %.2f km、%.1f 分、%.1f 秒" % (r["meta"]["km"], sc["total"], secs))

    # 對照測試要能離線重跑，所以基準另外算一份「不計高程」的分數：
    # JS 端在離線測試時拿不到 Open-Meteo，兩邊都用 climb=None 才比得準。
    from catrun import fitter, scoring
    sc0 = scoring.score(r["net"], r["route"], "standard", "south", None)

    # ── 固定佈局的基準 ──
    # 搜尋是啟發式的，兩套實作在平手處會挑到不同路線，那是正常的。
    # 但「同一條路線的評分」必須一模一樣，否則就是真的算錯了。
    # 所以另外用一組寫死的（中心、尺寸、旋轉）建路線，這一項要嚴格對上。
    print("3. 固定佈局基準（隔離搜尋的隨機性）")
    net = r["net"]
    base = shapes.normalised("cat_hand")
    s0, w0, n0, e0 = net.cfg["bbox"]
    center = (s0 + (n0 - s0) * 0.5, w0 + (e0 - w0) * 0.5)
    size, rot = 1650.0, -8.0
    fixed = fitter.build(net, base, center, size, rot)
    if fixed is None:
        raise SystemExit("固定佈局建不出路線，換一組參數")
    fsc = scoring.score(net, fixed, "standard", "south", None)
    from catrun import export as _ex
    fcues = _ex.cue_sheet(fixed)
    fnotes = _ex.safety_notes(net, fixed)
    print("   %.3f km、%.1f 分、cue %d 段" % (fixed.km, fsc["total"], len(fcues)))
    exp = {
        "trace": {"points": info["簡化後點數"], "aspect": info["寬高比"], "pts": pts},
        "plan": {
            "shape": shapes.normalised("cat_hand"),
            "km": r["meta"]["km"], "total": sc0["total"], "parts": sc0["parts"],
            "cues": len(r["cues"]), "notes": len(r["notes"]), "seconds": secs,
            "total_with_climb": sc["total"], "climb": r["meta"]["climb_m"],
        },
        "fixed": {
            "center": [center[0], center[1]], "size": size, "rot": rot,
            "km": round(fixed.km, 3), "total": fsc["total"], "parts": fsc["parts"],
            "cues": len(fcues), "notes": len(fnotes),
            "detail": fsc["detail"],
        },
    }
    p = os.path.join(OUT, "parity_expected.json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(exp, f, ensure_ascii=False)
    print("   基準寫出：%s" % p)
    print("\n接著跑：node tools_parity_check.js")


if __name__ == "__main__":
    main()

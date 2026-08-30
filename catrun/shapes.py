# -*- coding: utf-8 -*-
"""圖形庫。

座標系：x 向右 0~1、y 向上 0~1（正規化到單位方框，落地時再縮放旋轉平移）。
每個圖形是一條**封閉**折線——規格書 2-1 要求封閉環狀線，起終點相同。

`spurs` 標註「這幾段是為了特定筆畫的短距離折返」（規格書 2-2 明文允許），例如貓耳。
目前僅供閱讀與挑圖形時參考——評分是整條路線一起算折返比例（`Route.retrace_ratio`），
5% 以內不扣分，貓耳那種短折返落在容許範圍內。
"""

import math


def _closed(pts):
    return pts if pts[0] == pts[-1] else pts + [pts[0]]


# 依使用者手繪的蹲坐貓側面稿數位化：左側頭部、背脊往右下、右方伸出尾巴、蹲坐後腿。
_CAT_SIT = [
    (0.020, 0.560),   # 鼻尖
    (0.030, 0.680),   # 嘴／臉前緣
    (0.095, 0.815),   # 額頭
    (0.155, 0.855),   # 左耳根
    (0.180, 0.985),   # 左耳尖
    (0.245, 0.840),   # 兩耳之間的凹
    (0.300, 0.895),   # 右耳根
    (0.335, 1.000),   # 右耳尖
    (0.400, 0.840),   # 後腦
    (0.445, 0.740),   # 頸
    (0.585, 0.660),   # 背
    (0.720, 0.545),   # 臀部上緣
    (0.745, 0.440),   # 尾巴根
    (0.900, 0.400),   # 尾巴
    (1.000, 0.440),   # 尾尖
    (0.805, 0.360),   # 尾巴下緣（回程）
    (0.762, 0.225),   # 後腿
    (0.720, 0.045),   # 後腳著地
    (0.300, 0.020),   # 地面
    (0.140, 0.040),   # 前腳掌
    (0.100, 0.280),   # 前腿
    (0.050, 0.450),   # 前胸
]

# 貓臉（正面）：圓潤外廓＋兩隻三角耳，適合同心圓／對稱街廓的區域
_CAT_FACE = [
    (0.500, 0.855),   # 兩耳之間的凹
    (0.600, 0.880),   # 右耳內側
    (0.760, 1.000),   # 右耳尖
    (0.790, 0.790),   # 右耳外側
    (0.930, 0.640),   # 右臉頰
    (0.965, 0.440),
    (0.870, 0.230),
    (0.690, 0.075),
    (0.500, 0.030),   # 下巴
    (0.310, 0.075),
    (0.130, 0.230),
    (0.035, 0.440),
    (0.070, 0.640),   # 左臉頰
    (0.210, 0.790),   # 左耳外側
    (0.240, 1.000),   # 左耳尖
    (0.400, 0.880),   # 左耳內側
]


def _paw_outline():
    """肉球外廓：一筆封閉。下方主掌墊一段大弧，上方四顆趾墊接成連續花瓣。

    原本用手列點會讓輪廓穿過掌墊中心、畫出兩條重疊線，實跑就變成折返。
    改成用參數式產生，保證是簡單封閉曲線。
    """
    pts = []
    # 主掌墊：從左上沿著下緣掃到右上
    for i in range(29):
        a = math.pi * (1.0 - i / 28.0)          # 180° → 0°
        pts.append((0.5 + 0.36 * math.cos(a), 0.33 - 0.27 * math.sin(a)))
    # 四顆趾墊：由右而左，各畫半圈
    toes = [(0.845, 0.660, 0.115), (0.635, 0.815, 0.125),
            (0.375, 0.820, 0.125), (0.165, 0.655, 0.115)]
    for cx, cy, r in toes:
        for i in range(15):
            a = math.pi * (i / 14.0)      # 0°(右) → 90°(頂) → 180°(左)
            pts.append((cx + r * math.cos(a) * 1.15, cy + r * math.sin(a) * 1.35))
    return pts

# 肉球：主掌墊＋四顆趾墊，全部圓弧，適合綠園道多、彎道好取的區域
_CAT_PAW = _paw_outline()

# 蜷睡貓：一個大圓弧的身體＋尾巴繞回來，流線型
_CAT_CURL = [
    (0.180, 0.520), (0.150, 0.700), (0.240, 0.850), (0.410, 0.930),
    (0.430, 1.000), (0.520, 0.930), (0.600, 1.000), (0.620, 0.910),
    (0.780, 0.830), (0.900, 0.660), (0.940, 0.460), (0.870, 0.260),
    (0.700, 0.110), (0.480, 0.050), (0.280, 0.100), (0.130, 0.230),
    (0.070, 0.400), (0.140, 0.330), (0.270, 0.260), (0.430, 0.250),
    (0.560, 0.320), (0.610, 0.450), (0.560, 0.560), (0.440, 0.590),
    (0.330, 0.560), (0.300, 0.470),
]

SHAPES = {
    "cat_sit": {
        "name": "蹲坐貓咪（側面）",
        "pts": _closed(_CAT_SIT),
        "aspect": 1.45,          # 寬 : 高
        "spurs": [(3, 5), (6, 8)],   # 左耳、右耳：允許短距離折返的筆畫
        "note": "頭在左、背脊往右下、右方伸出長尾。輪廓最像貓，但需要兩支南北向短巷做耳朵。",
        "fits": ("wuri", "xitun", "nantun", "east", "south", "west"),
    },
    "cat_face": {
        "name": "貓臉（正面）",
        "pts": _closed(_CAT_FACE),
        "aspect": 1.05,
        "spurs": [],
        "note": "圓潤外廓加兩隻三角耳，沒有折返，最容易做成漂亮的封閉環。",
        "fits": ("nantun", "east", "west", "south"),
    },
    "cat_paw": {
        "name": "貓咪肉球",
        "pts": _closed(_CAT_PAW),
        "aspect": 1.00,
        "spurs": [],
        "note": "掌墊加四顆趾墊，全弧線，需要彎道與圓環才畫得順。",
        "fits": ("west", "south", "nantun"),
    },
    "cat_curl": {
        "name": "蜷睡貓咪",
        "pts": _closed(_CAT_CURL),
        "aspect": 1.30,
        "spurs": [],
        "note": "一整條流線大弧，尾巴繞回身體，適合河濱與綠園道長弧線。",
        "fits": ("wuri", "west", "south"),
    },
}


# ── 使用者描圖進來的圖形 ──
# 放在 data/shapes/*.json，開機時併進 SHAPES。這樣「從手繪稿描一個新圖形」
# 不必改程式碼，也不必在執行時再讀一次影像。
def _load_user_shapes():
    import json
    import os
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "shapes")
    if not os.path.isdir(d):
        return
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                x = json.load(f)
            SHAPES[x["key"]] = {
                "name": x["name"], "pts": _closed([tuple(p) for p in x["pts"]]),
                "aspect": x.get("aspect", 1.0), "spurs": x.get("spurs", []),
                "note": x.get("note", ""), "fits": tuple(x.get("fits", ()))
                or tuple(SHAPES["cat_sit"]["fits"]),
            }
        except Exception as e:      # noqa - 壞掉的檔案不該讓整個系統起不來
            print("圖形檔讀取失敗 %s：%r" % (fn, e))


_load_user_shapes()


def get(key):
    if key not in SHAPES:
        raise KeyError("沒有這個圖形：%s（可用：%s）" % (key, "、".join(SHAPES)))
    return SHAPES[key]


def normalised(key):
    """回傳等比置中、長邊為 1 的點列——落地前先把圖形本身的比例固定下來。"""
    s = get(key)
    pts = s["pts"]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    k = max(w, h) or 1.0
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return [((x - cx) / k, (y - cy) / k) for x, y in pts]

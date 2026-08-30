# -*- coding: utf-8 -*-
"""從 OSM 取台中 29 個行政區的真實邊界，產生 config 用的區域定義。

兩個 bbox 是刻意分開的：
  bbox       行政區真實範圍。圖形的「中心」只在這裡面搜尋，路線才會落在該區。
  fetch_bbox 路網下載範圍，往外擴。中區只有 1×1 公里，比 5 公里路線需要的
             圖形還小，不擴的話路線一定溢出去卻沒有路網可走。

寬高比（aspect）不用猜：取該區自己的長寬比 ±0.4。一個圖形若與所在行政區的
形狀比例相近，落地時比較不會被邊界切掉——這比憑印象填數字有依據。
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from catrun.osmnet import overpass                       # noqa: E402

# 各區特性與適配圖形風格。前六個沿用規格書 2-4 的原文，其餘依實地特徵補寫。
STYLE = {
    "烏日區": ("大型長條型、翅膀型或飛鳥型圖形", "高鐵特區、三川匯流河岸；路幅開闊、河濱堤防直線長"),
    "南區": ("圓潤、流線型、自然生態類圖形", "興大周邊、綠園道豐富、文教綠帶多"),
    "南屯區": ("同心結構或對稱圖形", "黎明新村同心圓／弧形街廓、單元重劃區街道方正"),
    "東區": ("復古、同心圓或多邊形圖形", "舊市區、帝國製糖廠、台糖湖濱、環狀鐵道綠廊"),
    "西屯區": ("現代幾何、科技感、大輪廓圖形", "七期／逢甲／水湳；棋盤式道路、中央公園、秋紅谷"),
    "西區": ("圓潤、流線型、自然生態類圖形", "草悟道、國美館、綠園道與柳川"),
    "中區": ("小尺度、緊湊的圖形", "舊城核心、宮原眼科與第二市場；街廓極密但全區僅約 1 公里見方"),
    "北區": ("流線型、帶綠帶的圖形", "一中商圈、科博館、經國綠園道；綠帶穿越市區"),
    "北屯區": ("大輪廓、東西向延伸的圖形", "西半為重劃區棋盤、東半接大坑淺山；捷運綠線貫穿"),
    "太平區": ("大輪廓圖形（建議留在西半市區）", "西半為平原市區棋盤，東半頭汴坑為淺山，爬升明顯"),
    "大里區": ("方正幾何或對稱圖形", "密集住宅棋盤、大里溪與草湖溪堤防；平坦"),
    "霧峰區": ("流線型圖形（避開東側丘陵）", "光復新村、亞洲大學；西半平原、東半九九峰丘陵"),
    "豐原區": ("放射狀或多邊形圖形", "葫蘆墩圳與廟東商圈；舊市區道路呈放射狀"),
    "后里區": ("開闊的大輪廓圖形", "花博園區、麗寶樂園、后豐鐵馬道；台地平坦"),
    "石岡區": ("小尺度、沿綠廊的線形圖形", "石岡壩與東豐自行車綠廊；面積小、綠道品質高"),
    "東勢區": ("沿河的長條型圖形", "大甲溪畔、東豐綠廊起點；市區外多為丘陵果園"),
    "和平區": ("不建議規劃圖形路線", "谷關、梨山；全區山地，坡度與里程都遠超規格書上限"),
    "新社區": ("流線型圖形（坡度大）", "新社台地、白冷圳；道路沿等高線蜿蜒，爬升可觀"),
    "潭子區": ("方正幾何圖形", "摘星山莊、潭子加工區；街廓方正"),
    "大雅區": ("現代幾何、直角圖形", "中科周邊與小麥田；重劃區道路極方正"),
    "神岡區": ("對稱或幾何圖形", "社口林宅、圳道與農地；平坦方正"),
    "大肚區": ("長條型圖形（西側平原）", "大肚山台地與追分車站；山側坡度大，平原在西"),
    "沙鹿區": ("幾何或對稱圖形", "靜宜與弘光校區、竹林南路商圈；台地邊緣有坡"),
    "龍井區": ("長條型或翅膀型圖形", "東海大學、麗水漁港、龍井堤防；濱海平坦"),
    "梧棲區": ("方正幾何、大輪廓圖形", "台中港與三井 Outlet；港區道路寬闊方正、極平坦"),
    "清水區": ("長條型圖形", "高美濕地、鰲峰山；西半濱海平原、東半接大肚台地"),
    "大甲區": ("開闊的大輪廓圖形", "鎮瀾宮、大甲溪出海口；平原開闊"),
    "外埔區": ("流線型圖形", "水流東桐花步道與農路；台地緩坡"),
    "大安區": ("長條型或流線型圖形", "大安海水浴場與濱海農地；平坦但路網稀疏"),
}
# 不建議規劃的區（仍會抓路網，但預設不放進公開站的選單）
NOT_RECOMMENDED = {"和平區"}
KEY = {
    "烏日區": "wuri", "南區": "south", "南屯區": "nantun", "東區": "east",
    "西屯區": "xitun", "西區": "west", "中區": "central", "北區": "north",
    "北屯區": "beitun", "太平區": "taiping", "大里區": "dali", "霧峰區": "wufeng",
    "豐原區": "fengyuan", "后里區": "houli", "石岡區": "shigang", "東勢區": "dongshi",
    "和平區": "heping", "新社區": "xinshe", "潭子區": "tanzi", "大雅區": "daya",
    "神岡區": "shengang", "大肚區": "dadu", "沙鹿區": "shalu", "龍井區": "longjing",
    "梧棲區": "wuqi", "清水區": "qingshui", "大甲區": "dajia", "外埔區": "waipu",
    "大安區": "daan",
}
MIN_SPAN_KM = 4.5      # 路網至少要涵蓋這麼大，否則放不下 5 公里的圖形
MARGIN_KM = 0.8        # 再往外留一點，路線壓到邊界時才有路可走


def fetch_bounds():
    q = ('[out:json][timeout:180];'
         'area["name"="臺中市"]["admin_level"="4"]->.tc;'
         'relation(area.tc)["admin_level"="7"]["boundary"="administrative"];'
         'out tags bb;')
    out = {}
    for el in overpass(q).get("elements", []):
        t, b = el.get("tags", {}), el.get("bounds")
        if not b or not t.get("name"):
            continue
        out[t["name"]] = (b["minlat"], b["minlon"], b["maxlat"], b["maxlon"])
    return out


def expand(bb, min_km, margin_km):
    s, w, n, e = bb
    lat_km, lon_km = 111.3, 111.3 * 0.913
    cs, cw = (s + n) / 2, (w + e) / 2
    h = max((n - s) * lat_km, min_km) + margin_km * 2
    wd = max((e - w) * lon_km, min_km) + margin_km * 2
    return (round(cs - h / 2 / lat_km, 4), round(cw - wd / 2 / lon_km, 4),
            round(cs + h / 2 / lat_km, 4), round(cw + wd / 2 / lon_km, 4))


def main():
    bounds = fetch_bounds()
    lines = []
    for name in sorted(bounds, key=lambda x: list(KEY).index(x) if x in KEY else 99):
        if name not in KEY:
            print("略過未知行政區：%s" % name)
            continue
        bb = bounds[name]
        s, w, n, e = bb
        ar = ((e - w) * 0.913) / max(n - s, 1e-9)
        lo = round(max(0.70, ar - 0.40), 2)
        hi = round(min(2.60, ar + 0.40), 2)
        style, traits = STYLE[name]
        lines.append({
            "key": KEY[name], "name": name, "osm": name,
            "bbox": [round(x, 4) for x in bb],
            "fetch_bbox": list(expand(bb, MIN_SPAN_KM, MARGIN_KM)),
            "style": style, "traits": traits, "aspect": [lo, hi],
            "recommended": name not in NOT_RECOMMENDED,
            "span_km": [round((e - w) * 111.3 * 0.913, 1), round((n - s) * 111.3, 1)],
        })
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "districts.json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(lines, f, ensure_ascii=False, indent=1)
    print("寫出 %d 個行政區定義 → %s" % (len(lines), p))
    for d in lines:
        print("  %-9s %-4s %5.1f×%-5.1f km  抓取範圍 %5.1f×%-5.1f km  寬高比 %.2f–%.2f%s"
              % (d["key"], d["name"], d["span_km"][0], d["span_km"][1],
                 (d["fetch_bbox"][3] - d["fetch_bbox"][1]) * 111.3 * 0.913,
                 (d["fetch_bbox"][2] - d["fetch_bbox"][0]) * 111.3,
                 d["aspect"][0], d["aspect"][1],
                 "" if d["recommended"] else "  ← 不建議"))


if __name__ == "__main__":
    main()

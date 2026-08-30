# -*- coding: utf-8 -*-
"""把圖形「落地」到真實路網。

做法分三步，每一步都對應規格書 2-1 的要求：
  1. 佈點：把正規化圖形依（中心、尺寸、旋轉角）投影成一串經緯度控制點。
  2. 吸附：每個控制點抓最近的路網節點——線條轉折一定落在真實路口上。
  3. 串接：相鄰控制點之間走最短路徑（成本已把「危險的路變長」算進去），
     串起來就是一條完全跑得到的封閉環線。

搜尋策略是先粗掃（中心格點 × 旋轉角）再局部爬山，最後用二分法把里程調進
所選級距。全域最佳解不保證，但規格書要的是「可用且高分」，不是數學最優。
"""
import math

import networkx as nx
from shapely.geometry import LineString, Point

from .geo import enu, enu_inv, hav, path_len, resample

CTRL = 46          # 控制點數：太少畫不出耳朵，太多會被巷弄雜訊帶偏
MAX_SNAP_M = 260.0  # 控制點離最近道路超過這個距離，代表那裡根本沒路


def place(base, center, size_m, rot_deg):
    """正規化圖形 →（中心、尺寸、旋轉）→ 經緯度點列。"""
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for x, y in base:
        X, Y = x * size_m, y * size_m
        out.append(enu_inv(center, (X * ca - Y * sa, X * sa + Y * ca)))
    return out


def ctrl_points(target, max_gap=None):
    """控制點＝圖形本身的頂點 ＋ 中間補點。

    不能直接等距重取樣：耳尖那種只有一兩公分的頂點會被取樣點跨過去，
    整隻貓就變成沒有耳朵的橢圓。頂點一定要保留，長邊再補點。
    """
    if max_gap is None:
        max_gap = max(60.0, path_len(target) / 40.0)
    out = []
    for a, b in zip(target, target[1:]):
        out.append(a)
        d = hav(a, b)
        n = int(d // max_gap)
        for i in range(1, n + 1):
            t = i / (n + 1.0)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    out.append(target[-1])
    return out


def _astar(G, coord, a, b):
    def h(u, v):
        return hav(coord[u], coord[v])
    return nx.astar_path(G, a, b, heuristic=h, weight="cost")


def snap_and_route(net, target):
    """控制點吸附＋最短路徑串接。

    回傳 (節點序列, 吸附誤差, 繞路倍率)。繞路倍率＝相鄰控制點之間實際路徑長
    ÷ 直線距離；某一段暴衝代表那裡根本沒有連通的路，圖形會被拉出一個大凸包，
    所以要當成品質指標帶回去評分，不能只看平均吸附誤差。
    """
    ctrl = ctrl_points(target)
    nodes, snap_err = [], []
    for p in ctrl:
        nid = net.nearest(p)
        if nid is None:
            return None, None, None
        d = hav(p, net.xy(nid))
        snap_err.append(d)
        if not nodes or nodes[-1] != nid:
            nodes.append(nid)
    if len(nodes) < 4:
        return None, None, None
    if nodes[0] != nodes[-1]:
        nodes.append(nodes[0])          # 封閉環線
    seq, detours = [], []
    for a, b in zip(nodes, nodes[1:]):
        if a == b:
            continue
        try:
            seg = _astar(net.G, net.coord, a, b)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None, None, None
        straight = hav(net.xy(a), net.xy(b))
        actual = sum(net.G[u][v]["length"] for u, v in zip(seg, seg[1:]))
        if straight > 25:
            detours.append(actual / straight)
        seq.extend(seg[:-1])
    seq.append(nodes[-1])
    return _collapse(seq), snap_err, detours


def _collapse(seq):
    """砍掉 A→B→A 這種原地折返的贅點（不影響刻意的耳朵折返，那是長段）。"""
    out = []
    for n in seq:
        if len(out) >= 2 and out[-2] == n:
            out.pop()
        else:
            out.append(n)
    return out


class Route:
    """一條規劃好的路線：節點序列＋座標＋沿線邊的屬性。"""

    def __init__(self, net, seq, target, place_info):
        self.net, self.seq, self.target = net, seq, target
        self.place = place_info
        self.pts = [net.xy(n) for n in seq]
        self.edges = []
        for a, b in zip(seq, seq[1:]):
            d = net.G[a][b]
            self.edges.append(d)
        self.length_m = sum(e["length"] for e in self.edges)

    @property
    def km(self):
        return self.length_m / 1000.0

    def retrace_ratio(self):
        """重複走過的路段佔比——規格書 2-1 不喜歡折返，這個數字要小。"""
        seen, dup, tot = set(), 0.0, 0.0
        for (a, b), e in zip(zip(self.seq, self.seq[1:]), self.edges):
            k = (min(a, b), max(a, b))
            tot += e["length"]
            if k in seen:
                dup += e["length"]
            else:
                seen.add(k)
        return (dup / tot) if tot else 0.0

    def safety_mean(self):
        tot = sum(e["length"] for e in self.edges) or 1.0
        return sum(e["safety"] * e["length"] for e in self.edges) / tot


def _dists(A, B):
    return sorted(B.distance(Point(c)) for c in A.coords)


def fidelity(route, target, size_m):
    """路線與目標圖形的貼合度 0~1。

    只看平均會被騙：整體貼得很近、但某一段暴衝兩百公尺，平均只動幾公尺，
    肉眼卻已經看不出是貓了。所以平均與 P90 各佔一半，局部走鐘會被抓出來。
    """
    o = route.pts[0]
    A = LineString([enu(o, p) for p in resample(route.pts, 240)])
    B = LineString([enu(o, p) for p in resample(target, 240)])
    da, db = _dists(A, B), _dists(B, A)
    mean = (sum(da) / len(da) + sum(db) / len(db)) / 2.0
    p90 = (da[int(len(da) * 0.9)] + db[int(len(db) * 0.9)]) / 2.0
    err = 0.5 * mean + 0.5 * p90
    # 誤差達圖形尺寸的 12% 就完全不像了；線性折算成 0~1
    return max(0.0, 1.0 - err / (0.12 * size_m)), err


def _perimeter_unit(base):
    return sum(math.dist(base[i], base[i + 1]) for i in range(len(base) - 1))


def build(net, base, center, size_m, rot):
    target = place(base, center, size_m, rot)
    seq, snap_err, detours = snap_and_route(net, target)
    if not seq:
        return None
    if snap_err:
        if (sum(snap_err) / len(snap_err)) > MAX_SNAP_M or max(snap_err) > MAX_SNAP_M * 1.6:
            return None                 # 這裡沒有路，硬吸只會把圖形拉爛
    r = Route(net, seq, target, {"center": center, "size_m": size_m, "rot": rot})
    r.snap_err = snap_err or [0.0]
    r.detours = detours or [1.0]
    return r


def quick_score(net, base, center, size_m, rot):
    """粗掃用的便宜分數：貼合度為主，安全度、折返、吸附與繞路品質為輔。"""
    r = build(net, base, center, size_m, rot)
    if r is None:
        return None, None
    f, _ = fidelity(r, r.target, size_m)
    snap = 1.0 - min(1.0, (sum(r.snap_err) / len(r.snap_err)) / 160.0)
    worst = max(r.detours)
    det = 1.0 - min(1.0, (worst - 1.3) / 1.7)      # 繞路 1.3 倍以內不扣分
    # 轉太多度就不像「坐著的貓」了，超過 25 度開始扣分（視覺可讀性）
    tilt = 1.0 - min(1.0, max(0.0, abs(rot) - 25.0) / 35.0)
    s = (0.46 * f + 0.16 * r.safety_mean() + 0.08 * (1.0 - min(1.0, r.retrace_ratio() * 2.5))
         + 0.12 * snap + 0.10 * det + 0.08 * tilt)
    return s, r


def search(net, base, target_km, centers=None,
           rots=(-24, -16, -8, 0, 8, 16, 24),
           log=None):
    """粗掃 → 局部爬山 → 里程二分。回傳最佳 Route。"""
    per = _perimeter_unit(base)
    size0 = target_km * 1000.0 / per / 1.12      # 1.12：吸附繞路造成的膨脹
    s, w, n, e = net.cfg["bbox"]
    if centers is None:
        centers = []
        for i in range(1, 6):
            for j in range(1, 6):
                centers.append((s + (n - s) * i / 6.0, w + (e - w) * j / 6.0))
    best = (None, None)
    for c in centers:
        # 中心點附近沒路就不用試了
        nid = net.nearest(c)
        if nid is None or hav(c, net.xy(nid)) > 600:
            continue
        for rot in rots:
            sc, r = quick_score(net, base, c, size0, rot)
            if sc is not None and (best[0] is None or sc > best[0]):
                best = (sc, r)
                if log:
                    log("粗掃 %.4f  中心 %.5f,%.5f  轉 %+d°  %.2f km"
                        % (sc, c[0], c[1], rot, r.km))
    if best[1] is None:
        return None
    cur = best[1]
    curs = best[0]
    # 局部爬山：中心平移 ±、旋轉 ±、尺寸 ±
    step_m, step_r, step_s = 260.0, 6.0, 0.10
    for _ in range(9):
        improved = False
        c = cur.place["center"]
        for dx, dy in ((step_m, 0), (-step_m, 0), (0, step_m), (0, -step_m),
                       (step_m, step_m), (-step_m, -step_m)):
            cc = enu_inv(c, (dx, dy))
            sc, r = quick_score(net, base, cc, cur.place["size_m"], cur.place["rot"])
            if sc is not None and sc > curs:
                curs, cur, improved = sc, r, True
        for dr in (step_r, -step_r):
            sc, r = quick_score(net, base, cur.place["center"],
                                cur.place["size_m"], cur.place["rot"] + dr)
            if sc is not None and sc > curs:
                curs, cur, improved = sc, r, True
        for ds in (1 + step_s, 1 - step_s):
            sc, r = quick_score(net, base, cur.place["center"],
                                cur.place["size_m"] * ds, cur.place["rot"])
            if sc is not None and sc > curs:
                curs, cur, improved = sc, r, True
        if not improved:
            step_m *= 0.55
            step_r *= 0.6
            step_s *= 0.6
            if step_m < 40:
                break
        if log:
            log("爬山 %.4f  %.2f km" % (curs, cur.km))
    return cur


def fit_band(net, base, route, lo_km, hi_km, log=None):
    """把里程調進規格書的級距。

    重點：級距是**約束**不是目標。先用二分找出對應級距上下限的尺寸，再在區間內
    取幾個尺寸各自評分，挑分數最高的——不是硬湊到區間中點。市區密、郊區疏，
    同一個級距裡小一號往往反而畫得比較像。
    """
    def size_for(km):
        lo, hi = route.place["size_m"] * 0.30, route.place["size_m"] * 3.0
        best, bestd = route.place["size_m"], 1e9
        for _ in range(16):
            m = (lo + hi) / 2.0
            r = build(net, base, route.place["center"], m, route.place["rot"])
            if r is None:
                lo = m
                continue
            if abs(r.km - km) < bestd:
                best, bestd = m, abs(r.km - km)
            if r.km < km:
                lo = m
            else:
                hi = m
        return best

    s_lo, s_hi = size_for(lo_km + 0.15), size_for(hi_km - 0.15)
    if s_hi < s_lo:
        s_lo, s_hi = s_hi, s_lo
    cands = [s_lo + (s_hi - s_lo) * i / 4.0 for i in range(5)]
    best, bests = route, -1.0
    for m in cands:
        sc, r = quick_score(net, base, route.place["center"], m, route.place["rot"])
        if r is None:
            continue
        if not (lo_km <= r.km <= hi_km):
            continue
        if log:
            log("級距內候選 尺寸 %.0f m → %.2f km，分數 %.4f" % (m, r.km, sc))
        if sc > bests:
            best, bests = r, sc
    if bests < 0 and log:
        log("警告：找不到落在級距內的尺寸，沿用搜尋結果 %.2f km" % route.km)
    return best

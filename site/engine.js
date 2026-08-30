/* 貓行台中 — 規劃引擎（Web Worker）
 *
 * 這支是 Python 版 catrun 的 JavaScript 移植：描圖、路網、圖形落地、五構面評分，
 * 全部在使用者自己的瀏覽器裡跑。這樣做的三個理由：
 *   1. 上傳的圖不會離開使用者的裝置
 *   2. 公開站維持純靜態，零伺服器成本，不會被大量請求灌爆
 *   3. 實測比伺服器端 Python 還快（typed array 的緊迴圈是 JIT 最擅長的）
 *
 * ⚠ 這裡的演算法必須與 catrun/ 底下的 Python 版一致。改任何一邊都要跑
 *   tools_parity_check.py 對照，否則公開站算出來的分數會跟本機工具對不起來。
 */
"use strict";

var NET = null;          // 目前載入的路網
var BASE = "";           // 站台根路徑

/* ══════════════════ 幾何 ══════════════════ */
var R_EARTH = 6371008.8, DEG = Math.PI / 180;

function hav(a, b) {
  var p1 = a[0] * DEG, p2 = b[0] * DEG;
  var dp = p2 - p1, dl = (b[1] - a[1]) * DEG;
  var h = Math.sin(dp / 2) * Math.sin(dp / 2) +
    Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
  return 2 * R_EARTH * Math.asin(Math.sqrt(h));
}
function segDist(px, py, ax, ay, bx, by) {
  var dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
  var t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L2));
  var qx = ax + t * dx, qy = ay + t * dy;
  return Math.hypot(px - qx, py - qy);
}
function polyDist(px, py, poly) {   // 點到折線的最短距離（公尺平面）
  var best = Infinity;
  for (var i = 0; i < poly.length - 1; i++) {
    var d = segDist(px, py, poly[i][0], poly[i][1], poly[i + 1][0], poly[i + 1][1]);
    if (d < best) best = d;
  }
  return best;
}
function resample(pts, n) {
  if (pts.length < 2) { var o = []; for (var k = 0; k < n; k++) o.push(pts[0]); return o; }
  var segs = [], total = 0, i;
  for (i = 0; i < pts.length - 1; i++) {
    var d = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
    segs.push(d); total += d;
  }
  if (total <= 0) { var o2 = []; for (i = 0; i < n; i++) o2.push(pts[0]); return o2; }
  var out = [], step = total / (n - 1), acc = 0, j = 0;
  for (var q = 0; q < n; q++) {
    var target = step * q;
    while (j < segs.length && acc + segs[j] < target) { acc += segs[j]; j++; }
    if (j >= segs.length) { out.push(pts[pts.length - 1]); continue; }
    var t = segs[j] === 0 ? 0 : (target - acc) / segs[j];
    var a = pts[j], b = pts[j + 1];
    out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
  }
  return out;
}
function bearing(a, b) {
  var y = Math.sin((b[1] - a[1]) * DEG) * Math.cos(b[0] * DEG);
  var x = Math.cos(a[0] * DEG) * Math.sin(b[0] * DEG) -
    Math.sin(a[0] * DEG) * Math.cos(b[0] * DEG) * Math.cos((b[1] - a[1]) * DEG);
  return (Math.atan2(y, x) / DEG + 360) % 360;
}
function turnOf(prev, next) {
  var d = (next - prev + 540) % 360 - 180;
  if (Math.abs(d) < 25) return "直行";
  if (Math.abs(d) > 150) return "迴轉";
  return d > 0 ? "右轉" : "左轉";
}

/* ══════════════════ 描圖：手繪影像 → 封閉輪廓 ══════════════════
 * 與 catrun/trace.py 同一套流程：二值化 → 膨脹補缺口 → 從邊框灌水找外部
 * → 侵蝕回原粗細 → 取最大連通塊 → 邊界追蹤 → Douglas-Peucker。
 * 先膨脹再侵蝕是關鍵：手繪線常常沒真正閉合，不補起來灌水會從缺口漏光。 */
function binarize(gray, w, h) {
  var n = w * h, i;
  var sorted = Float32Array.from(gray).sort();
  var med = sorted[n >> 1], mn = sorted[0];
  var th = Math.max(60, Math.min(200, (med + mn) / 2));
  var ink = new Uint8Array(n);
  for (i = 0; i < n; i++) ink[i] = gray[i] < th ? 1 : 0;
  return ink;
}
function dilate(src, w, h, iters) {
  var a = src, i, x, y;
  for (var it = 0; it < iters; it++) {
    var b = new Uint8Array(w * h);
    for (y = 0; y < h; y++) {
      for (x = 0; x < w; x++) {
        var p = y * w + x;
        if (a[p]) { b[p] = 1; continue; }
        var on = 0;
        for (var dy = -1; dy <= 1 && !on; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            var nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            if (a[ny * w + nx]) { on = 1; break; }
          }
        }
        b[p] = on;
      }
    }
    a = b;
  }
  return a;
}
function erode(src, w, h, iters) {
  /* 影像邊界視為實心（等同 scipy 的 border_value=1），否則貼邊的圖形會被削掉 */
  var a = src, x, y;
  for (var it = 0; it < iters; it++) {
    var b = new Uint8Array(w * h);
    for (y = 0; y < h; y++) {
      for (x = 0; x < w; x++) {
        var p = y * w + x;
        if (!a[p]) { b[p] = 0; continue; }
        var all = 1;
        for (var dy = -1; dy <= 1 && all; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            var nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;   // 界外算實心
            if (!a[ny * w + nx]) { all = 0; break; }
          }
        }
        b[p] = all;
      }
    }
    a = b;
  }
  return a;
}
function floodOutside(free, w, h) {
  /* 從影像邊框灌水，回傳「碰得到邊框的空白」= 外部 */
  var out = new Uint8Array(w * h);
  var st = new Int32Array(w * h), sp = 0, i, x, y;
  function push(p) { if (free[p] && !out[p]) { out[p] = 1; st[sp++] = p; } }
  for (x = 0; x < w; x++) { push(x); push((h - 1) * w + x); }
  for (y = 0; y < h; y++) { push(y * w); push(y * w + w - 1); }
  while (sp > 0) {
    var p = st[--sp], px = p % w, py = (p / w) | 0;
    if (px > 0) push(p - 1);
    if (px < w - 1) push(p + 1);
    if (py > 0) push(p - w);
    if (py < h - 1) push(p + w);
  }
  return out;
}
function largestBlob(mask, w, h) {
  var lab = new Int32Array(w * h).fill(-1);
  var st = new Int32Array(w * h);
  var best = -1, bestSize = 0, id = 0;
  for (var s = 0; s < w * h; s++) {
    if (!mask[s] || lab[s] >= 0) continue;
    var sp = 0, size = 0;
    st[sp++] = s; lab[s] = id;
    while (sp > 0) {
      var p = st[--sp]; size++;
      var px = p % w, py = (p / w) | 0;
      var nb = [px > 0 ? p - 1 : -1, px < w - 1 ? p + 1 : -1,
      py > 0 ? p - w : -1, py < h - 1 ? p + w : -1];
      for (var k = 0; k < 4; k++) {
        var q = nb[k];
        if (q >= 0 && mask[q] && lab[q] < 0) { lab[q] = id; st[sp++] = q; }
      }
    }
    if (size > bestSize) { bestSize = size; best = id; }
    id++;
  }
  var out = new Uint8Array(w * h);
  if (best < 0) return null;
  for (var i = 0; i < w * h; i++) out[i] = lab[i] === best ? 1 : 0;
  return out;
}
function traceBoundary(mask, w, h) {
  /* Moore 鄰域邊界追蹤：回傳順時針的外輪廓像素序列 */
  var start = -1;
  for (var p = 0; p < w * h; p++) if (mask[p]) { start = p; break; }
  if (start < 0) return null;
  var DX = [1, 1, 0, -1, -1, -1, 0, 1], DY = [0, 1, 1, 1, 0, -1, -1, -1];
  function at(x, y) { return (x < 0 || y < 0 || x >= w || y >= h) ? 0 : mask[y * w + x]; }
  var sx = start % w, sy = (start / w) | 0;
  var cx = sx, cy = sy, dir = 6, out = [[sx, sy]], guard = 0;
  do {
    var found = false;
    for (var i = 0; i < 8; i++) {
      var d = (dir + 6 + i) % 8;                 // 從「回頭方向的右邊」開始找
      var nx = cx + DX[d], ny = cy + DY[d];
      if (at(nx, ny)) { cx = nx; cy = ny; dir = d; out.push([cx, cy]); found = true; break; }
    }
    if (!found) break;
    if (++guard > 4 * w * h) break;
  } while (!(cx === sx && cy === sy));
  return out;
}
function rdp(pts, eps) {
  if (pts.length < 3) return pts.slice();
  var ax = pts[0][0], ay = pts[0][1];
  var bx = pts[pts.length - 1][0], by = pts[pts.length - 1][1];
  var dx = bx - ax, dy = by - ay, L = Math.hypot(dx, dy);
  var maxD = -1, idx = 0;
  for (var i = 1; i < pts.length - 1; i++) {
    var d = L < 1e-9 ? Math.hypot(pts[i][0] - ax, pts[i][1] - ay)
      : Math.abs(dx * (pts[i][1] - ay) - dy * (pts[i][0] - ax)) / L;
    if (d > maxD) { maxD = d; idx = i; }
  }
  if (maxD <= eps) return [pts[0], pts[pts.length - 1]];
  var a = rdp(pts.slice(0, idx + 1), eps), b = rdp(pts.slice(idx), eps);
  return a.slice(0, a.length - 1).concat(b);
}
function smoothRing(pts, k) {
  var n = pts.length, out = [];
  for (var i = 0; i < n; i++) {
    var sx = 0, sy = 0, c = 0;
    for (var j = -k; j <= k; j++) {
      var q = pts[((i + j) % n + n) % n];
      sx += q[0]; sy += q[1]; c++;
    }
    out.push([sx / c, sy / c]);
  }
  return out;
}
function traceImage(gray, w, h, closePx, targetPts) {
  var ink = binarize(gray, w, h);
  var inkCount = 0;
  for (var i = 0; i < ink.length; i++) inkCount += ink[i];
  if (inkCount < 50) throw new Error("圖上幾乎沒有線條，換一張線條清楚、背景乾淨的圖");
  var thick = closePx > 0 ? dilate(ink, w, h, closePx) : ink;
  var free = new Uint8Array(w * h);
  for (i = 0; i < free.length; i++) free[i] = thick[i] ? 0 : 1;
  var outside = floodOutside(free, w, h);
  var solid = new Uint8Array(w * h);
  for (i = 0; i < solid.length; i++) solid[i] = outside[i] ? 0 : 1;
  if (closePx > 0) solid = erode(solid, w, h, closePx);
  /* 侵蝕後可能重新開洞，再灌一次把洞補回來 */
  var free2 = new Uint8Array(w * h);
  for (i = 0; i < free2.length; i++) free2[i] = solid[i] ? 0 : 1;
  var out2 = floodOutside(free2, w, h);
  for (i = 0; i < solid.length; i++) solid[i] = out2[i] ? 0 : 1;
  var blob = largestBlob(solid, w, h);
  if (!blob) throw new Error("找不到封閉輪廓：線條可能斷太多，把「缺口補償」調大再試");
  var ring = traceBoundary(blob, w, h);
  if (!ring || ring.length < 20) throw new Error("輪廓太短，描不出形狀");
  var sm = smoothRing(ring, 3);
  /* 二分 eps 把點數壓到目標附近 */
  var lo = 0.05, hi = Math.max(w, h) / 6, res = sm;
  for (var it = 0; it < 28; it++) {
    var eps = (lo + hi) / 2;
    var r = rdp(sm, eps);
    if (r.length > targetPts) lo = eps; else hi = eps;
    res = r;
    if (Math.abs(r.length - targetPts) <= 2) break;
  }
  /* 影像列往下 → 圖形 y 往上；置中、長邊正規化為 1 */
  var xs = res.map(function (p) { return p[0]; });
  var ys = res.map(function (p) { return -p[1]; });
  var mnx = Math.min.apply(null, xs), mxx = Math.max.apply(null, xs);
  var mny = Math.min.apply(null, ys), mxy = Math.max.apply(null, ys);
  var k = Math.max(mxx - mnx, mxy - mny) || 1;
  var pts = res.map(function (p, i2) {
    return [(xs[i2] - (mnx + mxx) / 2) / k, (ys[i2] - (mny + mxy) / 2) / k];
  });
  if (pts[0][0] !== pts[pts.length - 1][0] || pts[0][1] !== pts[pts.length - 1][1])
    pts.push(pts[0]);
  return {
    pts: pts,
    info: {
      "影像尺寸": w + "×" + h, "筆畫像素": inkCount,
      "輪廓原始點數": ring.length, "簡化後點數": pts.length,
      "缺口補償(px)": closePx,
      "寬高比": Math.round((mxx - mnx) / Math.max(mxy - mny, 1e-9) * 1000) / 1000
    }
  };
}

/* ══════════════════ 路網 ══════════════════ */
function buildNet(bin, side) {
  var dv = new DataView(bin);
  if (String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3)) !== "CRG2")
    throw new Error("路網檔格式不符");
  var nN = dv.getUint32(4, true), nE = dv.getUint32(8, true);
  var lat0 = dv.getFloat64(12, true), lon0 = dv.getFloat64(20, true);
  var off = 28, i;
  var LA = new Float64Array(nN), LO = new Float64Array(nN);
  var sumLat = 0;
  for (i = 0; i < nN; i++) {
    var la = dv.getUint32(off, true), lo = dv.getUint32(off + 4, true);
    LA[i] = lat0 + la / 1e6; LO[i] = lon0 + lo / 1e6;
    sumLat += LA[i];
    off += 8;
  }
  /* 公尺平面必須與 Python 端 KD-tree 用的完全相同（平均緯度 × 111319.49），
     否則「最近節點」會在平手處翻面，整條路線就跟著不一樣——對照測試就是這樣抓到的。 */
  var ky = 111319.49, kx = Math.cos(sumLat / nN * DEG) * ky;
  var X = new Float32Array(nN), Y = new Float32Array(nN);
  for (i = 0; i < nN; i++) { X[i] = LO[i] * kx; Y[i] = LA[i] * ky; }
  var EA = new Int32Array(nE), EB = new Int32Array(nE);
  var EL = new Float32Array(nE), EC = new Float32Array(nE);
  var ES = new Float32Array(nE), EN = new Uint16Array(nE), EF = new Uint16Array(nE);
  for (var e = 0; e < nE; e++) {
    EA[e] = dv.getUint32(off, true); EB[e] = dv.getUint32(off + 4, true);
    EL[e] = dv.getUint16(off + 8, true) / 10;
    ES[e] = dv.getUint16(off + 10, true) / 1000;
    EN[e] = dv.getUint16(off + 12, true);
    EF[e] = dv.getUint16(off + 14, true);
    EC[e] = EL[e] * (1 + 1.6 * (1 - ES[e]));   // 危險的路「感覺比較長」
    off += 16;
  }
  var deg = new Int32Array(nN + 1);
  for (i = 0; i < nE; i++) { deg[EA[i] + 1]++; deg[EB[i] + 1]++; }
  for (i = 0; i < nN; i++) deg[i + 1] += deg[i];
  var head = deg, adj = new Int32Array(nE * 2), aidx = new Int32Array(nE * 2);
  var fill = new Int32Array(nN);
  for (i = 0; i < nE; i++) {
    var a = EA[i], b = EB[i];
    var p1 = head[a] + fill[a]++; adj[p1] = b; aidx[p1] = i;
    var p2 = head[b] + fill[b]++; adj[p2] = a; aidx[p2] = i;
  }
  var CELL = 120, minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (i = 0; i < nN; i++) {
    if (X[i] < minX) minX = X[i]; if (X[i] > maxX) maxX = X[i];
    if (Y[i] < minY) minY = Y[i]; if (Y[i] > maxY) maxY = Y[i];
  }
  var gw = Math.max(1, Math.ceil((maxX - minX) / CELL));
  var gh = Math.max(1, Math.ceil((maxY - minY) / CELL));
  function cellOf(x, y) {
    var cx = Math.min(gw - 1, Math.max(0, ((x - minX) / CELL) | 0));
    var cy = Math.min(gh - 1, Math.max(0, ((y - minY) / CELL) | 0));
    return cy * gw + cx;
  }
  var cnt = new Int32Array(gw * gh + 1);
  for (i = 0; i < nN; i++) cnt[cellOf(X[i], Y[i]) + 1]++;
  for (i = 0; i < gw * gh; i++) cnt[i + 1] += cnt[i];
  var cells = new Int32Array(nN), cf = new Int32Array(gw * gh);
  for (i = 0; i < nN; i++) { var c = cellOf(X[i], Y[i]); cells[cnt[c] + cf[c]++] = i; }
  return {
    side: side, nN: nN, nE: nE, X: X, Y: Y, LA: LA, LO: LO,
    EA: EA, EB: EB, EL: EL, EC: EC, ES: ES, EN: EN, EF: EF,
    head: head, adj: adj, aidx: aidx,
    minX: minX, minY: minY, gw: gw, gh: gh, CELL: CELL, cnt: cnt, cells: cells,
    lat0: lat0, lon0: lon0, kx: kx, ky: ky, cellOf: cellOf,
    g: new Float32Array(nN), stamp: new Int32Array(nN), came: new Int32Array(nN),
    cameE: new Int32Array(nN), run: 0,
    heapK: new Float64Array(1 << 17), heapV: new Int32Array(1 << 17)
  };
}
function toXY(N, lat, lon) { return [lon * N.kx, lat * N.ky]; }
function toLL(N, x, y) { return [y / N.ky, x / N.kx]; }
function nearest(N, x, y) {
  /* 逐圈往外找最近節點。
     ⚠ 不能「找到就停」：格子邊長 120 公尺，在第 1 圈找到 297 公尺外的節點時，
     281 公尺外的那個可能還在第 2、3 圈。必須一直擴到「這一圈最近可能距離
     已經超過目前最佳」才停，否則吸附會挑到次近的點——整條路線就跟著跑掉。
     （這個 bug 是跟 Python 版對照時抓到的，Python 用 KD-tree 沒有這問題。） */
  var best = -1, bd = Infinity;
  var cx = Math.min(N.gw - 1, Math.max(0, ((x - N.minX) / N.CELL) | 0));
  var cy = Math.min(N.gh - 1, Math.max(0, ((y - N.minY) / N.CELL) | 0));
  var maxR = Math.max(N.gw, N.gh);
  for (var r = 0; r <= maxR; r++) {
    if (best >= 0 && (r - 1) * N.CELL > Math.sqrt(bd)) break;
    for (var dy = -r; dy <= r; dy++) {
      for (var dx = -r; dx <= r; dx++) {
        if (r > 0 && Math.abs(dx) !== r && Math.abs(dy) !== r) continue;
        var gx = cx + dx, gy = cy + dy;
        if (gx < 0 || gy < 0 || gx >= N.gw || gy >= N.gh) continue;
        var c = gy * N.gw + gx;
        for (var p = N.cnt[c]; p < N.cnt[c + 1]; p++) {
          var i = N.cells[p], ex = N.X[i] - x, ey = N.Y[i] - y, d = ex * ex + ey * ey;
          if (d < bd) { bd = d; best = i; }
        }
      }
    }
  }
  return { i: best, d: Math.sqrt(bd) };
}
/* A*：用 run 戳記代替每次清空陣列。兩萬次呼叫下這是關鍵 */
function astar(N, s, t, wantPath) {
  if (s === t) return wantPath ? { nodes: [s], edges: [] } : 0;
  N.run++;
  var g = N.g, stamp = N.stamp, run = N.run, HK = N.heapK, HV = N.heapV, hn = 0;
  var tx = N.X[t], ty = N.Y[t];
  g[s] = 0; stamp[s] = run; N.came[s] = -1; N.cameE[s] = -1;
  HK[0] = Math.hypot(N.X[s] - tx, N.Y[s] - ty); HV[0] = s; hn = 1;
  var guard = 0;
  while (hn > 0) {
    if (++guard > 300000) return wantPath ? null : -1;
    var v = HV[0];
    hn--; HK[0] = HK[hn]; HV[0] = HV[hn];
    var i = 0;
    while (true) {
      var l = 2 * i + 1, r = l + 1, m = i;
      if (l < hn && HK[l] < HK[m]) m = l;
      if (r < hn && HK[r] < HK[m]) m = r;
      if (m === i) break;
      var tk = HK[i]; HK[i] = HK[m]; HK[m] = tk;
      var tv = HV[i]; HV[i] = HV[m]; HV[m] = tv; i = m;
    }
    if (v === t) {
      if (!wantPath) return g[t];
      var nodes = [], edges = [], cur = t;
      while (cur !== -1) { nodes.push(cur); if (N.cameE[cur] >= 0) edges.push(N.cameE[cur]); cur = N.came[cur]; }
      nodes.reverse(); edges.reverse();
      return { nodes: nodes, edges: edges };
    }
    var gv = g[v];
    for (var p = N.head[v]; p < N.head[v + 1]; p++) {
      var w = N.adj[p], ei = N.aidx[p], ng = gv + N.EC[ei];
      if (stamp[w] === run && g[w] <= ng) continue;
      stamp[w] = run; g[w] = ng; N.came[w] = v; N.cameE[w] = ei;
      var f = ng + Math.hypot(N.X[w] - tx, N.Y[w] - ty);
      var j = hn++;
      if (j >= HK.length) return wantPath ? null : -1;
      HK[j] = f; HV[j] = w;
      while (j > 0) {
        var pa = (j - 1) >> 1;
        if (HK[pa] <= HK[j]) break;
        var a1 = HK[pa]; HK[pa] = HK[j]; HK[j] = a1;
        var b1 = HV[pa]; HV[pa] = HV[j]; HV[j] = b1; j = pa;
      }
    }
  }
  return wantPath ? null : -1;
}

/* ══════════════════ 圖形落地 ══════════════════ */
var MAX_SNAP = 260;
function ctrlPoints(pts) {
  /* pts 是經緯度；距離一律用 haversine，與 Python 的 path_len 一致 */
  var per = 0, i;
  for (i = 0; i < pts.length - 1; i++) per += hav(pts[i], pts[i + 1]);
  var gap = Math.max(60, per / 40), out = [];
  for (i = 0; i < pts.length - 1; i++) {
    var a = pts[i], b = pts[i + 1];
    out.push(a);
    var d = hav(a, b), n = Math.floor(d / gap);
    for (var k = 1; k <= n; k++) {
      var t = k / (n + 1);
      out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    }
  }
  out.push(pts[pts.length - 1]);
  return out;
}
function place(clat, clon, base, size, rot) {
  /* 與 Python fitter.place 一致：以「中心」為原點的切平面，回傳經緯度。
     不能改用全域平面——兩者差 0.03%，足以讓吸附結果翻面。 */
  var a = rot * DEG, ca = Math.cos(a), sa = Math.sin(a), o = [];
  var kLat = 1 / (R_EARTH * DEG), kLon = 1 / (R_EARTH * DEG * Math.cos(clat * DEG));
  for (var i = 0; i < base.length; i++) {
    var x = base[i][0] * size, y = base[i][1] * size;
    var ex = x * ca - y * sa, ny = x * sa + y * ca;
    o.push([clat + ny * kLat, clon + ex * kLon]);
  }
  return o;
}
function moveCenter(clat, clon, dx, dy) {
  return [clat + dy / (R_EARTH * DEG),
  clon + dx / (R_EARTH * DEG * Math.cos(clat * DEG))];
}
function edgeBetween(N, a, b) {
  /* 兩個相鄰節點之間的路段索引。同一對節點可能有多條邊（極少），取成本最低的，
     與 Python 端建圖時「同一對只留成本最低那條」的規則一致。 */
  var best = -1, bc = Infinity;
  for (var p = N.head[a]; p < N.head[a + 1]; p++) {
    if (N.adj[p] === b && N.EC[N.aidx[p]] < bc) { bc = N.EC[N.aidx[p]]; best = N.aidx[p]; }
  }
  return best;
}
function build(N, base, clat, clon, size, rot) {
  var target = place(clat, clon, base, size, rot);      // 經緯度
  var ctrl = ctrlPoints(target), nodes = [], errs = [], i;
  for (i = 0; i < ctrl.length; i++) {
    var xy = toXY(N, ctrl[i][0], ctrl[i][1]);
    var r = nearest(N, xy[0], xy[1]);
    if (r.i < 0) return null;
    errs.push(hav(ctrl[i], [N.LA[r.i], N.LO[r.i]]));
    if (!nodes.length || nodes[nodes.length - 1] !== r.i) nodes.push(r.i);
  }
  if (nodes.length < 4) return null;
  var sum = 0, mx = 0;
  for (i = 0; i < errs.length; i++) { sum += errs[i]; if (errs[i] > mx) mx = errs[i]; }
  if (sum / errs.length > MAX_SNAP || mx > MAX_SNAP * 1.6) return null;
  if (nodes[0] !== nodes[nodes.length - 1]) nodes.push(nodes[0]);
  var seq = [], detours = [];
  for (i = 0; i < nodes.length - 1; i++) {
    var a = nodes[i], b = nodes[i + 1];
    if (a === b) continue;
    var pr = astar(N, a, b, true);
    if (!pr) return null;
    var straight = hav([N.LA[a], N.LO[a]], [N.LA[b], N.LO[b]]), actual = 0;
    for (var k = 0; k < pr.edges.length; k++) actual += N.EL[pr.edges[k]];
    if (straight > 25) detours.push(actual / straight);
    for (var q = 0; q < pr.nodes.length - 1; q++) seq.push(pr.nodes[q]);
  }
  seq.push(nodes[nodes.length - 1]);
  /* 砍掉 A→B→A 的原地折返贅點。只動節點序列，路段之後再由節點對重建——
     同時 pop 兩者會錯位，長度會整整多算一公里（對照測試抓到過）。 */
  var s2 = [];
  for (i = 0; i < seq.length; i++) {
    if (s2.length >= 2 && s2[s2.length - 2] === seq[i]) s2.pop();
    else s2.push(seq[i]);
  }
  var e2 = [], L = 0;
  for (i = 0; i < s2.length - 1; i++) {
    var ei = edgeBetween(N, s2[i], s2[i + 1]);
    if (ei < 0) return null;
    e2.push(ei); L += N.EL[ei];
  }
  return {
    seq: s2, edges: e2, target: target, length: L,
    place: { clat: clat, clon: clon, size: size, rot: rot },
    snapMean: sum / errs.length, snapMax: mx,
    worstDetour: detours.length ? Math.max.apply(null, detours) : 1
  };
}
function fidelity(N, rt, size) {
  var A = resample(rt.seq.map(function (i) { return [N.X[i], N.Y[i]]; }), 240);
  var B = resample(rt.target.map(function (p) { return toXY(N, p[0], p[1]); }), 240);
  var da = [], db = [], i;
  for (i = 0; i < A.length; i++) da.push(polyDist(A[i][0], A[i][1], B));
  for (i = 0; i < B.length; i++) db.push(polyDist(B[i][0], B[i][1], A));
  da.sort(function (a, b) { return a - b; }); db.sort(function (a, b) { return a - b; });
  function mean(v) { var s = 0; for (var j = 0; j < v.length; j++) s += v[j]; return s / v.length; }
  var m = (mean(da) + mean(db)) / 2;
  var p90 = (da[(da.length * 0.9) | 0] + db[(db.length * 0.9) | 0]) / 2;
  var err = 0.5 * m + 0.5 * p90;
  return { score: Math.max(0, 1 - err / (0.12 * size)), err: err };
}
function retraceRatio(N, rt) {
  var seen = {}, dup = 0, tot = 0;
  for (var i = 0; i < rt.edges.length; i++) {
    var e = rt.edges[i]; tot += N.EL[e];
    if (seen[e]) dup += N.EL[e]; else seen[e] = 1;
  }
  return tot ? dup / tot : 0;
}
function safetyMean(N, rt) {
  var tot = 0, s = 0;
  for (var i = 0; i < rt.edges.length; i++) {
    tot += N.EL[rt.edges[i]]; s += N.ES[rt.edges[i]] * N.EL[rt.edges[i]];
  }
  return tot ? s / tot : 0;
}
function quickScore(N, base, clat, clon, size, rot) {
  var rt = build(N, base, clat, clon, size, rot);
  if (!rt) return null;
  var f = fidelity(N, rt, size).score;
  var snap = 1 - Math.min(1, rt.snapMean / 160);
  var det = 1 - Math.min(1, (rt.worstDetour - 1.3) / 1.7);
  var tilt = 1 - Math.min(1, Math.max(0, Math.abs(rot) - 25) / 35);
  rt.q = 0.46 * f + 0.16 * safetyMean(N, rt) +
    0.08 * (1 - Math.min(1, retraceRatio(N, rt) * 2.5)) +
    0.12 * snap + 0.10 * det + 0.08 * tilt;
  return rt;
}
function search(N, base, targetKm, onProg) {
  var per = 0, i;
  for (i = 0; i < base.length - 1; i++)
    per += Math.hypot(base[i + 1][0] - base[i][0], base[i + 1][1] - base[i][1]);
  var size0 = targetKm * 1000 / per / 1.12;
  var bb = N.side.bbox, S = bb[0], W = bb[1], Nn = bb[2], E = bb[3];
  var rots = [-24, -16, -8, 0, 8, 16, 24], best = null, done = 0, tot = 25 * rots.length;
  for (var a = 1; a <= 5; a++) {
    for (var b = 1; b <= 5; b++) {
      var clat = S + (Nn - S) * a / 6, clon = W + (E - W) * b / 6;
      var xy = toXY(N, clat, clon), nb = nearest(N, xy[0], xy[1]);
      if (nb.i < 0 || hav([clat, clon], [N.LA[nb.i], N.LO[nb.i]]) > 600) {
        done += rots.length; continue;
      }
      for (var r = 0; r < rots.length; r++) {
        var rt = quickScore(N, base, clat, clon, size0, rots[r]);
        if (rt && (!best || rt.q > best.q)) best = rt;
        done++;
      }
      if (onProg) onProg(done / tot * 0.7);
    }
  }
  if (!best) return null;
  var stepM = 260, stepR = 6, stepS = 0.10;
  for (var it = 0; it < 9; it++) {
    var improved = false, p = best.place, cand, c2;
    var moves = [[stepM, 0], [-stepM, 0], [0, stepM], [0, -stepM],
    [stepM, stepM], [-stepM, -stepM]];
    for (i = 0; i < moves.length; i++) {
      c2 = moveCenter(p.clat, p.clon, moves[i][0], moves[i][1]);
      cand = quickScore(N, base, c2[0], c2[1], p.size, p.rot);
      if (cand && cand.q > best.q) { best = cand; improved = true; p = best.place; }
    }
    for (i = 0; i < 2; i++) {
      cand = quickScore(N, base, p.clat, p.clon, p.size, p.rot + (i ? -stepR : stepR));
      if (cand && cand.q > best.q) { best = cand; improved = true; p = best.place; }
    }
    for (i = 0; i < 2; i++) {
      cand = quickScore(N, base, p.clat, p.clon, p.size * (i ? 1 - stepS : 1 + stepS), p.rot);
      if (cand && cand.q > best.q) { best = cand; improved = true; p = best.place; }
    }
    if (!improved) { stepM *= 0.55; stepR *= 0.6; stepS *= 0.6; if (stepM < 40) break; }
    if (onProg) onProg(0.7 + it / 9 * 0.25);
  }
  return best;
}
function fitBand(N, base, rt, loKm, hiKm) {
  var P = rt.place;
  function sizeFor(km) {
    var lo = P.size * 0.30, hi = P.size * 3.0, best = P.size, bd = 1e9;
    for (var i = 0; i < 16; i++) {
      var m = (lo + hi) / 2;
      var r = build(N, base, P.clat, P.clon, m, P.rot);
      if (!r) { lo = m; continue; }
      var km2 = r.length / 1000;
      if (Math.abs(km2 - km) < bd) { bd = Math.abs(km2 - km); best = m; }
      if (km2 < km) lo = m; else hi = m;
    }
    return best;
  }
  var sLo = sizeFor(loKm + 0.15), sHi = sizeFor(hiKm - 0.15);
  if (sHi < sLo) { var t = sLo; sLo = sHi; sHi = t; }
  var best = rt, bq = -1;
  for (var i = 0; i < 5; i++) {
    var m = sLo + (sHi - sLo) * i / 4;
    var r = quickScore(N, base, P.clat, P.clon, m, P.rot);
    if (!r) continue;
    var km = r.length / 1000;
    if (km < loKm || km > hiKm) continue;
    if (r.q > bq) { best = r; bq = r.q; }
  }
  return best;
}
self.CATRUN_ENGINE = true;

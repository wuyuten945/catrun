/* 貓行台中 — Worker：評分、產出、訊息迴圈
 * 演算法本體在 engine.js；這裡是規格書第二節的評分與第三節的產出。
 * ⚠ 與 catrun/scoring.py、catrun/export.py 必須一致，改了要跑對照測試。 */
"use strict";
importScripts("engine.js");

var WEIGHTS = { shape: 0.30, safety: 0.25, distance: 0.20, district: 0.15, logistics: 0.10 };
var LABEL = {
  shape: "圖形還原度與辨識性", safety: "道路安全性與通行品質",
  distance: "里程與難度分級", district: "區域特性與地標串聯",
  logistics: "補給與交通可達性"
};
var LEVELS = {
  standard: { label: "標準大眾級（休閒跑）", min: 5, max: 8 },
  challenge: { label: "挑戰級（進階跑）", min: 10, max: 15 }
};
var CLIMB_PER_10KM = 50, SIGNAL_PER_KM = 1.5, SUPPLY_GAP_KM = 3;
var MAP_SCALE = 25000, TRANSIT_WALK = 400;
var GREENWAY = ["園道", "綠道", "步道", "自行車道", "河濱", "堤防", "草悟道",
  "美術園道", "柳川", "綠川", "筏子溪", "旱溪"];
var HW_GREEN = { 0: 1, 1: 1, 2: 1, 4: 1, 5: 1 };      // footway/path/pedestrian/cycleway/living_street
var HW_MAJOR = { 12: 1, 14: 1 };                       // secondary / primary

function band(v, good, bad) {
  if (good === bad) return v <= good ? 1 : 0;
  if (good < bad) return Math.max(0, Math.min(1, (bad - v) / (bad - good)));
  return Math.max(0, Math.min(1, (v - bad) / (good - bad)));
}
function hwOf(N, e) { return N.EF[e] & 31; }
function litOf(N, e) { return (N.EF[e] >> 5) & 3; }
function nameOf(N, e) { return N.side.names[N.EN[e]] || ""; }
function isGreen(N, e) {
  if (HW_GREEN[hwOf(N, e)]) return true;
  var nm = nameOf(N, e);
  for (var i = 0; i < GREENWAY.length; i++) if (nm.indexOf(GREENWAY[i]) >= 0) return true;
  return false;
}
function routeLL(N, rt) {
  return rt.seq.map(function (i) { return [N.LA[i], N.LO[i]]; });
}
/* 路線周邊的路段，給前端畫底圖用。只送框內的，整區送過去會是好幾 MB。 */
function roadsNear(N, rt, padRatio) {
  var la = rt.seq.map(function (i) { return N.LA[i]; });
  var lo = rt.seq.map(function (i) { return N.LO[i]; });
  var s = Math.min.apply(null, la), n = Math.max.apply(null, la);
  var w = Math.min.apply(null, lo), e = Math.max.apply(null, lo);
  var pl = (n - s) * padRatio, po = (e - w) * padRatio;
  s -= pl; n += pl; w -= po; e += po;
  var out = [], major = [];
  for (var i = 0; i < N.nE; i++) {
    var a = N.EA[i], b = N.EB[i];
    var mla = (N.LA[a] + N.LA[b]) / 2, mlo = (N.LO[a] + N.LO[b]) / 2;
    if (mla < s || mla > n || mlo < w || mlo > e) continue;
    var t = HW_MAJOR[N.EF[i] & 31] ? major : out;
    t.push(N.LA[a], N.LO[a], N.LA[b], N.LO[b]);
  }
  return { minor: new Float32Array(out), major: new Float32Array(major) };
}
/* 沿線 radius 內的設施，附帶「在第幾公尺處」——補給空窗與地標里程都要用 */
function nearbyAlong(N, rt, items, radius) {
  var pts = rt.seq.map(function (i) { return [N.X[i], N.Y[i]]; });
  var cum = [0];
  for (var i = 0; i < pts.length - 1; i++)
    cum.push(cum[i] + Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]));
  var out = [];
  for (var k = 0; k < items.length; k++) {
    var xy = toXY(N, items[k][0], items[k][1]);
    var bd = Infinity, at = 0;
    for (var j = 0; j < pts.length; j++) {
      var d = Math.hypot(pts[j][0] - xy[0], pts[j][1] - xy[1]);
      if (d < bd) { bd = d; at = cum[j]; }
    }
    if (bd <= radius) out.push({ d: bd, at: at, it: items[k] });
  }
  out.sort(function (a, b) { return a.at - b.at; });
  return out;
}
function signalsOnRoute(N, rt, radius) {
  var sig = N.side.signals;
  if (!sig.length) return 0;
  var pts = rt.seq.map(function (i) { return [N.X[i], N.Y[i]]; });
  var hit = 0;
  for (var k = 0; k < sig.length; k++) {
    var xy = toXY(N, sig[k][0], sig[k][1]);
    for (var j = 0; j < pts.length; j++) {
      if (Math.hypot(pts[j][0] - xy[0], pts[j][1] - xy[1]) <= radius) { hit++; break; }
    }
  }
  return hit;
}
function scoreRoute(N, rt, levelKey, climb) {
  var lv = LEVELS[levelKey], det = {}, i;
  var km = rt.length / 1000;

  var fid = fidelity(N, rt, rt.place.size);
  var closure = Math.hypot(N.X[rt.seq[0]] - N.X[rt.seq[rt.seq.length - 1]],
    N.Y[rt.seq[0]] - N.Y[rt.seq[rt.seq.length - 1]]);
  var retrace = retraceRatio(N, rt);
  var mm = rt.place.size / MAP_SCALE * 1000;
  var read = band(mm, 40, 12);
  var shape = 0.50 * fid.score + 0.20 * band(closure, 30, 400) +
    0.15 * band(retrace, 0.05, 0.40) + 0.15 * read;
  det.shape = {
    "貼合度": Math.round(fid.score * 1000) / 1000,
    "平均偏離(m)": Math.round(fid.err * 10) / 10,
    "起終點相距(m)": Math.round(closure * 10) / 10,
    "折返路段佔比": Math.round(retrace * 1000) / 1000,
    "1:25000 圖上長邊(mm)": Math.round(mm * 10) / 10
  };

  var sf = safetyMean(N, rt);
  var tot = 0, gw = 0, dark = 0;
  for (i = 0; i < rt.edges.length; i++) {
    var e = rt.edges[i]; tot += N.EL[e];
    if (isGreen(N, e)) gw += N.EL[e];
    if (litOf(N, e) === 2) dark += N.EL[e];
  }
  gw = tot ? gw / tot : 0;
  var sig = signalsOnRoute(N, rt, 35), sigKm = sig / Math.max(km, 0.1);
  var dead = 0;
  for (i = 0; i < rt.seq.length; i++)
    if (N.head[rt.seq[i] + 1] - N.head[rt.seq[i]] === 1) dead++;
  var safety = 0.45 * sf + 0.25 * band(sigKm, SIGNAL_PER_KM, 4) +
    0.20 * Math.min(1, gw / 0.35) + 0.10 * band(dead, 0, 6);
  det.safety = {
    "路段安全加權均值": Math.round(sf * 1000) / 1000,
    "綠園道/人行道佔比": Math.round(gw * 1000) / 1000,
    "沿線紅綠燈(處)": sig, "每公里停等(次)": Math.round(sigKm * 100) / 100,
    "上限(次/km)": SIGNAL_PER_KM, "行經無出口節點": dead,
    "無照明路段(m)": Math.round(dark)
  };

  var distS = (km >= lv.min && km <= lv.max) ? 1
    : band(km < lv.min ? lv.min - km : km - lv.max, 0, 3);
  var climbS, climbTxt, limit = null;
  if (climb === null || climb === undefined) { climbS = 0.75; climbTxt = "未取得"; }
  else {
    limit = CLIMB_PER_10KM * km / 10;
    climbS = band(climb, limit, limit * 3 + 30);
    climbTxt = climb.toFixed(1) + " m";
  }
  det.distance = {
    "里程(km)": Math.round(km * 100) / 100, "級距": lv.label,
    "級距範圍": lv.min + "–" + lv.max + " km", "累計爬升": climbTxt,
    "爬升上限(m)": limit ? Math.round(limit * 10) / 10 : "—"
  };
  var distance = 0.70 * distS + 0.30 * climbS;

  var xs = rt.seq.map(function (i2) { return N.X[i2]; });
  var ys = rt.seq.map(function (i2) { return N.Y[i2]; });
  var wM = Math.max.apply(null, xs) - Math.min.apply(null, xs);
  var hM = Math.max.apply(null, ys) - Math.min.apply(null, ys);
  var aspect = hM ? wM / hM : 1;
  var lo = N.side.aspect[0], hi = N.side.aspect[1];
  var aspS = (aspect >= lo && aspect <= hi) ? 1
    : band(aspect < lo ? lo - aspect : aspect - hi, 0, 0.9);
  var marks = nearbyAlong(N, rt, N.side.landmarks, 180);
  var mkS = band(Math.abs(Math.min(marks.length, 3) - 3), 0, 3);
  var district = 0.40 * aspS + 0.35 * Math.min(1, gw / 0.30) + 0.25 * mkS;
  det.district = {
    "行政區": N.side.name, "適配風格": N.side.style,
    "路線寬高比": Math.round(aspect * 100) / 100,
    "建議寬高比": lo.toFixed(2) + "–" + hi.toFixed(2),
    "沿線地標(180m內)": marks.length
  };

  var start = [N.LA[rt.seq[0]], N.LO[rt.seq[0]]];
  var tBest = null, tD = 9999;
  for (i = 0; i < N.side.transit.length; i++) {
    var t = N.side.transit[i], d = hav(start, [t[0], t[1]]);
    if (d < tD) { tD = d; tBest = t; }
  }
  var sup = nearbyAlong(N, rt, N.side.supply, 220);
  var gap;
  if (sup.length) {
    var pos = [0].concat(sup.map(function (s) { return s.at; })).concat([rt.length]);
    gap = 0;
    for (i = 0; i < pos.length - 1; i++) gap = Math.max(gap, pos[i + 1] - pos[i]);
    gap /= 1000;
  } else gap = km;
  var logistics = 0.55 * band(tD, TRANSIT_WALK, 1500) + 0.45 * band(gap, SUPPLY_GAP_KM, 6);
  det.logistics = {
    "起點最近大眾運輸": tBest ? (tBest[2] + "（" + tBest[3] + "）") : "—",
    "距離(m)": Math.round(tD), "步行 5 分鐘門檻(m)": TRANSIT_WALK,
    "沿線補給點": sup.length, "最大補給空窗(km)": Math.round(gap * 100) / 100,
    "空窗上限(km)": SUPPLY_GAP_KM
  };

  var parts = { shape: shape, safety: safety, distance: distance, district: district, logistics: logistics };
  var total = 0;
  for (var k in WEIGHTS) total += WEIGHTS[k] * parts[k];
  var out = { total: Math.round(total * 1000) / 10, parts: {}, weights: {}, detail: det };
  for (k in parts) out.parts[k] = Math.round(parts[k] * 1000) / 10;
  for (k in WEIGHTS) out.weights[k] = Math.round(WEIGHTS[k] * 100);
  out.marks = marks.slice(0, 3).map(function (m) {
    return [Math.round(m.d), Math.round(m.at / 10) / 100, m.it[2], m.it[3]];
  });
  out.supply = sup.slice(0, 12).map(function (m) {
    return [Math.round(m.d), Math.round(m.at / 10) / 100, m.it[2]];
  });
  return out;
}

/* ── Cue Sheet ── */
function cueSheet(N, rt, minSeg) {
  minSeg = minSeg || 45;
  var rows = [], acc = 0, cur = null, segStart = 0, segPts = [], i;
  var LL = routeLL(N, rt);
  for (i = 0; i < rt.edges.length; i++) {
    var nm = nameOf(N, rt.edges[i]) || "無名巷弄";
    if (cur === null) { cur = nm; segStart = acc; segPts = [LL[i]]; }
    else if (nm !== cur) {
      if (acc - segStart >= minSeg) {
        rows.push({ km: segStart / 1000, road: cur, len: Math.round(acc - segStart),
          pts: segPts.concat([LL[i]]) });
        cur = nm; segStart = acc; segPts = [LL[i]];
      } else cur = nm;
    } else segPts.push(LL[i]);
    acc += N.EL[rt.edges[i]];
  }
  if (cur !== null) rows.push({ km: segStart / 1000, road: cur,
    len: Math.round(acc - segStart), pts: segPts.concat([LL[LL.length - 1]]) });
  return rows.map(function (r, k) {
    var act = "出發";
    if (k > 0) {
      var p = rows[k - 1].pts;
      var a = bearing(p[Math.max(0, p.length - 3)], p[p.length - 1]);
      var b = bearing(r.pts[0], r.pts[Math.min(2, r.pts.length - 1)]);
      act = turnOf(a, b);
    }
    return { seq: k + 1, km: Math.round(r.km * 100) / 100, action: act,
      road: r.road, len_m: r.len, lat: r.pts[0][0], lon: r.pts[0][1] };
  });
}
/* ── 安全提醒 ── */
function safetyNotes(N, rt) {
  var notes = [], acc = 0, darkFrom = null, darkLen = 0, i;
  for (i = 0; i < rt.edges.length; i++) {
    var e = rt.edges[i];
    if (litOf(N, e) === 2) { if (darkFrom === null) darkFrom = acc; darkLen += N.EL[e]; }
    else {
      if (darkFrom !== null && darkLen >= 120)
        notes.push({ km: darkFrom / 1000, type: "夜間照明",
          text: "無路燈標記路段約 " + Math.round(darkLen) + " 公尺，夜跑請帶頭燈" });
      darkFrom = null; darkLen = 0;
    }
    acc += N.EL[e];
  }
  if (darkFrom !== null && darkLen >= 120)
    notes.push({ km: darkFrom / 1000, type: "夜間照明",
      text: "無路燈標記路段約 " + Math.round(darkLen) + " 公尺，夜跑請帶頭燈" });
  var cr = nearbyAlong(N, rt, N.side.crossings, 25);
  for (i = 0; i < cr.length; i++) {
    var c = cr[i].it;
    if (c[2] === "traffic_signals" || c[2] === "signals") continue;
    notes.push({ km: cr[i].at / 1000, type: "無號誌路口",
      text: "無號誌斑馬線／穿越點" + (c[3] ? "（" + c[3] + "）" : "") + "，過馬路請停看" });
  }
  acc = 0;
  for (i = 0; i < rt.edges.length; i++) {
    var e2 = rt.edges[i];
    if (HW_MAJOR[hwOf(N, e2)] && N.EL[e2] >= 200)
      notes.push({ km: acc / 1000, type: "幹道路段",
        text: (nameOf(N, e2) || "此路段") + " 為主要幹道（" + Math.round(N.EL[e2]) +
          " 公尺），車流較快請走人行道" });
    acc += N.EL[e2];
  }
  notes.sort(function (a, b) { return a.km - b.km; });
  var out = [], last = null;
  for (i = 0; i < notes.length; i++) {
    if (last && last.type === notes[i].type && Math.abs(last.km - notes[i].km) < 0.35) continue;
    notes[i].km = Math.round(notes[i].km * 100) / 100;
    out.push(notes[i]); last = notes[i];
  }
  return out;
}
/* ── 軌跡檔 ── */
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" }[c];
  });
}
function gpxOf(LL, name, wpts) {
  var L = ['<?xml version="1.0" encoding="UTF-8"?>',
    '<gpx version="1.1" creator="catrun" xmlns="http://www.topografix.com/GPX/1/1">',
    "<metadata><name>" + esc(name) + "</name></metadata>"];
  (wpts || []).forEach(function (w) {
    L.push('<wpt lat="' + w[0].toFixed(6) + '" lon="' + w[1].toFixed(6) +
      '"><name>' + esc(w[2]) + "</name><desc>地標</desc></wpt>");
  });
  L.push("<trk><name>" + esc(name) + "</name><trkseg>");
  LL.forEach(function (p) {
    L.push('<trkpt lat="' + p[0].toFixed(6) + '" lon="' + p[1].toFixed(6) + '"/>');
  });
  L.push("</trkseg></trk>", "</gpx>");
  return L.join("\n");
}
function kmlOf(LL, name, wpts) {
  var L = ['<?xml version="1.0" encoding="UTF-8"?>',
    '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
    "<name>" + esc(name) + "</name>",
    '<Style id="r"><LineStyle><color>ff2000d6</color><width>4</width></LineStyle></Style>'];
  (wpts || []).forEach(function (w) {
    L.push("<Placemark><name>" + esc(w[2]) + "</name><Point><coordinates>" +
      w[1].toFixed(6) + "," + w[0].toFixed(6) + ",0</coordinates></Point></Placemark>");
  });
  L.push("<Placemark><name>" + esc(name) + '</name><styleUrl>#r</styleUrl><LineString>' +
    "<tessellate>1</tessellate><coordinates>");
  L.push(LL.map(function (p) { return p[1].toFixed(6) + "," + p[0].toFixed(6) + ",0"; }).join(" "));
  L.push("</coordinates></LineString></Placemark>", "</Document></kml>");
  return L.join("\n");
}
/* ── 高程（可選；取不到就誠實回 null，不塞假數字） ── */
function fetchClimb(LL) {
  var s = resample(LL, 120);
  var lat = s.map(function (p) { return p[0].toFixed(5); }).join(",");
  var lon = s.map(function (p) { return p[1].toFixed(5); }).join(",");
  return fetch("https://api.open-meteo.com/v1/elevation?latitude=" + lat + "&longitude=" + lon)
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) {
      if (!j || !j.elevation) return null;
      var e = j.elevation, gain = 0, ref = e[0];
      for (var i = 1; i < e.length; i++) {
        if (e[i] - ref >= 5) { gain += e[i] - ref; ref = e[i]; }
        else if (e[i] < ref) ref = e[i];
      }
      return Math.round(gain * 10) / 10;
    })
    .catch(function () { return null; });
}

/* ══════════════════ 訊息迴圈 ══════════════════ */
var LOADED = {};
function post(t, d) { d = d || {}; d.type = t; self.postMessage(d); }

self.onmessage = function (ev) {
  var m = ev.data;
  try {
    if (m.cmd === "trace") {
      var r = traceImage(m.gray, m.w, m.h, m.close, m.points);
      post("traced", { pts: r.pts, info: r.info });
      return;
    }
    if (m.cmd === "plan") {
      plan(m);
      return;
    }
  } catch (e) {
    post("error", { message: e && e.message ? e.message : String(e) });
  }
};

function loadNet(key) {
  if (LOADED[key]) return Promise.resolve(LOADED[key]);
  post("progress", { text: "下載路網…", p: 0.02 });
  return fetch("net/" + key + ".json").then(function (r) {
    if (!r.ok) throw new Error("路網下載失敗（" + r.status + "）");
    return r.json();
  }).then(function (side) {
    post("progress", { text: "建立索引…", p: 0.06 });
    /* 路網本體是 base64 夾在 JSON 裡——靜態主機不壓縮 .bin，包進 JSON 才吃得到
       Brotli，實際傳輸量差一倍。 */
    var s = atob(side.bin), n = s.length, u = new Uint8Array(n);
    for (var i = 0; i < n; i++) u[i] = s.charCodeAt(i);
    side.bin = null;
    var N = buildNet(u.buffer, side);
    LOADED[key] = N;
    return N;
  });
}

function plan(m) {
  var districts = m.districts, results = [], idx = 0;
  function next() {
    if (idx >= districts.length) {
      results.sort(function (a, b) { return b.score.total - a.score.total; });
      post("done", { results: results });
      return;
    }
    var key = districts[idx++];
    loadNet(key).then(function (N) {
      post("progress", { text: N.side.name + " 規劃中…", p: 0.1 });
      var lv = LEVELS[m.level];
      var rt = search(N, m.shape, (lv.min + lv.max) / 2, function (p) {
        post("progress", { text: N.side.name + " 規劃中…", p: 0.1 + p * 0.7 });
      });
      if (!rt) {
        post("progress", { text: N.side.name + "：找不到可用配置", p: 0.9 });
        next(); return;
      }
      rt = fitBand(N, m.shape, rt, lv.min, lv.max);
      post("progress", { text: N.side.name + " 評分中…", p: 0.9 });
      var LL = routeLL(N, rt);
      fetchClimb(LL).then(function (climb) {
        var sc = scoreRoute(N, rt, m.level, climb);
        var name = N.side.name + " " + (m.shapeName || "自訂圖形") + " 圖形路跑路線";
        results.push({
          district: key, districtName: N.side.name, name: name,
          km: Math.round(rt.length / 10) / 100, climb: climb,
          start: [LL[0][0], LL[0][1]], level: m.level, levelLabel: lv.label,
          score: sc, cues: cueSheet(N, rt), notes: safetyNotes(N, rt),
          ll: LL, target: rt.target, roads: roadsNear(N, rt, 0.18),
          gpx: gpxOf(LL, name, sc.marks.map(function (x, i) {
            var mk = null;
            for (var j = 0; j < N.side.landmarks.length; j++)
              if (N.side.landmarks[j][2] === x[2]) { mk = N.side.landmarks[j]; break; }
            return mk ? [mk[0], mk[1], mk[2]] : null;
          }).filter(Boolean)),
          kml: kmlOf(LL, name, [])
        });
        next();
      });
    }).catch(function (e) {
      post("error", { message: e && e.message ? e.message : String(e) });
    });
  }
  next();
}

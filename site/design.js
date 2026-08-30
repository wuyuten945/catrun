/* 「自己排一條」的畫面。
 * 演算法全部在 Web Worker（engine.js / worker.js）裡跑，
 * 上傳的圖片只在瀏覽器裡處理，不會送到任何伺服器。 */
(function () {
  "use strict";

  var W = null;              // Worker
  var SHAPE = null;          // 描好的圖形（正規化點列）
  var SHAPE_NAME = "";
  var SELD = "south";        // 選到的行政區，或 "*區位名" 表示整個區位一起比
  var RESULTS = [];
  var SEL = 0;

  function $(i) { return document.getElementById(i); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function hue(v) { return v >= 80 ? "var(--s-hi)" : v >= 62 ? "var(--s-mid)" : "var(--s-lo)"; }

  function worker() {
    if (W) return W;
    W = new Worker("worker.js");
    W.onmessage = function (ev) {
      var m = ev.data;
      if (m.type === "progress") {
        var b = $("dz-prog"); if (b) b.style.width = (m.p * 100).toFixed(0) + "%";
        var t = $("dz-stat"); if (t) t.textContent = m.text;
      } else if (m.type === "traced") {
        SHAPE = m.pts;
        drawShape(m.pts, m.info);
        setBusy(false);
      } else if (m.type === "done") {
        RESULTS = m.results; SEL = 0;
        setBusy(false);
        paintResults();
      } else if (m.type === "error") {
        setBusy(false);
        var e = $("dz-err");
        if (e) e.innerHTML = '<div class="callout" style="margin:12px 0 0"><b>失敗</b><p>' +
          esc(m.message) + "</p></div>";
      }
    };
    W.onerror = function (e) {
      setBusy(false);
      var el = $("dz-err");
      if (el) el.innerHTML = '<div class="callout" style="margin:12px 0 0"><b>引擎載入失敗</b>' +
        "<p>" + esc(e.message || "Worker 無法啟動") + "</p></div>";
    };
    return W;
  }
  function setBusy(on) {
    ["dz-trace", "dz-run"].forEach(function (id) {
      var b = $(id); if (b) b.disabled = on;
    });
    if (!on) { var p = $("dz-prog"); if (p) p.style.width = "0%"; }
  }

  /* ── 上傳的圖 → 灰階陣列（全在瀏覽器裡） ── */
  function readImage(file, cb) {
    var img = new Image();
    var url = URL.createObjectURL(file);
    img.onload = function () {
      URL.revokeObjectURL(url);
      var MAX = 900;                       // 太大只會拖慢描圖，細節也用不到
      var sc = Math.min(1, MAX / Math.max(img.width, img.height));
      var w = Math.max(1, Math.round(img.width * sc));
      var h = Math.max(1, Math.round(img.height * sc));
      var cv = document.createElement("canvas");
      cv.width = w; cv.height = h;
      var cx = cv.getContext("2d");
      cx.fillStyle = "#fff"; cx.fillRect(0, 0, w, h);   // 去掉 PNG 透明底
      cx.drawImage(img, 0, 0, w, h);
      var d = cx.getImageData(0, 0, w, h).data;
      var gray = new Float32Array(w * h);
      for (var i = 0, j = 0; i < gray.length; i++, j += 4)
        gray[i] = 0.299 * d[j] + 0.587 * d[j + 1] + 0.114 * d[j + 2];
      cb(gray, w, h, cv.toDataURL("image/jpeg", 0.7));
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      $("dz-err").innerHTML = '<div class="callout" style="margin:12px 0 0"><b>讀不了這個檔</b>' +
        "<p>請換一張 JPG 或 PNG。</p></div>";
      setBusy(false);
    };
    img.src = url;
  }

  function drawShape(pts, info) {
    var cv = $("dz-shape");
    if (!cv) return;
    var S = 300, dpr = window.devicePixelRatio || 1;
    cv.width = S * dpr; cv.height = S * dpr;
    cv.style.width = S + "px"; cv.style.height = S + "px";
    var c = cv.getContext("2d");
    c.scale(dpr, dpr);
    c.clearRect(0, 0, S, S);
    c.strokeStyle = getComputedStyle(document.body).getPropertyValue("--route") || "#d6001c";
    c.lineWidth = 3; c.lineJoin = "round"; c.lineCap = "round";
    c.beginPath();
    pts.forEach(function (p, i) {
      var x = S / 2 + p[0] * (S - 40), y = S / 2 - p[1] * (S - 40);
      if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
    });
    c.stroke();
    $("dz-info").innerHTML = Object.keys(info).map(function (k) {
      return '<span class="metric">' + esc(k) + " <b>" + esc(info[k]) + "</b></span>";
    }).join("");
    $("dz-ok").innerHTML = '<div class="callout"><b>描好了，先確認這是你要的輪廓</b>' +
      "<p>不對的話調整下面的參數重描：線條沒接起來就把「缺口補償」調大（8～12）；" +
      "細節太碎就把點數調小。對了就往下選行政區。</p></div>";
    $("dz-step2").classList.remove("hide");
  }

  /* ── 結果 ── */
  function drawMap(r) {
    var cv = $("dz-map");
    if (!cv) return;
    var wrap = cv.parentNode;
    var W0 = wrap.clientWidth || 700, H0 = Math.round(W0 * 0.72);
    var dpr = window.devicePixelRatio || 1;
    cv.width = W0 * dpr; cv.height = H0 * dpr;
    cv.style.width = W0 + "px"; cv.style.height = H0 + "px";
    var c = cv.getContext("2d");
    c.scale(dpr, dpr);
    var all = r.ll.concat(r.target);
    var la = all.map(function (p) { return p[0]; }), lo = all.map(function (p) { return p[1]; });
    var s = Math.min.apply(null, la), n = Math.max.apply(null, la);
    var w = Math.min.apply(null, lo), e = Math.max.apply(null, lo);
    var kx = Math.cos((s + n) / 2 * Math.PI / 180);
    var pad = 16;
    var sx = (W0 - pad * 2) / Math.max((e - w) * kx, 1e-9);
    var sy = (H0 - pad * 2) / Math.max(n - s, 1e-9);
    var k = Math.min(sx, sy);
    function P(p) {
      return [W0 / 2 + ((p[1] - (w + e) / 2) * kx) * k,
      H0 / 2 - (p[0] - (s + n) / 2) * k];
    }
    var css = getComputedStyle(document.body);
    c.fillStyle = css.getPropertyValue("--surface-2") || "#f3f5f9";
    c.fillRect(0, 0, W0, H0);
    function lines(arr, col, wid) {
      c.strokeStyle = col; c.lineWidth = wid; c.beginPath();
      for (var i = 0; i + 3 < arr.length; i += 4) {
        var a = P([arr[i], arr[i + 1]]), b = P([arr[i + 2], arr[i + 3]]);
        c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]);
      }
      c.stroke();
    }
    if (r.roads) {
      lines(r.roads.minor, css.getPropertyValue("--line") || "#ccd3de", 1);
      lines(r.roads.major, css.getPropertyValue("--ink-3") || "#8a94a3", 2);
    }
    function poly(pts, col, wid) {
      c.strokeStyle = col; c.lineWidth = wid; c.lineJoin = "round"; c.lineCap = "round";
      c.beginPath();
      pts.forEach(function (p, i) {
        var q = P(p);
        if (i === 0) c.moveTo(q[0], q[1]); else c.lineTo(q[0], q[1]);
      });
      c.stroke();
    }
    poly(r.target, "rgba(120,170,220,.75)", 3);
    poly(r.ll, css.getPropertyValue("--route") || "#d6001c", 4);
    var st = P(r.ll[0]);
    c.beginPath(); c.arc(st[0], st[1], 7, 0, Math.PI * 2);
    c.fillStyle = "#fff"; c.fill();
    c.strokeStyle = "#0a7d2c"; c.lineWidth = 3; c.stroke();
  }
  function dl(text, name, mime) {
    var b = new Blob([text], { type: mime });
    var u = URL.createObjectURL(b);
    var a = document.createElement("a");
    a.href = u; a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(u); a.remove(); }, 1000);
  }
  window._dzDL = function (kind) {
    var r = RESULTS[SEL];
    var base = r.districtName + "_" + (SHAPE_NAME || "自訂圖形");
    if (kind === "gpx") dl(r.gpx, base + ".gpx", "application/gpx+xml");
    else dl(r.kml, base + ".kml", "application/vnd.google-earth.kml+xml");
  };
  window._dzSel = function (i) { SEL = i; paintResults(); };

  function paintResults() {
    var host = $("dz-out");
    if (!RESULTS.length) { host.innerHTML = ""; return; }
    var h = "";
    if (RESULTS.length > 1) {
      h += '<div class="panel"><h3>跨區排名</h3><div class="inner"><div class="tbl-wrap">' +
        "<table><tr><th>名次</th><th>行政區</th><th>里程</th><th>總分</th></tr>" +
        RESULTS.map(function (r, i) {
          return '<tr style="cursor:pointer" onclick="_dzSel(' + i + ')">' +
            "<td>" + (i + 1) + "</td><td>" + esc(r.districtName) +
            '</td><td class="n">' + r.km.toFixed(2) + ' km</td><td class="n" style="color:' +
            hue(r.score.total) + '"><b>' + r.score.total + "</b></td></tr>";
        }).join("") + "</table></div>" +
        '<p style="font-size:13px;color:var(--ink-3);margin:8px 0 0">點任一列看該區的完整結果。</p>' +
        "</div></div>";
    }
    var r = RESULTS[SEL];
    h += '<div class="detail-head" style="margin-top:18px"><h2>' + esc(r.name) + "</h2>" +
      '<div class="big-score" style="color:' + hue(r.score.total) + '">' +
      r.score.total + "<s>/100</s></div></div>";
    h += '<div class="panel"><div class="inner" style="padding:12px">' +
      '<canvas id="dz-map"></canvas></div></div>';
    h += '<div class="panel"><div class="inner"><div class="kv">' +
      "<div><span>里程</span><b>" + r.km.toFixed(2) + " km</b></div>" +
      '<div><span>級距</span><b style="font-size:14px">' + esc(r.levelLabel) + "</b></div>" +
      "<div><span>累計爬升</span><b>" +
      (r.climb == null ? "未取得" : r.climb.toFixed(0) + " m") + "</b></div>" +
      '<div><span>起終點</span><b style="font-size:13px">' +
      r.start[0].toFixed(5) + ", " + r.start[1].toFixed(5) + "</b></div>" +
      "</div></div></div>";
    h += '<div class="panel"><h3>下載</h3><div class="inner"><div class="dl">' +
      '<button class="btn primary" onclick="_dzDL(\'gpx\')">GPX 軌跡</button>' +
      '<button class="btn" onclick="_dzDL(\'kml\')">KML</button></div></div></div>';
    h += '<div class="panel"><h3>評分</h3><div class="inner"><div class="dims">' +
      ["shape", "safety", "distance", "district", "logistics"].map(function (k) {
        return '<div class="dim"><div class="nm">' + esc(LABELS[k]) + "<em>" +
          r.score.weights[k] + '%</em></div><div class="vl" style="color:' +
          hue(r.score.parts[k]) + '">' + r.score.parts[k].toFixed(1) + "</div>" +
          '<div class="trk"><i style="width:' + Math.max(2, r.score.parts[k]) +
          "%;background:" + hue(r.score.parts[k]) + '"></i></div></div>';
      }).join("") + "</div>";
    var mets = [];
    Object.keys(r.score.detail).forEach(function (k) {
      var dd = r.score.detail[k];
      Object.keys(dd).forEach(function (a) {
        mets.push('<span class="metric">' + esc(a) + " <b>" + esc(dd[a]) + "</b></span>");
      });
    });
    h += '<div class="metrics" style="margin-top:16px">' + mets.join("") + "</div></div></div>";
    h += '<div class="panel"><h3>路名循序導航表（' + r.cues.length + " 段）</h3>" +
      '<div class="inner"><div class="tbl-wrap"><table>' +
      "<tr><th>#</th><th>累計 km</th><th>動作</th><th>路名</th><th>該段</th></tr>" +
      r.cues.map(function (c) {
        return '<tr><td class="n">' + c.seq + '</td><td class="n">' + c.km.toFixed(2) +
          "</td><td>" + esc(c.action) + "</td><td>" + esc(c.road) +
          '</td><td class="n">' + c.len_m + " m</td></tr>";
      }).join("") + "</table></div></div></div>";
    h += '<div class="panel"><h3>安全提醒</h3><div class="inner">';
    h += r.notes.length
      ? '<div class="tbl-wrap"><table><tr><th>累計 km</th><th>類別</th><th>提醒</th></tr>' +
      r.notes.map(function (n) {
        return '<tr><td class="n">' + n.km.toFixed(2) + '</td><td class="note-type">' +
          esc(n.type) + "</td><td>" + esc(n.text) + "</td></tr>";
      }).join("") + "</table></div>"
      : "<p>沿線未偵測到需特別標記的路段。仍請遵守號誌、注意來車。</p>";
    h += "</div></div>";
    host.innerHTML = h;
    drawMap(r);
  }

  var LABELS = {
    shape: "圖形還原度與辨識性", safety: "道路安全性與通行品質",
    distance: "里程與難度分級", district: "區域特性與地標串聯",
    logistics: "補給與交通可達性"
  };

  /* ── 畫面 ── */
  var NETIDX = null;      // net/index.json：全部 29 個行政區
  window.renderDesign = function (IDX) {
    if (!NETIDX) {
      fetch("net/index.json").then(function (r) { return r.json(); })
        .then(function (j) { NETIDX = j.districts; window.renderDesign(IDX); })
        .catch(function () { NETIDX = IDX.districts; window.renderDesign(IDX); });
      document.getElementById("view").innerHTML =
        '<main><div class="wrap"><p>載入行政區清單…</p></div></main>';
      return;
    }
    var d = NETIDX;
    if (!d[SELD] && SELD.charAt(0) !== "*") SELD = Object.keys(d)[0];
    var view = document.getElementById("view");
    view.innerHTML =
      '<div class="hero"><div class="wrap">' +
      "<h1>把你的圖畫成路線</h1>" +
      "<p>上傳一張手繪圖，系統會描出輪廓，再把它落到台中的真實街道上，" +
      "排出一條跑得到的封閉環線。" +
      "<b>整個過程都在你的瀏覽器裡完成，圖片不會上傳到任何地方。</b></p>" +
      "</div></div>" +

      '<main><div class="wrap">' +
      '<div class="panel"><h3>一、上傳手繪圖</h3><div class="inner">' +
      '<input type="file" id="dz-file" accept="image/*" style="width:100%;padding:10px;' +
      "border:1px dashed var(--line);border-radius:9px;background:var(--surface-2);" +
      'color:var(--ink)"/>' +
      '<p style="font-size:13px;color:var(--ink-3);margin:8px 0 0">' +
      "白底黑線、線條接起來的圖最好描。手機拍的照片也可以，但背景越乾淨越準。</p>" +
      '<div class="kv" style="margin-top:14px">' +
      '<div><label style="font-size:12px;color:var(--ink-3)">缺口補償（像素）</label>' +
      '<input id="dz-close" type="number" value="6" min="0" max="30" style="width:100%;' +
      'padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink)"/></div>' +
      '<div><label style="font-size:12px;color:var(--ink-3)">簡化後點數</label>' +
      '<input id="dz-pts" type="number" value="64" min="20" max="160" style="width:100%;' +
      'padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink)"/></div>' +
      "</div>" +
      '<div class="dl" style="margin-top:14px">' +
      '<button class="btn primary" id="dz-trace">描圖</button></div>' +
      '<div id="dz-err"></div><div id="dz-ok"></div>' +
      '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:14px;align-items:flex-start">' +
      '<canvas id="dz-shape" style="border:1px solid var(--line);border-radius:10px;' +
      'background:var(--surface)"></canvas>' +
      '<div class="metrics" id="dz-info" style="flex:1;min-width:180px"></div></div>' +
      "</div></div>" +

      '<div class="panel hide" id="dz-step2"><h3>二、選行政區與距離</h3><div class="inner">' +
      '<label style="font-size:12px;color:var(--ink-3)">行政區</label>' +
      '<div id="dz-chips" style="margin:6px 0 14px"></div>' +
      '<label style="font-size:12px;color:var(--ink-3)">距離</label>' +
      '<select id="dz-level" style="width:100%;max-width:320px;padding:10px;border:1px solid var(--line);' +
      'border-radius:9px;background:var(--surface);color:var(--ink);margin-bottom:14px">' +
      '<option value="standard">標準大眾級 5–8 km</option>' +
      '<option value="challenge">挑戰級 10–15 km</option></select>' +
      '<div class="dl"><button class="btn primary" id="dz-run">開始規劃</button>' +
      '<span id="dz-stat" style="font-size:14px;color:var(--ink-2);align-self:center"></span></div>' +
      '<div class="trk" style="margin-top:12px"><i id="dz-prog" style="width:0%;' +
      'background:var(--route)"></i></div>' +
      '<p style="font-size:13px;color:var(--ink-3);margin:10px 0 0">' +
      "第一次選某個行政區要下載該區路網（0.3～1.7 MB），之後就不用再下載。</p>" +
      "</div></div>" +

      '<div id="dz-out"></div>' +
      "</div></main>";

    var ORDER = ["中心市區", "屯區", "近郊", "海線", "山城線", "其他"];
    /* 29 個行政區用標籤攤開沒人選得下去，改成下拉。
       單選為主；跨區比較保留成選單最上面的「整個區位一起比」，
       選了那個就一次排該區位的每一區再排名。 */
    function chips() {
      var by = {};
      Object.keys(d).forEach(function (k) {
        var g = d[k].group || "其他";
        (by[g] = by[g] || []).push(k);
      });
      var groups = ORDER.filter(function (g) { return by[g]; });
      var h = '<select id="dz-sel" class="fsel" style="width:100%;max-width:360px;' +
        'padding:11px 34px 11px 14px;border-radius:9px;font-size:15px">';
      h += '<optgroup label="比較多個區（每多一區約多 40 秒）">';
      groups.forEach(function (g) {
        h += '<option value="*' + esc(g) + '">' + esc(g) + " ── " + by[g].length +
          " 區一起比較</option>";
      });
      h += "</optgroup>";
      groups.forEach(function (g) {
        h += '<optgroup label="' + esc(g) + '">';
        by[g].forEach(function (k) {
          var no = d[k].recommended === false;
          h += '<option value="' + k + '"' + (SELD === k ? " selected" : "") + ">" +
            esc(d[k].name) + (no ? "（山區，不建議）" : "") + "</option>";
        });
        h += "</optgroup>";
      });
      $("dz-chips").innerHTML = h + "</select>" +
        '<div id="dz-dnote" style="font-size:13px;color:var(--ink-3);margin-top:8px"></div>';
      var sel = $("dz-sel");
      sel.value = SELD;
      sel.onchange = function () { SELD = sel.value; dnote(); };
      dnote();
    }
    function dnote() {
      var el = $("dz-dnote");
      if (!el) return;
      if (SELD.charAt(0) === "*") {
        var g = SELD.slice(1);
        var ks = Object.keys(d).filter(function (k) { return (d[k].group || "其他") === g; });
        el.textContent = "會排 " + ks.length + " 個區再依綜合評分排名，約 " +
          Math.round(ks.length * 0.7) + " 分鐘。";
      } else {
        var x = d[SELD];
        el.innerHTML = x ? (esc(x.traits) +
          (x.recommended === false
            ? '<br/><b style="color:var(--route)">全區山地，爬升遠超規格書上限，排出來的路線分數會很低。</b>'
            : "")) : "";
      }
    }
    chips();
    if (SHAPE) { $("dz-step2").classList.remove("hide"); }

    $("dz-trace").onclick = function () {
      var f = $("dz-file").files[0];
      $("dz-err").innerHTML = "";
      if (!f) {
        $("dz-err").innerHTML = '<div class="callout" style="margin:12px 0 0">' +
          "<b>還沒選圖檔</b><p>先按上面的欄位挑一張圖。</p></div>";
        return;
      }
      SHAPE_NAME = f.name.replace(/\.[^.]+$/, "");
      setBusy(true);
      $("dz-stat").textContent = "描圖中…";
      readImage(f, function (gray, w, h) {
        worker().postMessage({
          cmd: "trace", gray: gray, w: w, h: h,
          close: Math.max(0, Math.min(30, +$("dz-close").value || 6)),
          points: Math.max(20, Math.min(160, +$("dz-pts").value || 64))
        }, [gray.buffer]);
      });
    };
    $("dz-run").onclick = function () {
      var ks = SELD.charAt(0) === "*"
        ? Object.keys(d).filter(function (k) {
          return (d[k].group || "其他") === SELD.slice(1);
        })
        : [SELD];
      $("dz-err").innerHTML = "";
      if (!SHAPE) { $("dz-err").innerHTML = '<div class="callout"><b>還沒描圖</b></div>'; return; }
      if (!ks.length) {
        $("dz-err").innerHTML = '<div class="callout" style="margin:12px 0 0">' +
          "<b>還沒選行政區</b></div>";
        return;
      }
      setBusy(true);
      $("dz-out").innerHTML = "";
      $("dz-stat").textContent = "準備中…";
      worker().postMessage({
        cmd: "plan", districts: ks, shape: SHAPE, shapeName: SHAPE_NAME,
        level: $("dz-level").value
      });
    };
    window.scrollTo({ top: 0 });
  };
})();

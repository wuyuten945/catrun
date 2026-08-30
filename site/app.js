/* 貓行台中 — 純前端，資料是離線先算好的 JSON。
   用 hash 路由：靜態主機不必設任何 rewrite 規則就能直接開子頁與分享網址。 */
(function () {
  "use strict";

  var DIMS = ["shape", "safety", "distance", "district", "logistics"];
  var SHORT = {
    shape: "圖形還原", safety: "道路安全", distance: "里程難度",
    district: "區域特性", logistics: "補給交通"
  };
  var IDX = null, SHAPES = null, CACHE = {};
  var F = { district: "", shape: "", level: "", sort: "total" };
  var view = document.getElementById("view");

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function hue(v) { return v >= 80 ? "var(--s-hi)" : v >= 62 ? "var(--s-mid)" : "var(--s-lo)"; }
  function bar(v) {
    return '<div class="trk"><i style="width:' + Math.max(2, v) +
      "%;background:" + hue(v) + '"></i></div>';
  }
  function get(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) { throw new Error(url + " 讀取失敗（" + r.status + "）"); }
      return r.json();
    });
  }
  function shapeName(k) { return (SHAPES && SHAPES[k]) ? SHAPES[k].name : k; }

  /* ── 路線庫 ── */
  function renderList() {
    var d = IDX.districts, lv = IDX.levels;
    function chips(key, items, allLabel) {
      // allLabel 給空字串＝這一組沒有「全部」選項（排序就是這種，一定要選一個）
      return (allLabel ? '<button class="chip" data-f="' + key +
        '" data-v="" aria-pressed="' + (F[key] === "" ? "true" : "false") + '">' +
        allLabel + "</button>" : "") +
        items.map(function (it) {
          return '<button class="chip" data-f="' + key + '" data-v="' + it[0] +
            '" aria-pressed="' + (F[key] === it[0] ? "true" : "false") + '">' +
            esc(it[1]) + "</button>";
        }).join("");
    }
    var rows = IDX.routes.filter(function (r) {
      return (!F.district || r.district === F.district)
        && (!F.shape || r.shape === F.shape)
        && (!F.level || r.level === F.level);
    });
    if (F.sort === "km") { rows = rows.slice().sort(function (a, b) { return a.km - b.km; }); }
    else if (F.sort === "shape") {
      rows = rows.slice().sort(function (a, b) { return b.parts.shape - a.parts.shape; });
    } else if (F.sort === "safety") {
      rows = rows.slice().sort(function (a, b) { return b.parts.safety - a.parts.safety; });
    }

    var totalKm = IDX.routes.reduce(function (a, r) { return a + r.km; }, 0);
    view.innerHTML =
      '<div class="hero"><div class="wrap">' +
      "<h1>把貓畫在台中的街道上</h1>" +
      "<p>每一條都是封閉環狀路線，每個轉折都落在真的走得到的路口上。" +
      "挑一條、下載 GPX，跑完打開軌跡就是一隻貓。</p>" +
      '<div class="stats">' +
      [[IDX.routes.length, "條路線"],
      [Object.keys(IDX.districts).length, "個行政區"],
      [Object.keys(SHAPES || {}).length, "種貓形"],
      [totalKm.toFixed(0) + " km", "累計里程"]].map(function (s) {
        return '<div class="stat"><b>' + s[0] + "</b><span>" + s[1] + "</span></div>";
      }).join("") +
      "</div></div></div>" +

      '<div class="filters"><div class="wrap">' +
      '<div class="frow"><span class="flabel">行政區</span>' +
      chips("district", Object.keys(d).map(function (k) { return [k, d[k].name]; }), "全部") +
      "</div>" +
      '<div class="frow"><span class="flabel">貓形</span>' +
      chips("shape", Object.keys(SHAPES || {}).map(function (k) {
        return [k, SHAPES[k].name];
      }), "全部") + "</div>" +
      '<div class="frow"><span class="flabel">距離</span>' +
      chips("level", Object.keys(lv).map(function (k) {
        return [k, lv[k].min + "–" + lv[k].max + " km"];
      }), "全部") +
      '<span class="count">' + rows.length + " 條</span></div>" +
      '<div class="frow"><span class="flabel">排序</span>' +
      chips("sort", [["total", "綜合評分"], ["shape", "最像貓"],
      ["safety", "最好跑"], ["km", "距離短→長"]], "") +
      "</div></div></div>" +

      '<main><div class="wrap">' +
      (rows.length ? '<div class="grid">' + rows.map(card).join("") + "</div>"
        : '<div class="empty">這個組合沒有路線。試著放寬篩選條件。</div>') +
      "</div></main>";

    Array.prototype.forEach.call(view.querySelectorAll(".chip"), function (b) {
      b.addEventListener("click", function () {
        F[b.dataset.f] = b.dataset.v;
        renderList();
        window.scrollTo({ top: 0 });
      });
    });
  }

  function card(r) {
    return '<a class="card" href="#/r/' + r.slug + '">' +
      '<figure><img loading="lazy" alt="' + esc(r.title) + '路線圖" src="img/' +
      r.slug + '.jpg"/></figure>' +
      '<div class="body"><h3>' + esc(IDX.districts[r.district].name) + " ・ " +
      esc(shapeName(r.shape)) + "</h3>" +
      '<p class="sub">' + esc(r.mark ? "行經 " + r.mark : IDX.districts[r.district].style) +
      "</p>" +
      '<div class="foot"><span class="km">' + r.km.toFixed(2) + " km</span>" +
      '<span class="lvl">' + esc(IDX.levels[r.level].label.replace(/（.*/, "")) + "</span>" +
      '<span class="sc" style="color:' + hue(r.total) + '">' + r.total.toFixed(1) +
      "<s>分</s></span></div></div></a>";
  }

  /* ── 單條路線 ── */
  function renderRoute(slug) {
    var r = null;
    IDX.routes.forEach(function (x) { if (x.slug === slug) { r = x; } });
    if (!r) { view.innerHTML = notFound(); return; }
    view.innerHTML = '<main><div class="wrap"><p>載入中…</p></div></main>';
    var p = CACHE[slug] ? Promise.resolve(CACHE[slug]) : get("data/route/" + slug + ".json");
    p.then(function (det) {
      CACHE[slug] = det;
      paintRoute(r, det);
    }).catch(function (e) {
      view.innerHTML = '<main><div class="wrap"><p>' + esc(e.message) + "</p></div></main>";
    });
  }

  function paintRoute(r, det) {
    var dims = DIMS.map(function (k) {
      return '<div class="dim"><div class="nm">' + esc(IDX.labels[k]) +
        "<em>" + Math.round(IDX.weights[k] * 100) + "%</em></div>" +
        '<div class="vl" style="color:' + hue(r.parts[k]) + '">' +
        r.parts[k].toFixed(1) + "</div>" + bar(r.parts[k]) + "</div>";
    }).join("");
    var mets = [];
    Object.keys(det.detail).forEach(function (k) {
      var dd = det.detail[k];
      Object.keys(dd).forEach(function (a) {
        mets.push('<span class="metric">' + esc(a) + " <b>" + esc(dd[a]) + "</b></span>");
      });
    });
    var cues = '<div class="tbl-wrap"><table><tr><th>#</th><th>累計 km</th>' +
      "<th>動作</th><th>路名</th><th>該段</th></tr>" +
      det.cues.map(function (c) {
        return '<tr><td class="n">' + c.seq + '</td><td class="n">' + c.km.toFixed(2) +
          "</td><td>" + esc(c.action) + "</td><td>" + esc(c.road) +
          '</td><td class="n">' + c.len_m + " m</td></tr>";
      }).join("") + "</table></div>";
    var notes = det.notes.length
      ? '<div class="tbl-wrap"><table><tr><th>累計 km</th><th>類別</th><th>提醒</th></tr>' +
      det.notes.map(function (n) {
        return '<tr><td class="n">' + n.km.toFixed(2) + '</td><td class="note-type">' +
          esc(n.type) + "</td><td>" + esc(n.text) + "</td></tr>";
      }).join("") + "</table></div>"
      : "<p>沿線未偵測到需特別標記的路段。仍請遵守號誌、注意來車。</p>";
    var marks = det.marks.length
      ? '<div class="tbl-wrap"><table><tr><th>累計 km</th><th>地標</th><th>距路線</th></tr>' +
      det.marks.map(function (m) {
        return '<tr><td class="n">' + m[1].toFixed(2) + "</td><td>" + esc(m[2]) +
          '</td><td class="n">' + m[0] + " m</td></tr>";
      }).join("") + "</table></div>"
      : "<p>沿線 180 公尺內沒有具名地標。</p>";

    view.innerHTML = '<main><div class="wrap">' +
      '<a class="back" href="#/">← 回路線庫</a>' +
      '<div class="detail-head"><h2>' + esc(r.title) + "</h2>" +
      '<div class="big-score" style="color:' + hue(r.total) + '">' +
      r.total.toFixed(1) + "<s>/100</s></div></div>" +

      '<div class="panel mapbox"><img alt="' + esc(r.title) +
      '路線圖" src="img/' + r.slug + '.jpg"/></div>' +

      '<div class="panel"><div class="inner"><div class="kv">' +
      '<div><span>里程</span><b>' + r.km.toFixed(2) + " km</b></div>" +
      '<div><span>級距</span><b style="font-size:14px">' +
      esc(IDX.levels[r.level].label) + "</b></div>" +
      '<div><span>累計爬升</span><b>' +
      (r.climb == null ? "未取得" : r.climb.toFixed(0) + " m") + "</b></div>" +
      '<div><span>起終點</span><b style="font-size:13px">' +
      r.start[0].toFixed(5) + ", " + r.start[1].toFixed(5) + "</b></div>" +
      "</div></div></div>" +

      '<div class="panel"><h3>下載</h3><div class="inner"><div class="dl">' +
      '<a class="btn primary" href="gpx/' + r.slug + '.gpx" download>GPX 軌跡</a>' +
      '<a class="btn" href="kml/' + r.slug + '.kml" download>KML（Google Earth）</a>' +
      '<a class="btn" target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=' +
      r.start[0] + "," + r.start[1] + '">在地圖上看起點</a>' +
      "</div></div></div>" +

      '<div class="panel"><h3>評分（依《台中市圖形路跑路線規劃規格書》）</h3>' +
      '<div class="inner"><div class="dims">' + dims + "</div>" +
      '<div class="metrics" style="margin-top:16px">' + mets.join("") + "</div>" +
      "</div></div>" +

      '<div class="panel"><h3>路名循序導航表（' + det.cues.length + " 段）</h3>" +
      '<div class="inner">' + cues + "</div></div>" +

      '<div class="panel"><h3>安全提醒</h3><div class="inner">' + notes + "</div></div>" +
      '<div class="panel"><h3>地標打卡點</h3><div class="inner">' + marks + "</div></div>" +
      "</div></main>";
    window.scrollTo({ top: 0 });
  }

  /* ── 圖形庫 ── */
  function renderShapes() {
    view.innerHTML = '<main><div class="wrap">' +
      '<h2 style="font-family:\'Noto Serif TC\',serif;font-size:27px;margin:8px 0 6px">圖形庫</h2>' +
      '<p style="color:var(--ink-2);max-width:66ch;margin:0 0 22px">' +
      "五種貓，都是正規化到單位方框的封閉折線。其中「手繪蹲坐貓」是把一張手稿" +
      "二值化、補起筆畫缺口、灌水找出封閉區域後自動描出來的，沒有人工重畫。</p>" +
      '<div class="gallery">' + Object.keys(SHAPES).map(function (k) {
        var s = SHAPES[k];
        var pts = s.pts.map(function (p) {
          return (50 + p[0] * 88).toFixed(2) + "," + (50 - p[1] * 88).toFixed(2);
        }).join(" ");
        return '<div class="shape"><svg viewBox="0 0 100 100" role="img" aria-label="' +
          esc(s.name) + '"><polyline points="' + pts + '" fill="none" ' +
          'stroke="var(--route)" stroke-width="1.8" stroke-linejoin="round" ' +
          'stroke-linecap="round"/></svg><h4>' + esc(s.name) + "</h4><p>" +
          esc(s.note) + "</p></div>";
      }).join("") + "</div></div></main>";
    window.scrollTo({ top: 0 });
  }

  /* ── 關於 ── */
  function renderAbout() {
    view.innerHTML = '<main><div class="wrap prose">' +
      '<h2 style="font-family:\'Noto Serif TC\',serif;font-size:27px;margin:8px 0 14px">關於這個網站</h2>' +
      "<p>這裡的每一條路線都不是把圖畫在地圖上，而是<b>把圖形的每個頂點吸附到最近的真實路口</b>，" +
      "再用最短路徑把相鄰路口接起來。所以產出的每一公尺都真的走得到，不會穿過建築物或私人土地。</p>" +

      '<div class="callout"><b>上路前請先看這一段</b>' +
      "<p>路線是依 OpenStreetMap 的資料自動規劃的，<b>沒有經過現場逐段實勘</b>。" +
      "施工、改道、單行、封閉巷弄都可能與資料不符。請把它當成路線建議而不是導航指令：" +
      "遵守號誌與交通規則、注意來車、夜間穿著反光衣物，遇到走不通就繞開。" +
      "自身安全請自行負責。</p></div>" +

      "<h3>怎麼評分</h3>" +
      "<p>依《台中市圖形路跑路線規劃規格書》的五個構面加權：圖形還原度 30%、道路安全 25%、" +
      "里程難度 20%、區域特性 15%、補給交通 10%。每條路線的原始指標都攤開在明細頁，" +
      "不是只給一個總分。</p>" +

      "<h3>危險的路會自動被繞開</h3>" +
      "<p>每條路段依道路分級、有無人行道、車道數算一個安全分數，路徑成本 ＝ 長度 ×(1＋1.6×(1−安全分))。" +
      "<b>危險的路在演算法眼裡比較「長」</b>，最短路徑自然會避開。綠園道、河濱步道、" +
      "柳川綠川筏子溪一律優先採用。</p>" +

      "<h3>安全分數為什麼普遍不高</h3>" +
      "<p>規格書訂「每公里主要路口停等 ≤1.5 次」，台中市區實測是 3～5 次，市區路線幾乎必然超標。" +
      "這是規格與現實的落差，不是路線畫壞了。想跑得順，優先挑安全分數高的那幾條。</p>" +

      "<h3>資料與限制</h3>" +
      "<p>路網、號誌、便利商店、公廁、大眾運輸與地標來自 <b>OpenStreetMap</b>（ODbL 授權）；" +
      "累計爬升取自 Open-Meteo 的 90 公尺網格高程，並以 5 公尺門檻過濾雜訊。" +
      "巷弄的人行道與照明標記在 OSM 上相當稀疏，安全提醒只能視為初篩。" +
      "旗艦級 21 公里因為單一行政區畫不下，本版未收。</p>" +

      "<h3>授權</h3>" +
      "<p>路線資料與 GPX 檔可自由下載使用。若對外轉載，請保留 OpenStreetMap 出處標示。</p>" +
      "</div></main>";
    window.scrollTo({ top: 0 });
  }

  function notFound() {
    return '<main><div class="wrap"><div class="empty">找不到這條路線。' +
      '<br/><a class="btn" style="margin-top:14px" href="#/">回路線庫</a></div></div></main>';
  }

  function route() {
    var h = location.hash.replace(/^#/, "") || "/";
    if (h.indexOf("/r/") === 0) { renderRoute(h.slice(3)); }
    else if (h === "/shapes") { renderShapes(); }
    else if (h === "/about") { renderAbout(); }
    else { renderList(); }
  }

  view.innerHTML = '<main><div class="wrap"><p>載入路線庫…</p></div></main>';
  Promise.all([get("data/index.json"), get("data/shapes.json")])
    .then(function (res) {
      IDX = res[0]; SHAPES = res[1];
      var g = document.getElementById("gen");
      if (g) { g.textContent = "資料產生於 " + IDX.generated; }
      window.addEventListener("hashchange", route);
      route();
    })
    .catch(function (e) {
      view.innerHTML = '<main><div class="wrap"><div class="empty">' +
        esc(e.message) + "</div></div></main>";
    });
})();

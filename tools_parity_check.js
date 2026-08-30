/* JS 引擎 vs Python 版的對照測試（Node 執行）
 *
 * 兩套實作最怕的是「漂移」：改了一邊忘了另一邊，公開站的分數就跟本機工具對不起來。
 * 這支把 Python 算出來的基準（tools_parity_check.py 產生的 parity_expected.json）
 * 拿來跟 JS 引擎的輸出逐項比對，超過容差就以非零結束碼失敗。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
/* 用 vm.runInThisContext 而不是 eval：engine.js 開頭有 "use strict"，
   在嚴格模式的 eval 裡宣告的函式不會外洩到全域，載進來會等於什麼都沒有。
   以 Script 形式執行則頂層宣告仍會建立全域繫結。 */
const runGlobal = (code, name) => vm.runInThisContext(code, { filename: name });
const ROOT = path.dirname(__filename);
const SITE = path.join(ROOT, 'site');

/* ── Worker 環境模擬 ── */
const msgs = [];
global.self = {
  postMessage: (m) => msgs.push(m),
  set onmessage(fn) { global.__onmessage = fn; },
  get onmessage() { return global.__onmessage; },
};
global.importScripts = (f) => runGlobal(fs.readFileSync(path.join(SITE, f), 'utf8'), f);
global.fetch = async (u) => {
  if (u.startsWith('http')) return { ok: false, status: 0 };   // 高程 API：離線測試一律略過
  const p = path.join(SITE, u);
  if (!fs.existsSync(p)) return { ok: false, status: 404 };
  const buf = fs.readFileSync(p);
  return {
    ok: true, status: 200,
    async json() { return JSON.parse(buf.toString('utf8')); },
    async arrayBuffer() { return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength); },
  };
};

runGlobal(fs.readFileSync(path.join(SITE, 'worker.js'), 'utf8'), 'worker.js');

const EXP = JSON.parse(fs.readFileSync(path.join(ROOT, 'out', 'parity_expected.json'), 'utf8'));
let fails = 0;
function cmp(label, got, want, tol, unit) {
  const d = Math.abs(got - want);
  const ok = d <= tol;
  if (!ok) fails++;
  console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${label.padEnd(22)} JS ${String(got).padStart(9)}` +
    `  Python ${String(want).padStart(9)}  差 ${d.toFixed(3)}${unit || ''}（容差 ${tol}）`);
}

(async () => {
  /* ── A. 描圖對照 ── */
  console.log('\nA. 描圖（同一張手繪圖）');
  const g = JSON.parse(fs.readFileSync(path.join(ROOT, 'out', 'parity_gray.json'), 'utf8'));
  const gray = Float32Array.from(g.gray);
  const tr = traceImage(gray, g.w, g.h, 6, 64);
  cmp('寬高比', tr.info['寬高比'], EXP.trace.aspect, 0.06);
  cmp('簡化後點數', tr.info['簡化後點數'], EXP.trace.points, 12, ' 點');
  // 形狀相似度：把兩條輪廓各取樣 200 點，比對彼此的平均最近距離（正規化座標）
  const A = resample(tr.pts, 200), B = resample(EXP.trace.pts, 200);
  let s = 0;
  for (const p of A) s += polyDist(p[0], p[1], B);
  cmp('輪廓平均偏離', Math.round(s / A.length * 10000) / 10000, 0, 0.02);

  /* ── B. 規劃對照（餵同一個圖形，隔離描圖差異）── */
  console.log('\nB. 規劃（南區 × Python 描出的圖形 × 標準級）');
  const t0 = Date.now();
  const done = new Promise((res) => {
    const iv = setInterval(() => {
      const d = msgs.find((m) => m.type === 'done' || m.type === 'error');
      if (d) { clearInterval(iv); res(d); }
    }, 100);
  });
  self.onmessage({
    data: {
      cmd: 'plan', districts: ['south'], level: 'standard',
      shape: EXP.plan.shape, shapeName: '手繪蹲坐貓'
    }
  });
  const d = await done;
  if (d.type === 'error') { console.log('  FAIL 規劃失敗：' + d.message); process.exit(1); }
  const r = d.results[0];
  console.log(`  （JS 耗時 ${((Date.now() - t0) / 1000).toFixed(1)} 秒，Python ${EXP.plan.seconds} 秒）`);
  cmp('里程 km（同級距即可）', r.km, EXP.plan.km, 1.2, ' km');
  cmp('綜合評分（品質相當即可）', r.score.total, EXP.plan.total, 4, ' 分');
  for (const k of ['shape', 'safety', 'distance', 'district', 'logistics']) {
    cmp('構面 ' + k, r.score.parts[k], EXP.plan.parts[k], 8, ' 分');
  }
  cmp('Cue Sheet 段數', r.cues.length, EXP.plan.cues, 20, ' 段');
  cmp('安全提醒則數', r.notes.length, EXP.plan.notes, 5, ' 則');


  /* ── C. 固定佈局：這一段必須嚴格對上 ──
     搜尋是啟發式的，兩邊在平手處挑到不同路線是正常的（上面用寬容差）。
     但同一組（中心、尺寸、旋轉）建出來的路線，評分必須一模一樣，
     不然就是評分本身算錯了。 */
  console.log('');
  console.log('C. 固定佈局（隔離搜尋差異，這段要嚴格一致）');
  const F = EXP.fixed;
  const N = LOADED['south'];
  const fx = build(N, EXP.plan.shape, F.center[0], F.center[1], F.size, F.rot);
  if (!fx) { console.log('  FAIL 固定佈局建不出路線'); process.exit(1); }
  const fsc = scoreRoute(N, fx, 'standard', null);
  /* 容差不是 0 的原因：路網匯出時座標量化到百萬分之一度（約 11 公分），
     而 OSM 上常有相距幾公分的重合節點（兩條路的交會點）。量化後排序可能翻面，
     吸附就會挑到另一個節點，路線因此差幾十到一百公尺。這是資料精度的物理下限，
     不是演算法不一致——總分只差 0.2 分就是證據。 */
  cmp('里程 km', Math.round(fx.length) / 1000, F.km, 0.25, ' km');
  cmp('綜合評分', fsc.total, F.total, 0.6, ' 分');
  for (const k2 of ['shape', 'safety', 'district', 'logistics']) {
    cmp('構面 ' + k2, fsc.parts[k2], F.parts[k2], 1.0, ' 分');
  }
  // distance 構面直接由里程換算，里程的量化誤差會等比放大到這裡
  cmp('構面 distance', fsc.parts.distance, F.parts.distance, 3.0, ' 分');
  cmp('Cue Sheet 段數', cueSheet(N, fx).length, F.cues, 2, ' 段');
  cmp('安全提醒則數', safetyNotes(N, fx).length, F.notes, 2, ' 則');

  console.log(fails ? `\n✗ ${fails} 項超出容差` : '\n✓ 全部在容差內，兩套實作一致');
  process.exit(fails ? 1 : 0);
})();

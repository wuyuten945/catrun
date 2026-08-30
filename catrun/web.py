# -*- coding: utf-8 -*-
"""本機網頁介面。

不用線上圖磚：路網底圖是自己畫的，沒網路（或 Overpass 快取已在）照樣看得到結果。
連接埠 5008——5000~5002 被 SSS/MSS 佔用、5005 是 PerfMon，避開。
"""
import io
import json
import os
import threading
import traceback

from flask import Flask, Response, jsonify, request, send_file

from .config import DISTRICTS, LEVELS
from .planner import OUT, plan
from .scoring import LABEL
from .shapes import SHAPES

app = Flask(__name__)
JOBS = {}
LOCK = threading.Lock()

PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>圖形路跑路線規劃系統</title><style>
*{box-sizing:border-box}
body{margin:0;background:#eef1f6;color:#1a2230;
 font-family:"Microsoft JhengHei","PingFang TC",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:18px 14px 60px}
h1{font-size:26px;margin:6px 0 2px}
.sub{color:#5a6a7a;font-size:14px;margin-bottom:16px}
.tile{background:#fff;border:1px solid #dde3ee;border-radius:14px;padding:16px;
 margin-bottom:14px;box-shadow:0 1px 3px rgba(20,30,60,.05)}
.tile h2{font-size:18px;margin:0 0 12px;color:#1a2230}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
label{display:block;font-size:13px;color:#5a6a7a;margin-bottom:5px}
select,input{width:100%;padding:11px;border:1px solid #cfd7e6;border-radius:9px;
 font-size:15px;background:#fff;color:#1a2230}
button{padding:13px 20px;border:0;border-radius:10px;font-size:16px;font-weight:700;
 cursor:pointer;background:#d6001c;color:#fff}
button.ghost{background:#fff;color:#1a2230;border:1px solid #cfd7e6;font-weight:600}
button:disabled{opacity:.5;cursor:default}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:14px}
.log{background:#101722;color:#cfe3ff;border-radius:10px;padding:12px;font-size:13px;
 font-family:Consolas,monospace;max-height:210px;overflow:auto;white-space:pre-wrap}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{border-bottom:1px solid #e6ebf3;padding:7px 6px;text-align:left;color:#1a2230}
th{color:#5a6a7a;font-weight:600;font-size:13px}
.big{font-size:40px;font-weight:800;color:#d6001c;line-height:1}
.bar{height:9px;background:#e9edf4;border-radius:5px;overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;background:#2f6fed}
.chip{display:inline-block;background:#eef2fb;border:1px solid #d7e0f0;border-radius:999px;
 padding:3px 11px;font-size:13px;margin:2px 4px 2px 0;color:#1a2230}
img{max-width:100%;border-radius:10px;border:1px solid #dde3ee}
.hintbox{background:#fff7e6;border:1px solid #f0d9a8;border-radius:10px;padding:10px;
 font-size:13px;color:#6a4a10;margin-top:10px}
.scroll{overflow-x:auto}
a.dl{display:inline-block;margin:4px 8px 4px 0;padding:9px 14px;border-radius:9px;
 background:#f2f5fb;border:1px solid #d7e0f0;color:#1a2230;text-decoration:none;font-size:14px}
</style></head><body><div class="wrap">
<h1>台中市圖形路跑路線規劃系統</h1>
<div class="sub">依《台中市圖形路跑路線規劃規格書》建置 ・ 路網來源 OpenStreetMap</div>

<div class="tile"><h2>規劃條件</h2>
<div class="grid">
 <div><label>行政區</label><select id="d"></select></div>
 <div><label>圖形</label><select id="s"></select></div>
 <div><label>里程級距</label><select id="l"></select></div>
 <div><label>指定中心（選填，lat,lng）</label><input id="c" placeholder="自動搜尋"/></div>
</div>
<div class="row"><button id="go">開始規劃</button>
 <button class="ghost" id="reload">重抓 OSM 路網</button>
 <span id="st" style="color:#5a6a7a;font-size:14px"></span></div>
<div class="hintbox" id="note"></div>
</div>

<div class="tile" id="logtile" style="display:none"><h2>執行紀錄</h2><div class="log" id="log"></div></div>
<div id="result"></div>
</div><script>
var META=null, TIMER=null;
function $(i){return document.getElementById(i)}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){
 return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
fetch("/api/meta").then(function(r){return r.json()}).then(function(m){
 META=m;
 var d=$("d"); m.districts.forEach(function(x){
  var o=document.createElement("option"); o.value=x.key; o.textContent=x.name+"（"+x.style+"）"; d.appendChild(o)});
 var s=$("s"); m.shapes.forEach(function(x){
  var o=document.createElement("option"); o.value=x.key; o.textContent=x.name; s.appendChild(o)});
 var l=$("l"); m.levels.forEach(function(x){
  var o=document.createElement("option"); o.value=x.key; o.textContent=x.label+" "+x.min+"–"+x.max+" km"; l.appendChild(o)});
 d.onchange=s.onchange=note; note();
});
function note(){
 if(!META)return;
 var d=META.districts.filter(function(x){return x.key===$("d").value})[0]||{};
 var s=META.shapes.filter(function(x){return x.key===$("s").value})[0]||{};
 var ok=(s.fits||[]).indexOf(d.key)>=0;
 $("note").innerHTML="<b>"+esc(d.name)+"</b>："+esc(d.traits)+"<br/><b>"+esc(s.name)+"</b>："+esc(s.note)
  +"<br/>"+(ok?"這個組合符合規格書 2-4 的區域風格建議。"
              :"⚠ 規格書 2-4 建議這一區走「"+esc(d.style)+"」，此圖形不在建議清單內，仍可規劃但區域特性分數會偏低。");
}
$("go").onclick=function(){run(false)};
$("reload").onclick=function(){run(true)};
function run(refresh){
 $("go").disabled=$("reload").disabled=true;
 $("st").textContent="規劃中…第一次跑某個行政區要下載路網，約 1~2 分鐘";
 $("logtile").style.display=""; $("log").textContent=""; $("result").innerHTML="";
 fetch("/api/plan",{method:"POST",headers:{"Content-Type":"application/json"},
  body:JSON.stringify({district:$("d").value,shape:$("s").value,level:$("l").value,
   center:$("c").value,refresh:refresh})})
  .then(function(r){return r.json()}).then(function(j){poll(j.job)});
}
function poll(job){
 clearInterval(TIMER);
 TIMER=setInterval(function(){
  fetch("/api/job/"+job).then(function(r){return r.json()}).then(function(j){
   $("log").textContent=(j.log||[]).join("\\n"); $("log").scrollTop=1e6;
   if(j.state==="done"){clearInterval(TIMER);done(j)}
   else if(j.state==="error"){clearInterval(TIMER);
    $("st").textContent="失敗"; $("go").disabled=$("reload").disabled=false;
    $("result").innerHTML='<div class="tile"><h2>失敗</h2><pre style="white-space:pre-wrap">'
      +esc(j.error)+'</pre></div>'}
  })},900);
}
function done(j){
 $("st").textContent="完成"; $("go").disabled=$("reload").disabled=false;
 var r=j.result, sc=r.score, h="";
 h+='<div class="tile"><h2>綜合評分</h2><div class="grid">';
 h+='<div><div class="big">'+sc.total+'</div><div class="sub">／ 100 分</div></div>';
 for(var k in sc.parts){
  h+='<div><label>'+esc(r.labels[k])+'（權重 '+sc.weights[k]+'%）</label>'
   +'<b style="font-size:20px">'+sc.parts[k]+'</b>'
   +'<div class="bar"><i style="width:'+sc.parts[k]+'%"></i></div></div>';
 }
 h+='</div>';
 for(var k in sc.detail){
  h+='<div style="margin-top:12px"><b>'+esc(r.labels[k])+'</b><div>';
  var dd=sc.detail[k];
  for(var a in dd){h+='<span class="chip">'+esc(a)+'：'+esc(dd[a])+'</span>'}
  h+='</div></div>';
 }
 h+='</div>';
 h+='<div class="tile"><h2>路線圖</h2><img src="/api/img/'+j.id+'?t='+Date.now()+'"/>';
 h+='<div style="margin-top:10px">';
 ["gpx","kml","csv","md","png"].forEach(function(x){
  h+='<a class="dl" href="/api/file/'+j.id+'/'+x+'">下載 '+x.toUpperCase()+'</a>'});
 h+='</div></div>';
 h+='<div class="tile"><h2>路名循序導航表（Cue Sheet）</h2><div class="scroll"><table>'
  +'<tr><th>#</th><th>累計 km</th><th>動作</th><th>路名</th><th>該段</th></tr>';
 r.cues.forEach(function(c){h+='<tr><td>'+c.seq+'</td><td>'+c.km.toFixed(2)+'</td><td>'
  +esc(c.action)+'</td><td>'+esc(c.road)+'</td><td>'+c.len_m+' m</td></tr>'});
 h+='</table></div></div>';
 h+='<div class="tile"><h2>安全提醒</h2>';
 if(r.notes.length){h+='<div class="scroll"><table><tr><th>累計 km</th><th>類別</th><th>提醒</th></tr>';
  r.notes.forEach(function(n){h+='<tr><td>'+n.km.toFixed(2)+'</td><td>'+esc(n.type)
   +'</td><td>'+esc(n.text)+'</td></tr>'}); h+='</table></div>'}
 else{h+='<div class="sub">沿線未偵測到需特別標記的路段。</div>'}
 h+='</div>';
 h+='<div class="tile"><h2>地標打卡點</h2>';
 if(r.marks.length){h+='<div class="scroll"><table><tr><th>累計 km</th><th>地標</th><th>類型</th><th>距路線</th></tr>';
  r.marks.slice(0,3).forEach(function(m){h+='<tr><td>'+m[1].toFixed(2)+'</td><td>'+esc(m[2])
   +'</td><td>'+esc(m[3])+'</td><td>'+m[0]+' m</td></tr>'}); h+='</table></div>'}
 else{h+='<div class="sub">沿線 180 公尺內沒有具名地標，建議手動指定。</div>'}
 h+='</div>';
 $("result").innerHTML=h;
}
</script></body></html>"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html; charset=utf-8")


@app.route("/api/meta")
def meta():
    return jsonify({
        "districts": [{"key": k, "name": v["name"], "style": v["style"],
                       "traits": v["traits"]} for k, v in DISTRICTS.items()],
        "shapes": [{"key": k, "name": v["name"], "note": v["note"],
                    "fits": list(v["fits"])} for k, v in SHAPES.items()],
        "levels": [{"key": k, "label": v["label"], "min": v["min_km"],
                    "max": v["max_km"]} for k, v in LEVELS.items()],
    })


def _run(job, body):
    def log(*a):
        with LOCK:
            JOBS[job]["log"].append(" ".join(str(x) for x in a))
    try:
        c = None
        if (body.get("center") or "").strip():
            lat, lng = body["center"].split(",")
            c = (float(lat), float(lng))
        r = plan(body["district"], body["shape"], body.get("level", "standard"),
                 refresh=bool(body.get("refresh")), log=log, center=c)
        rid = "%s_%s_%s" % (body["district"], body["shape"], body.get("level", "standard"))
        with LOCK:
            JOBS[job].update(state="done", id=rid, result={
                "meta": r["meta"], "score": r["score"], "cues": r["cues"],
                "notes": r["notes"],
                "marks": [[m[0], m[1], m[2], m[3]] for m in r["marks"]],
                "labels": LABEL,
            })
    except Exception as e:      # noqa - 把錯誤送回畫面，不要只留在終端機
        with LOCK:
            JOBS[job].update(state="error",
                             error="%s\n%s" % (e, traceback.format_exc()))


@app.route("/api/plan", methods=["POST"])
def api_plan():
    body = request.get_json(force=True)
    job = os.urandom(6).hex()
    with LOCK:
        JOBS[job] = {"state": "run", "log": []}
    threading.Thread(target=_run, args=(job, body), daemon=True).start()
    return jsonify({"job": job})


@app.route("/api/job/<job>")
def api_job(job):
    with LOCK:
        j = dict(JOBS.get(job) or {"state": "missing"})
    return jsonify(j)


@app.route("/api/img/<rid>")
def api_img(rid):
    return send_file(os.path.join(OUT, rid + "_地圖.png"), mimetype="image/png")


@app.route("/api/file/<rid>/<ext>")
def api_file(rid, ext):
    names = {"gpx": rid + ".gpx", "kml": rid + ".kml", "csv": rid + "_cue.csv",
             "md": rid + "_報告.md", "png": rid + "_地圖.png"}
    if ext not in names:
        return "不支援的格式", 400
    return send_file(os.path.join(OUT, names[ext]), as_attachment=True)


def main(port=5008):
    print("圖形路跑路線規劃系統：http://127.0.0.1:%d" % port)
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()

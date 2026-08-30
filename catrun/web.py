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
from .planner import OUT, plan, plan_best
from .scoring import LABEL
from . import shapes
from .shapes import SHAPES

app = Flask(__name__)
JOBS = {}
LOCK = threading.Lock()

# 介面 HTML 抽成獨立檔：那一坨字串已經大到不好維護，放在 .html 才有語法著色。
# 每次請求重讀，改完存檔重整瀏覽器就看得到，本機工具不必為此重啟。
UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")


def page():
    with open(UI, encoding="utf-8") as f:
        return f.read()


@app.route("/")
def index():
    return Response(page(), mimetype="text/html; charset=utf-8")


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


def _slugify(name):
    """圖形名稱 → 檔名安全的代號。

    名稱可能整串是中文、也可能是瀏覽器送來的怪字元。取不出可用字元時，
    退回名稱的雜湊而不是固定字串——固定字串會讓兩次不同的上傳互相覆蓋。
    """
    import hashlib
    import re
    s = re.sub(r"[^0-9A-Za-z一-鿿]+", "_", (name or "").strip()).strip("_")
    if not s:
        s = hashlib.sha1((name or "shape").encode("utf-8")).hexdigest()[:8]
    return "user_" + s[:40]


@app.route("/api/trace", methods=["POST"])
def api_trace():
    """收一張手繪圖，描出輪廓並登錄成圖形。

    先把描圖結果回給畫面確認，是因為手繪照片千變萬化——線沒接好、拍歪、
    背景有陰影，描出來可能完全不是那隻貓。讓人先看一眼再決定要不要用。
    """
    import base64
    import io as _io
    import json as _json
    from .trace import trace

    f = request.files.get("image")
    if f is None:
        return jsonify({"error": "沒有收到圖檔"}), 400
    raw = f.read()
    if not raw:
        return jsonify({"error": "圖檔是空的"}), 400
    if len(raw) > 12 * 1024 * 1024:
        return jsonify({"error": "圖檔超過 12 MB，請先縮小再上傳"}), 400
    name = (request.form.get("name") or "").strip() or os.path.splitext(
        f.filename or "我的圖形")[0]
    try:
        close_px = max(0, min(30, int(request.form.get("close") or 6)))
        points = max(20, min(160, int(request.form.get("points") or 64)))
    except ValueError:
        return jsonify({"error": "參數要是數字"}), 400

    tmp = os.path.join(OUT, "_upload_%s" % (f.filename or "img"))
    with open(tmp, "wb") as fh:
        fh.write(raw)
    try:
        pts, info = trace(tmp, close_px=close_px, target_pts=points)
    except Exception as e:      # noqa - 描不出來要講原因，不要丟 500
        return jsonify({"error": "描不出封閉輪廓：%s。試著把「缺口補償」調大，"
                                 "或改用線條清楚、背景乾淨的圖。" % e}), 400
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    key = _slugify(name)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(root, "data", "shapes")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, key + ".json"), "w", encoding="utf-8") as fh:
        _json.dump({"key": key, "name": name, "note": "使用者上傳描圖",
                    "aspect": info["寬高比"], "pts": pts}, fh, ensure_ascii=False)
    SHAPES[key] = {"name": name, "pts": shapes._closed([tuple(p) for p in pts]),
                   "aspect": info["寬高比"], "spurs": [], "note": "使用者上傳描圖",
                   "fits": tuple(DISTRICTS)}

    from .trace import preview
    png = os.path.join(OUT, key + "_描圖.png")
    preview(pts, png)
    with open(png, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return jsonify({"key": key, "name": name, "info": info,
                    "pts": shapes.normalised(key),
                    "preview": "data:image/png;base64," + b64})


@app.route("/api/shape/delete", methods=["POST"])
def api_shape_delete():
    """刪掉上傳的圖形。只准刪 user_ 開頭的——內建圖形是程式的一部分，不給刪。"""
    key = (request.get_json(force=True) or {}).get("key") or ""
    if not key.startswith("user_"):
        return jsonify({"error": "只能刪除自己上傳的圖形"}), 400
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    f = os.path.join(root, "data", "shapes", key + ".json")
    if os.path.exists(f):
        os.remove(f)
    SHAPES.pop(key, None)
    return jsonify({"ok": True, "shapes": [k for k in SHAPES if k.startswith("user_")]})


@app.route("/api/plan-multi", methods=["POST"])
def api_plan_multi():
    body = request.get_json(force=True)
    ds = [x for x in (body.get("districts") or []) if x in DISTRICTS]
    if not ds:
        return jsonify({"error": "至少要選一個行政區"}), 400
    job = os.urandom(6).hex()
    with LOCK:
        JOBS[job] = {"state": "run", "log": []}

    def work():
        def log(*a):
            with LOCK:
                JOBS[job]["log"].append(" ".join(str(x) for x in a))
        try:
            rows = plan_best(ds, body["shape"], body.get("level", "standard"),
                             log=log)
            out = []
            for r in rows:
                rid = "%s_%s_%s" % (r["meta"]["district"], body["shape"],
                                    body.get("level", "standard"))
                out.append({"id": rid, "meta": r["meta"], "score": r["score"],
                            "cues": r["cues"], "notes": r["notes"],
                            "marks": [[m[0], m[1], m[2], m[3]] for m in r["marks"]]})
            with LOCK:
                JOBS[job].update(state="done", multi=out, labels=LABEL)
        except Exception as e:      # noqa
            with LOCK:
                JOBS[job].update(state="error",
                                 error="%s" % (e,) + chr(10) + traceback.format_exc())

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"job": job})


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

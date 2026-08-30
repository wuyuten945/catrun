# 貓行台中（catrun.ego-intl.com）靜態站

這個資料夾就是要部署的東西，**沒有建置步驟**——Render Static Site 的
Publish Directory 指到這裡即可。

| 路徑 | 內容 |
|---|---|
| `index.html` / `app.js` / `styles.css` | 前端（純 vanilla、hash 路由，靜態主機不用設 rewrite） |
| `data/index.json` | 全部路線摘要（首頁一次載入，約 28 KB） |
| `data/route/*.json` | 單條路線的導航表、安全提醒、地標（點進去才載） |
| `data/shapes.json` | 圖形庫 |
| `img/*.jpg` `gpx/*.gpx` `kml/*.kml` | 路線圖與軌跡檔 |

內容由專案根目錄的 `tools_build_site.py` 產生，要更新路線就重跑它再 commit。

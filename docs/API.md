# 外部HTTP API

API與Streamlit介面獨立運行。API採每次請求提交資料的方式，不開放伺服器檔案路徑、不共用不同呼叫者的資料。API金鑰只授權運算，不會授權存取先前的場址或成果。暫存上傳在請求結束後刪除；回傳結果由呼叫端保存。

## 本機啟動

在儲存庫根目錄執行安裝後：

```bash
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/spatial-monitor api-keys --client alice --client bob
.venv/bin/spatial-monitor api --keys-file .api-keys.json
```

金鑰檔以0600權限建立，既有檔案不會被覆寫，也不會在終端印出金鑰。請透過安全方式將各人的金鑰交給本人；不要提交至Git或放入網址。伺服器也可透過 `SM_API_KEYS_JSON` 取得相同的JSON物件。未提供有效設定時拒絕啟動。輪替或撤銷金鑰：修改安全設定後重新啟動API。

本機預設監聽127.0.0.1:8000；`--address 0.0.0.0` 可讓同網路其他機器連線，但公開使用應經HTTPS入口。`/docs` 提供互動文件，點選Authorize填入金鑰後可測試呼叫。文件與health公開，資料與運算端點均需 `X-API-Key` 標頭。

## 端點

| 方法／路徑 | 用途 |
|---|---|
| GET /healthz | 健康檢查，不揭露資料或金鑰 |
| GET /v1/parameters | 支援參數及伺服器限制 |
| POST /v1/validate | JSON資料表整併、品質檢核與標準化資料 |
| POST /v1/validate/file | multipart Excel或CSV ZIP上傳及檢核 |
| POST /v1/layers | 列出依介質、方法、日期、深度等隔離的有效分層 |
| POST /v1/interpolate | 指定group_index的2D IDW，回傳網格及支持遮罩 |
| POST /v1/export | JSON資料表產生完整成果ZIP |
| POST /v1/export/file | Excel或CSV ZIP產生成果ZIP |

JSON格式與原有欄位對應設定相容：

```json
{
  "tables": {
    "samples": [{"sample_id":"S1","point_id":"P1","date":"2026-01-01","medium":"groundwater","depth_top":0,"depth_bottom":1}],
    "analyses": [{"sample_id":"S1","parameter":"pH","value":7.2,"unit":"1"}],
    "points": [{"point_id":"P1","x":250000,"y":2650000,"crs":"EPSG:3826"}]
  },
  "config": {}
}
```

可加入 `geology`、`water_levels`。以 `/v1/layers` 取得group_index後，在同一資料請求頂層加入 `"group_index": 0` 呼叫內插。未支持的網格值回傳null；點位不足則available=false及實測資料，並非錯誤估值或補零。

Excel使用預設工作表名稱；CSV ZIP必須僅包含根目錄的samples.csv、analyses.csv、points.csv，以及選填geology.csv、water_levels.csv。自訂欄位／工作表對應請用JSON的config介面；檔案端點採預設對應。禁止ZIP路徑、重複成員、加密壓縮及超量展開。

Python呼叫範例（API金鑰由環境變數讀取）：

```python
import os
import requests

base = os.environ['SPATIAL_API_URL'].rstrip('/')  # 已部署的HTTPS網址
headers = {'X-API-Key': os.environ['SPATIAL_API_KEY']}
with open('monitoring.xlsx', 'rb') as handle:
    response = requests.post(base + '/v1/validate/file', headers=headers,
                             files={'file': ('monitoring.xlsx', handle)}, timeout=120)
response.raise_for_status()
print(response.json()['valid_observations'])

with open('monitoring.xlsx', 'rb') as handle:
    response = requests.post(base + '/v1/export/file', headers=headers,
                             files={'file': ('monitoring.xlsx', handle)}, timeout=600)
response.raise_for_status()
with open('results.zip', 'wb') as output:
    output.write(response.content)
```

錯誤碼：401金鑰無效、413資料／上傳超限、422格式或分析選擇無效、429已有運算執行中（依Retry-After重試）。資料品質異常由正常檢核回應的errors與issues列出，不自動丟棄資料。

預設每請求16MiB、壓縮展開64MiB、20,000筆總資料列、1,000點位、每層500量測、20,000網格及64張分層圖。每個服務程序同時接受一個分析工作；正式服務採單worker以保護製圖程序，擴展以獨立容器進行。建議代理及用戶端匯出逾時600秒；小型同步API不提供背景佇列或跨請求成果保存。

## 公開部署

專案提供兩種可選部署設定；在取得實際主機／平台前，設定檔不代表已發布。

### 已有Linux伺服器及網域

需要Docker Compose、80／443連接埠及指向主機的DNS。將程式碼及私密金鑰檔放在主機儲存庫目錄，以持有0600金鑰檔的非root使用者執行下列設定，讓容器以同一UID讀取檔案，不需放寬檔案權限。

```bash
# 在儲存庫根目錄；API_DOMAIN換成你持有的實際網域
export API_DOMAIN=api.your-domain.example
export SPATIAL_UID=$(id -u)
export SPATIAL_GID=$(id -g)
docker compose -f deploy/compose.yaml up -d --build
```

Caddy取得並更新TLS憑證，只有HTTPS入口對外開放，API埠不直接發布。健康檢查為 `https://<你的網域>/healthz`，文件為 `/docs`。金鑰檔留在主機，不會進入Docker image。

### Render等容器平台

`render.yaml` 提供Render設定（starter為付費方案，需由平台帳號設定及啟用）。使用Docker部署，平台提供HTTPS網域；在平台的私密環境設定填入SM_API_KEYS_JSON。不要將金鑰寫進render.yaml。容器內綁定0.0.0.0:8000。其他平台可直接使用同一Dockerfile，對應服務埠8000並設定健康檢查。

伺服器對伺服器呼叫不需要CORS。若從另一個網站的瀏覽器前端呼叫，設定 `SM_API_ALLOWED_ORIGINS=https://your-app.example`；多個來源逗號分隔，不接受萬用字元。不要將正式共用金鑰嵌入公開前端JavaScript；應透過自己的後端轉呼叫。

本專案的部署分支可由 [Deploy to Render](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fa95175368420-create%2FSpatial-Interpolation%2Ftree%2Fcodex%2Fexternal-api) 開始：登入Render並授權讀取該GitHub儲存庫、選擇部署方案，再在私密欄位填入SM_API_KEYS_JSON（內容來自本機生成的.api-keys.json，請勿貼到公開issue或聊天）。SM_API_ALLOWED_ORIGINS無瀏覽器跨站需求時留空。確認費用並發布後，Render會提供實際HTTPS網址；取得網址後應重新執行遠端健康、授權與上傳下載檢查。

# 環境空間工作台 · Spatial Interpolation

先開啟 [個人測試網站部署入口](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fa95175368420-create%2FSpatial-Interpolation%2Ftree%2Fcodex%2Fexternal-api)，依 [個人測試說明](docs/PERSONAL_SITE.md) 設定一個密碼即可發布圖形網站；預設使用Render免費方案。

以 Python 建立的多參數環境監測與地質整合工具。支援 Excel／CSV 匯入、可追溯資料檢核、2D 分布、時間與深度比較、地質柱狀與剖面、互動 3D 實測展示及離線成果輸出。

**監測資料庫v3.4可直接上傳**：下載 [已填合成範例](examples/workbooks/monitoring_v34_demo.xlsx) 或 [空白範本](examples/workbooks/monitoring_v34_blank.xlsx)，每個場址使用一份相同格式工作簿。平台自動辨識中文表格及場址，不需額外YAML。詳見 [v3.4匯入及多人使用說明](docs/WORKBOOK_V34.md)。

內建 pH、EC、DO、DO 飽和度、ORP、TOC、Zn、水溫與濁度；可透過 YAML 增加其他連續數值參數。沒有內建法規判定值。`examples/demo` 是合成示範資料，不是實際調查結果。

## 安裝與啟動

需要 Python 3.11 以上。在儲存庫根目錄執行：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/spatial-monitor serve
```

Windows 請將 `.venv/bin/` 改為 `.venv\Scripts\`。開發其他版本組合時可用 `python -m pip install -e '.[dev]'`，目前驗證版本記錄於 `requirements.lock`。

介面預設載入合成資料，可切換本機 CSV 資料夾、Excel 工作簿或上傳檔案。Excel 每張工作表對應一種資料表；CSV 需一併上傳同一資料組的基本資料表。YAML 可另行上傳以對應欄位或設定參數。介面包括資料檢核、分布與趨勢、地質與剖面、3D 實測展示、選用分析及成果匯出。

命令列批次操作：

```bash
.venv/bin/spatial-monitor validate examples/demo --config examples/config.yaml
.venv/bin/spatial-monitor export examples/demo --config examples/config.yaml --output outputs/demo --zip
.venv/bin/python -m pytest -q
```

`validate` 發現 error 時回傳 1，設定／輸入失敗回傳 2，成功回傳 0。匯出保留無效資料與異常清單，只對有效數值製圖；請先查看檢核結果。輸出目錄必須為新目錄或空目錄，不覆寫前次成果。批次預設上限 200 張分層圖，超過時需明確指定 `--max-maps`。

## 資料格式

基本資料表為 `samples.csv`、`analyses.csv`、`points.csv`；`geology.csv`、`water_levels.csv` 選填。Excel 預設工作表名稱為上述不含副檔名的名稱。

| 資料表 | 欄位 |
|---|---|
| samples | `sample_id, point_id, date, medium, depth_top, depth_bottom, batch, site` |
| analyses | `sample_id, parameter, value, unit, method, basis, temperature, temperature_compensated, reference_temperature, reference_electrode, calibration` |
| points | `point_id, x, y, crs, surface_elevation, site` |
| geology | `point_id, depth_top, depth_bottom, lithology, stratum, description` |
| water_levels | `point_id, date, depth` |

識別碼以字串保存，必須在整份資料內唯一對應。`date` 使用 ISO 日期／時間。深度單位為公尺、向下為正，區間為 `depth_top ≤ depth_bottom`；點量測可使用相同上下限。坐標需宣告 CRS（如 `EPSG:3826`）；距離與內插功能要求公尺投影座標。已確認為工程公尺座標時可明確宣告 `LOCAL_METRIC`。不自動重投影，也不將經緯度當公尺。

介質預設代碼：`soil`、`water`、`groundwater`、`surface_water`、`air`（各參數依適用介質限制）。土壤 TOC 需明確提供 `basis`，如 `dry` 或 `wet`。選填資訊不足時會留下檢核訊息，缺少必要欄位不會被默認填成有效數據。

中文欄位與原始三張工作表可透過 YAML 對應，方向為「來源名稱 → 標準名稱」：

```yaml
tables:
  samples:
    sheet: 01_樣品登錄
    columns:
      樣品編號: sample_id
      點位編號: point_id
      日期: date
      介質: medium
      深度上限: depth_top
      深度下限: depth_bottom
  analyses:
    sheet: 02_分析資料庫
    columns:
      樣品編號: sample_id
      分析項目: parameter
      濃度: value
      單位: unit
  points:
    sheet: 03_點位主檔
    columns:
      點位編號: point_id
      平面X: x
      平面Y: y
crs: EPSG:3826
```

欄位對應不會猜測欄位值，例如原始「土壤」需在來源整理為 `soil`，或在參數設定中明確定義該介質及標準單位。原始資料不會被程式覆寫。

## 參數設定與資料規則

參數、欄位對應及內插設定採遞迴合併 YAML，未指定的欄位沿用預設值。範例：

```yaml
parameters:
  pH:
    colorscale: RdBu
    thresholds:
      - kind: range
        value: [6.5, 8.5]
        label: 專案示例範圍
        source: 使用者指定的示例，非法規預設
        conditions: 僅用於已確認適用的水樣組別
  custom_indicator:
    label: 自訂指標
    unit: mg/L
    media: [water, groundwater]
    data_type: continuous
    transform: none
    colorscale: Viridis
    valid_range: [0, null]
    thresholds: []
interpolation:
  power: 2
  grid_spacing: 1
  max_distance: 10
  min_width: 2
  max_cells: 100000
```

- EC 的 mS/cm 可轉為 uS/cm，保留溫度補償與參考溫度。未補償資料依量測溫度分組，補償資料依明確參考溫度分組；不推算缺少的補償係數。
- DO 的 mg/L 與 `%` 飽和度分別管理為 `DO`、`DO_SAT`，不互相換算。
- TOC 按介質、單位及乾濕重基準分組。相容質量比單位可換算，不跨介質轉換；水樣 ppm 不直接當 mg/L。
- pH 與 ORP 不允許濃度 log10 轉換；負 ORP 為合法量測。其他參數只有所有使用值皆正時才能使用 log10 內插。
- `ND`、`<檢出限` 等保留原始值、限定符及可解析檢出限，不以零或半檢出限補值；不納入數值內插。
- 重複樣品／點位關聯不會放大 join，重複量測不自動平均。缺值、無法對應、異常區間或單位列入 `issues`，保留來源檔案與來源列號。
- 門檻支援 `upper`、`lower`、`range`，必須填來源與適用條件。圖面標為設定參考值，使用者應確認選取介質及方法適用。

## 空間展示與選用模型

所有可比較分組保留場址、參數、介質、單位、方法、量測基準、電極及溫度資訊。分層圖另外按日期、批次與精確深度區間分組，跨層樣品不會拆分。相同組別跨層共用圖幅與色階，第二參數使用自己的色階。實測點可套疊當日地下水與涵蓋該深度區間的岩性記錄。

2D 使用全點 IDW；凸包與最近點距離限制只控制顯示。少於三點、共線、凸包面積／直徑小於設定寬度、相同 XY 重複或網格超限時只顯示實測點並列出原因。空白區域沒有估值，不代表無污染。增加網格解析度不會增加原始調查資訊。

地質圖保留真實深度區間，缺口標為 Unknown，不以類別編碼內插。剖面使用指定起終點與側向納入距離，顯示沿線距離及偏距。使用者可輸入下列 JSON 並勾選確認，加入虛線解釋接觸：

```json
[{"name":"砂層頂界", "point_ids":["BH01","BH02"], "depths":[1.5,2.0]}]
```

3D 以實測柱段、岩性及地下水觀測展示，圖例可切換圖層、滑鼠可旋轉縮放及查詢。地表高程完整才用實際高程；任一來源缺少高程時整張圖使用地表零點的相對模型。堆疊圖明示「2D layer stack」，不當作三維內插；地表高程不一致時，需有地形參考才能建立平面堆疊，現版會說明並略過。

選用模組預設關閉，介面獨立啟用：

1. **關聯分析**：依點位、場址、介質、基準及指定時間／深度容許差建立雙向唯一配對；模糊配對排除並揭露。統計按雙方單位與方法等相容條件分組，至少三組非定值配對才計算 Pearson／Spearman。地層分組只納入完整落於唯一實測地層的樣品。相關性不等於因果。
2. **連續地質模型**：使用者指定由淺至深的完整地層序列並記錄解釋版本，確認後以一致權重推估地層界面。限制在完整鑽孔共同支持範圍，提供界面及 Y 向剖切。現版適用單一有序、連續層序，缺層、斷層、尖滅等複雜地質會拒絕而非自動修補。
3. **三維數值模型**：以採樣區間中點近似位置，設定水平／垂直尺度，使用有空間支持限制的各向異性 IDW。需要非共面資料，輸出留一驗證的有效數、覆蓋率、MAE／RMSE。地層限制需要已確認且相同 CRS／場址／高程基準的地質模型。
4. **體積估算**：查看驗證結果後另行啟用；至少三個受支持的留一預測且誤差可計算才允許統計。採網格中心分類並乘單格體積，僅代表支持範圍內的模型估算，提供解析度、邊界與驗證假設；不直接作為整治土方量。

三維等值面只連接具有效推估支持的網格，不以零補入未知區域。部分網格缺值時，只從八個頂點皆有效的網格單元建立表面；大型或無法形成表面的網格改以明確標示的有效網格中心點展示，最多顯示10,000點。此顯示限制不會改動下載的完整推估網格或體積統計。

## 成果與驗證

批次或介面輸出包含標準化 CSV、所有異常紀錄、分層統計、2D PNG、可離線互動的 HTML、地質柱狀圖、剖面及 3D 實測圖、YAML 設定及 SHA-256 輸入指紋。HTML 與同資料夾 `plotly.min.js` 必須一併保存。選用模型另可下載網格、界面、驗證及解釋紀錄；ZIP 會檢查 CRC。

自動測試涵蓋單位與未檢出處理、欄位映射、無效 join、跨批次／跨基準隔離、IDW 與有效範圍、實測區間高程、地質缺口、唯一配對、三維驗證與體積條件、離線成果及互動介面。推估功能的測試證明程式行為，不等於特定場址已達調查或模型準確度要求。

Python 公開入口：`load_config`、`load_dataset`、`filter_observations`、`interpolate_layer`、各 `*_figure`、`export_dataset`；選用模組位於 `spatial_monitor.extensions`。不需外部服務或 API 金鑰。套件下載使用 PyPI；開啟離線成果不需外網。

### 實際瀏覽器操作測試

除 Python 與 Streamlit 元件測試外，另提供 Chromium 的實際 DOM／WebGL 操作測試：

```bash
.venv/bin/python -m pip install -e '.[browser]'
# 先在另一個終端執行 .venv/bin/spatial-monitor serve
.venv/bin/python tests/browser_smoke.py --browser /usr/bin/chromium
.venv/bin/python tests/browser_optional.py --browser /usr/bin/chromium
```

需有 Chromium；`--browser` 可指定其他 Chromium 執行檔位置。測試會實際上傳CSV／Excel、切換圖層、拖曳3D相機並下載檔案，將截圖與JSON結果保存於 `outputs/browser-qa/`。選用模型測試另驗證結果保留、地質確認撤回與三維渲染。這些測試不會修改來源資料。

## 外部HTTP API

新增獨立HTTP服務，支援每位使用者API金鑰、Excel／CSV ZIP或JSON資料匯入、檢核、2D內插及成果下載。先執行 `spatial-monitor api-keys --client alice`，再執行 `spatial-monitor api --keys-file .api-keys.json`。完整端點、呼叫範例及HTTPS部署方式見 [API說明](docs/API.md)。Docker／Caddy與Render部署設定均已提供；API的Blueprint保留於`deploy/render-api.yaml`，預設`render.yaml`為個人圖形入口。仍需實際伺服器或雲端帳號才能取得公開網址。

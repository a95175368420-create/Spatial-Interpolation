# 個人測試網站入口

預設Render部署現在只建立一個「環境空間工作台」圖形網站。使用密碼登入，可直接操作六個頁籤、內建合成範例、自己的Excel／CSV及成果下載。這個入口不需要API金鑰。

## 發布步驟

1. 開啟 [Deploy to Render](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fa95175368420-create%2FSpatial-Interpolation%2Ftree%2Fcodex%2Fexternal-api)，登入Render並允許讀取此GitHub儲存庫。
2. 確認服務為 **spatial-monitor-personal**、使用 **Free** 方案。在私密環境欄位 **SM_TEST_PASSWORD** 填入自己的至少16字元密碼；也可以使用本機`.personal-site-password`檔案內容。密碼不需要JSON格式，勿貼入聊天、GitHub或公開設定檔。
3. 發布完成後，在Render服務頁開啟它分配的HTTPS網址。看到「個人測試入口」後輸入同一密碼，點選「進入工作台」。此網址就是個人測試入口，不需加上`/docs`。

免費服務可能休眠，首次開啟需等待喚醒；資源及平台政策以Render發布頁為準。若已有其他Blueprint或服務，本設定不會替你關閉它；API服務設定保留在`deploy/render-api.yaml`供日後另外啟用。

## 第一次使用

登入後預設使用「內建範例」，應顯示288筆分析紀錄、288筆有效數值及6個點位。範例是合成資料。

- **資料檢核**：查看標準化資料及檢核訊息。
- **分布與趨勢**：選擇參數與深度，勾選「顯示2D內插估值」。
- **地質與剖面**：選擇剖面起終點，查看柱狀圖與剖面。
- **3D實測展示**：拖曳旋轉、縮放，點擊圖例切換圖層。
- **選用分析**：按需要啟用關聯、三維推估及地質模型。
- **成果匯出**：產生並下載ZIP。

自己的資料可由側欄切換「上傳Excel或CSV」。監測資料庫v3.4會自動辨識，不需YAML；側欄可下載已填範例及空白範本，各使用者可上傳各自場址的同格式檔案，詳見[v3.4說明](WORKBOOK_V34.md)。一般Excel仍支援samples、analyses、points，以及選填的地質與地下水表；其他中文欄位可上傳YAML對應。網站上傳限制為每檔16MB。個人測試模式不提供伺服器檔案路徑輸入。

點選「登出測試入口」會清除該工作階段的已載入資料、模型及成果狀態。重新連線或網站重新啟動可能需要再次登入、上傳；請下載需要保留的成果。若要變更密碼，在Render修改SM_TEST_PASSWORD後重新啟動服務，舊工作階段將失效。

## 在本機／雲端工作區驗證

```bash
.venv/bin/spatial-monitor site-password
.venv/bin/spatial-monitor serve --password-file .personal-site-password
```

密碼檔以0600權限建立，命令不印出密碼，亦不覆寫已存在的檔案。密碼檔已被Git與Docker排除。

實際瀏覽器重測：

```bash
.venv/bin/python tests/browser_personal.py --browser /usr/bin/chromium
```

若個人模式缺少有效密碼，僅顯示設定錯誤，不能進入資料處理。一般本機開發的`spatial-monitor serve`保留原流程；公開部署應使用Blueprint設定或明確啟用SM_APP_MODE=personal。

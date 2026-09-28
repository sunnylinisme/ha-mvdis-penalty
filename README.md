# 台灣監理站罰單通知（Home Assistant Add-on）

這是一個 Home Assistant Add-on，會定時查詢本人或已授權家人在台灣監理服務網上的未繳交通違規紀錄，在 Home Assistant 主機本機辨識驗證碼，建立感測器，並在出現新紀錄時發出通知。最多可設定 5 人。

> [!WARNING]
> 本專案是非官方的社群作品，與交通部、公路局及監理服務網無關，也未獲其背書。政府網站若改版，查詢功能可能暫時失效。罰單、金額與期限等重要資訊，請務必回到[監理服務網](https://www.mvdis.gov.tw/)確認。

## 支援環境

目前版本支援 Home Assistant OS `amd64` 與 `aarch64`。

## 安裝方式

1. 在 Home Assistant 開啟「設定 → 附加元件 → 附加元件商店」。
2. 開啟右上角選單，選擇「儲存庫」，加入：
   `https://github.com/sunnylinisme/ha-mvdis-penalty`
3. 安裝「台灣監理站罰單通知」。
4. 在「設定」頁填入：
   - 主要查詢人的顯示名稱。
   - 本人的身分證字號，例如 `A123456789`；Add-on 會檢查格式與檢查碼。
   - 七碼民國出生年月日，例如民國 78 年 7 月 2 日填 `0780702`。
   - 如需多人查詢，在「其他查詢人」加入名稱、身分證字號及出生年月日；最多可再加入 4 人。
   - 查詢間隔，預設 24 小時，最短 6 小時。
   - 驗證碼最多嘗試次數，預設 1 次；每張驗證碼會先在本機用多種影像處理方式辨識，不會為此增加網站請求。只有仍經常失敗時才建議手動提高。
5. 啟動 Add-on，並建議開啟「開機時啟動」與「監控程式」。
6. 按下「儲存」後，Add-on 會在約 5 秒內自動驗證新設定並立即查詢，不必重新啟動或等待下次排程。
7. 按「開啟網頁介面」，即可查看每位查詢人的成功或失敗狀態、罰單內容、立即查詢或傳送測試通知；也可到「日誌」確認出現 `MVDIS query succeeded`。

Add-on 使用 GitHub 預先建置的映像檔；Home Assistant 只需下載完成品，不會在主機上編譯或安裝 OCR 套件。下載時間仍會受到網路速度影響。安裝完成後不需要重啟 Home Assistant。

## Home Assistant 實體

- `sensor.mvdis_penalty_unpaid_count`：未繳筆數
- `sensor.mvdis_penalty_total_amount`：未繳總金額
- `binary_sensor.mvdis_penalty_has_unpaid`：是否有未繳罰單
- `sensor.mvdis_penalty_last_check`：最後查詢時間
- `sensor.mvdis_penalty_status`：最後查詢狀態

以上固定 ID 屬於主要查詢人，升級後既有儀表板與自動化可以繼續使用。其他查詢人會各自建立同樣的 5 個實體，ID 會加入一段以本機隨機金鑰產生、不含身分證字號的識別碼，例如 `sensor.mvdis_penalty_a1b2c3d4e5_unpaid_count`；Home Assistant 介面顯示的名稱會使用你設定的查詢人名稱。

每位查詢人另有一個可搜尋的群組實體，主要查詢人為 `group.mvdis_penalty`，其他查詢人的群組 ID 會包含相同的本機識別碼。點開群組即可集中查看該人的 5 個實體。由 Add-on 直接建立的實體在「裝置與服務」頁仍可能列於「未分組」，這是 Home Assistant REST 狀態實體的限制，不影響群組、查詢或通知。

## 內建查詢頁面

Add-on 使用 Home Assistant Ingress 提供管理頁面，不對區域網路開放連接埠，並由 Home Assistant 處理登入驗證。頁面提供：

- 每位查詢人的最後查詢時間、狀態、未繳筆數及金額。
- 儲存設定後自動驗證與查詢；格式錯誤會指出查詢人及原因，身分資料遭監理服務網拒絕則顯示在該查詢人的卡片中。
- 監理服務網回傳的完整表格欄位，例如違規日期、事實、地點與應繳金額。
- 「立即查詢」按鈕；若已有查詢正在執行，不會重複送出。
- 為避免因重新啟動或連續按鈕造成密集存取，相同設定查詢後 5 分鐘內會暫停再次立即查詢，頁面會顯示可再次查詢時間；手動查詢完成後會重新計算下一次排程。
- 每位查詢人的測試通知；內容會明確標示為測試，不會建立假罰單資料。
- 驗證碼、身分資料、網站格式、逾時及網路等錯誤分類。
- 連線或網站異常時停止整批請求並冷卻 30 分鐘，保留上次成功資料，之後自動重試。

每位查詢人的第一次成功查詢只會建立各自基準，不會把原本已存在的紀錄誤報為「新罰單」。同一天的不同違規會以日期、事實、地點及可用單號等欄位分別識別，不會互相覆蓋。之後若發現從未回報過的紀錄，Add-on 會建立 Home Assistant 永久通知，並觸發 `mvdis_penalty_new_case` 事件；已通知過的紀錄即使暫時消失後重新出現，也不會重複通知。若原有未繳紀錄之後全部消失，則會建立確認提醒並觸發 `mvdis_penalty_cleared`；仍應回監理服務網確認案件狀態。從設定移除查詢人後，其舊實體及群組會在下次查詢時清理。

## 手機通知（選用）

Add-on 本身一定會建立 Home Assistant 永久通知。若要同步推播到手機，可建立下列自動化，並把 `notify.mobile_app_your_phone` 換成自己手機的通知動作：

```yaml
alias: 監理站發現新罰單
triggers:
  - trigger: event
    event_type: mvdis_penalty_new_case
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "監理服務發現新罰單"
      message: >-
        {{ trigger.event.data.profile }}新增 {{ trigger.event.data.count }} 筆：
        {{ trigger.event.data.summaries | join('；') }}
mode: queued
```

## 更新方式

新版本發布後，進入 Add-on 商店的「台灣監理站罰單通知」頁面按「更新」。若沒有看到更新，可先在附加元件商店選單中按「檢查更新」，或重新載入自訂儲存庫。更新時原本的 Add-on 設定會保留。

## 常見問題

### 日誌顯示驗證碼失敗

監理服務網的驗證碼由本機 OCR 判讀。Add-on 會對同一張圖進行多種本機辨識，再選出最可信的答案；這不會增加網站請求，但驗證碼仍可能偶爾辨識失敗。若持續失敗，可提高「驗證碼最多重試次數」後重新啟動 Add-on；每增加一次代表再向網站取得一張新驗證碼並查詢一次。

### 已啟動但沒有收到通知

第一次成功查詢只建立基準，所以不會通知既有紀錄。請先查看五個實體是否已出現，並確認日誌包含 `MVDIS query succeeded`。只有後續新增且未見過的紀錄才會產生新通知。

### 資料沒有即時出現

違規資料必須等舉發機關登錄後才會出現在監理服務網，因此可能不是即時更新。本 Add-on 只能回報網站當下提供的結果。

## 隱私與安全

- 只能查詢本人，或你已獲合法授權管理的身分資料。
- 每位查詢人的身分證字號及出生年月日儲存在 Home Assistant Supervisor 的 Add-on 設定中，並非靜態加密；請妥善保護管理員帳號與備份。
- Add-on 不會把身分證字號或出生年月日寫入日誌或實體狀態。
- 查詢到的罰單欄位會保存在 Add-on 資料及 Home Assistant 實體屬性中，以供頁面、通知與自動化使用；請妥善保護 Home Assistant 資料庫及備份。
- 驗證碼使用精簡自 `ddddocr` 的辨識模型與 `onnxruntime` 在本機處理。
- 不會對區域網路開放任何連接埠。
- 本專案不會勾選罰單，也不會啟動繳費程序。

## 使用限制與免責聲明

預設每 24 小時查詢一次，最短間隔為 6 小時，請勿以高頻率存取政府網站。請自行遵守監理服務網的使用規範、可用性與容量限制。

本專案只提供提醒，不保證查無資料就代表沒有罰單，也不對資料正確性、繳費期限或法律結果提供任何保證。請以監理服務網及正式通知為準。

## 開發與測試

```bash
python -m pip install pytest beautifulsoup4 requests ruff
pytest
ruff check mvdis_penalty_backend/app tests
python -m compileall mvdis_penalty_backend/app
```

## 授權

MIT

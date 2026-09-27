# 台灣監理站罰單通知（Home Assistant Add-on）

這是一個 Home Assistant Add-on，會定時查詢使用者本人在台灣監理服務網上的未繳交通違規紀錄，在 Home Assistant 主機本機辨識驗證碼，建立感測器，並在出現新紀錄時發出通知。**只要安裝 Add-on，不需要 HACS。**

> [!WARNING]
> 本專案是非官方的社群作品，與交通部、公路局及監理服務網無關，也未獲其背書。政府網站若改版，查詢功能可能暫時失效。罰單、金額與期限等重要資訊，請務必回到[監理服務網](https://www.mvdis.gov.tw/)確認。

## 支援環境

目前版本支援 Home Assistant OS `amd64`。

## 安裝方式

1. 在 Home Assistant 開啟「設定 → 附加元件 → 附加元件商店」。
2. 開啟右上角選單，選擇「儲存庫」，加入：
   `https://github.com/sunnylinisme/ha-mvdis-penalty`
3. 安裝「台灣監理站罰單通知」。
4. 在「設定」頁填入：
   - 本人的身分證字號，例如 `A123456789`。
   - 七碼民國出生年月日，例如民國 78 年 7 月 2 日填 `0780702`。
   - 查詢間隔，預設 24 小時，最短 6 小時。
   - 驗證碼最多重試次數，預設 3 次。
5. 啟動 Add-on，並建議開啟「開機時啟動」與「監控程式」。
6. 到「日誌」確認出現 `MVDIS query succeeded`。

第一次建立映像檔時需要下載本機 OCR 執行環境，可能會花幾分鐘。安裝完成後不需要重啟 Home Assistant，也不需要另外加入 HACS 自訂儲存庫。

## Home Assistant 實體

- `sensor.mvdis_penalty_unpaid_count`：未繳筆數
- `sensor.mvdis_penalty_total_amount`：未繳總金額
- `binary_sensor.mvdis_penalty_has_unpaid`：是否有未繳罰單
- `sensor.mvdis_penalty_last_check`：最後查詢時間
- `sensor.mvdis_penalty_status`：最後查詢狀態

第一次成功查詢只會建立基準，不會把原本已存在的紀錄誤報為「新罰單」。之後若發現未見過的紀錄，Add-on 會建立 Home Assistant 永久通知，並觸發 `mvdis_penalty_new_case` 事件。

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
        新增 {{ trigger.event.data.count }} 筆：
        {{ trigger.event.data.summaries | join('；') }}
mode: queued
```

## 更新方式

新版本發布後，進入 Add-on 商店的「台灣監理站罰單通知」頁面按「更新」。若沒有看到更新，可先在附加元件商店選單中按「檢查更新」，或重新載入自訂儲存庫。更新時原本的 Add-on 設定會保留。

## 常見問題

### 日誌顯示 `Missing Subject Key Identifier`

請更新至 `0.2.1` 或更新版本。新版僅針對監理服務網停用 Python OpenSSL 過度嚴格的 X.509 檢查；憑證授權單位、主機名稱、有效期限與簽章驗證仍然啟用，並未使用不安全的 `verify=False`。

### 日誌顯示驗證碼失敗

監理服務網的驗證碼由本機 OCR 判讀，偶爾失敗是正常的。Add-on 會依設定自動重試。若持續失敗，可提高「驗證碼最多重試次數」後重新啟動 Add-on。

### 已啟動但沒有收到通知

第一次成功查詢只建立基準，所以不會通知既有紀錄。請先查看五個實體是否已出現，並確認日誌包含 `MVDIS query succeeded`。只有後續新增且未見過的紀錄才會產生新通知。

### 資料沒有即時出現

違規資料必須等舉發機關登錄後才會出現在監理服務網，因此可能不是即時更新。本 Add-on 只能回報網站當下提供的結果。

## 隱私與安全

- 只能查詢本人，或你已獲合法授權管理的身分資料。
- 身分證字號及出生年月日儲存在 Home Assistant Supervisor 的 Add-on 設定中，並非靜態加密；請妥善保護管理員帳號與備份。
- Add-on 不會把身分證字號或出生年月日寫入日誌或實體狀態。
- 驗證碼使用 `ddddocr` 與 `onnxruntime` 在本機辨識。
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

# 使用說明

這個 Add-on 會定時查詢監理服務網、在 Home Assistant 主機本機辨識驗證碼、建立實體，並在出現新罰單時建立永久通知。只需安裝這一個 Add-on，不需要 HACS。

> 本專案並非交通部、公路局或監理服務網的官方服務。重要資料請回到監理服務網確認。

## 設定

請只設定本人，或你已獲合法授權管理的身分資料。

- **身分證字號**：台灣身分證字號，例如 `A123456789`。
- **出生年月日（民國）**：固定七碼。例如民國 78 年 7 月 2 日填 `0780702`。
- **查詢間隔（小時）**：每次查詢之間的間隔，預設 24，最小值 6。
- **驗證碼最多重試次數**：每次查詢時，本機 OCR 可重試的次數，預設 3。

設定後啟動 Add-on，並到「日誌」確認出現 `MVDIS query succeeded`。第一次建立映像檔會下載 OCR 執行環境，可能需要幾分鐘。

## 查詢與通知規則

第一次成功查詢只會建立基準，不會把原本已存在的紀錄誤報為新罰單。之後若發現未見過的紀錄，Add-on 會：

1. 建立 Home Assistant 永久通知。
2. 觸發 `mvdis_penalty_new_case` 事件，供手機推播或其他自動化使用。
3. 更新相關感測器。

Add-on 不會勾選任何紀錄，也不會啟動繳費程序。

## 建立的實體

- `sensor.mvdis_penalty_unpaid_count`：未繳筆數
- `sensor.mvdis_penalty_total_amount`：未繳總金額
- `binary_sensor.mvdis_penalty_has_unpaid`：是否有未繳罰單
- `sensor.mvdis_penalty_last_check`：最後查詢時間
- `sensor.mvdis_penalty_status`：最後查詢狀態

## 手機推播（選用）

將下列自動化中的 `notify.mobile_app_your_phone` 改成自己的手機通知動作：

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

## 常見問題

### `Missing Subject Key Identifier`

請更新至 `0.2.1` 或更新版本。新版會維持憑證授權單位、主機名稱、有效期限與簽章驗證，只針對監理服務網避開 Python OpenSSL 過度嚴格的 X.509 檢查。

### 驗證碼持續失敗

OCR 偶爾辨識失敗是正常現象，Add-on 會自動重試。若持續失敗，可增加「驗證碼最多重試次數」並重新啟動。

### 沒有通知

第一次成功查詢只會建立基準。請先確認五個實體已建立，並在日誌找到 `MVDIS query succeeded`。後續新增且未見過的紀錄才會通知。

### 資料尚未出現

監理服務網的違規資料必須等舉發機關登錄後才會顯示，可能不是即時更新。本 Add-on 只能呈現網站當下回傳的資料。

## 隱私與安全

- Add-on 設定由 Home Assistant Supervisor 保存，並非靜態加密；請妥善保護管理員帳號與備份。
- 身分證字號及出生年月日不會寫入日誌或實體狀態。
- 驗證碼使用 `ddddocr` 與 `onnxruntime` 在本機辨識。
- 不會對區域網路開放任何連接埠。
- 建議保留至少 6 小時的查詢間隔，避免對政府網站造成負擔。

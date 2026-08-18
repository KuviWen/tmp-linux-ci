# Ticket 09 AC 5–7 正式化操作手冊

本手冊只處理 Ticket 09 尚未完成的 AC 5–7：正式 hard gates、`owner_operated` 核准，以及五個不同 eligible EOD 日期的 shadow。工程 fixture、mock、同日重播或人工改資料庫都不能當作通過證據。

## 目前判斷（2026-08-18）

| 來源 | 可存取狀態 | 正式資格狀態 | 目前缺口 |
| --- | --- | --- | --- |
| FinMind Free API | 使用者已有帳號；仍須在持久化 runtime 以 write-only UI 儲存並 live validate token | `not_qualified` | 公開文件沒有逐一明示五個必要資料集可長期保存、備份、轉換、訓練模型、保存衍生品；也沒有逐資料集不可變修訂／更正／生命週期證據 |
| Alpaca Trading API Basic | 使用者已有帳號；仍須確認 Basic、individual/nonprofessional 與帳號所在地分類，再以 write-only UI 儲存並 live validate key pair | `not_qualified` | 公開條款支持個人非商業使用，但沒有足以滿足 ADR 0018 的逐 distribution 明示保存、備份、轉換、訓練與衍生權利；交易所／第三方資料限制和帳號分類仍須保存證據 |

帳號、token/key 可用只證明 authentication；它不等於來源使用資格。正式資格必須同時具備：

1. 符合零付費方案主體分類的真實帳號。
2. 每個 distribution 的用途權利和限制證據。
3. 真實 live contract、schema、coverage、revision、calendar、corporate action 與 listing lifecycle 證據。
4. 平台簽發且內容定址的 historical qualification artifacts。

現行官方依據：

- FinMind [Terms of Use & Privacy Policy](https://finmind.github.io/en/PrivacyPolicy/) 與 [Disclaimer & Data Licensing](https://finmind.github.io/Disclaimer/)。
- Alpaca [Market Data API plans](https://docs.alpaca.markets/us/docs/about-market-data-api)、[Terms and Conditions](https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf) 與 [Customer Agreement](https://files.alpaca.markets/disclosures/library/AcctAppMarginAndCustAgmt.pdf)。
- Repo 決策：[ADR 0018](../adr/0018-zero-cost-authenticated-source-credentials.md) 與 [ADR 0019](../adr/0019-owner-operated-model-approval.md)。

## 證據與秘密的存放規則

- 本機證據庫：`.artifacts/ticket-09-ac5-7/evidence/`。此路徑已被 `.gitignore` 排除。
- 非秘密的 owner、方案、權利回覆、操作結果、checksums 可以放入證據庫。
- Alpaca key ID、secret key 與 FinMind token 不得放進證據庫、`.env`、ticket、Git commit、log、截圖或 public `tmp-linux-ci`。
- 真實憑證只能經應用的 `/operations/source-credentials` write-only 頁面交給持久化 `EncryptedFilesystemSecretProvider`。
- 目前 GitHub Actions 沒有 provider `secrets.*` 參照，因此不要替 public repo 新增 Alpaca／FinMind secrets。
- provider 回覆、帳號頁截圖與 plan receipt 應遮蔽 token、key、帳號號碼、住址及不必要個資；原始檔留在本機證據庫。

## 10 個操作階段

### 1. 鎖定 owner 與實際用途

建立 `owner-use-profile.yaml`，至少記錄：

- 穩定 owner principal；
- 部署地區與時區；
- 單一自然人、個人、非商業、只在本機研究使用；
- 不對第三方提供 raw data、預測 UI 或服務；
- 每日最長 24 小時 operating window 與預定停機窗；
- 停機資料只在重啟後從 committed checkpoint 補抓，`first_observed_at` 使用實際重啟後時間。

這份聲明支持 Alpaca／FinMind 帳號分類與 Ticket 09 `owner_operated` 核准，但不能自行擴張 provider 權利。

### 2. 建立不可混入 secrets 的證據庫

保存下列資料並在最後產生 `SHA256SUMS`：

- 官方條款／license 的存檔或列印 PDF；
- account plan／classification 的遮蔽截圖；
- provider support ticket 或 email 的完整往返；
- live validation 的非秘密 result、trace ID、policy version 與時間；
- qualification、training、evaluation、gate、approval、shadow 的內容 ID 與 checksums。

官方文件若可變，必須保存實際 reviewed version/date；mutable URL 不能單獨當作不可變證據。

### 3. FinMind 帳號與存取證據

在 [FinMind user page](https://finmindtrade.com/analysis/#/user) 確認帳號、Free 方案及 token 可重新發行。只記錄 plan／account classification；不要把 token 貼進 wizard。

必須涵蓋的 distributions：

- `TaiwanStockPrice`
- `TaiwanStockTradingDate`
- `TaiwanStockDividendResult`
- `TaiwanStockDelisting`
- `TaiwanStockSplitPrice`

`TaiwanStockInfo` 只能補 current reference，不能代替歷史 listing lifecycle。

### 4. Alpaca 帳號與存取證據

在 [Alpaca dashboard](https://app.alpaca.markets/paper/dashboard/overview) 確認：

- Trading API Basic，非 Broker API 或付費 plan；
- individual／nonprofessional 分類；
- 帳號所在地與 provider 實際核准狀態；
- 可發行 API key ID／secret pair。

必須涵蓋的 distributions 是 unadjusted EOD bars、corporate actions／symbol changes 及 trading calendar／early close。只在應用的 write-only 頁面輸入 key pair。

### 5. 取得逐資料集書面權利證據

公開條款目前不足以直接升格。最低成本做法是用既有免費帳號向 provider 取得書面確認，不需先升級付費方案。

寄給 FinMind（`finmind.tw@gmail.com`）的問題需逐一涵蓋五個 dataset，並請對方確認單一使用者的個人非商業本機研究是否可：

1. 以 API 自動擷取並長期保存 raw responses 與 decoded history；
2. 做本機備份及災難復原；
3. 清洗、校正、連結公司行動與 symbol lifecycle；
4. 用於 feature、label、training、calibration、backtest、shadow 與本機 prediction；
5. 長期保存 derived features、model artifacts、evaluation 與 prediction history；
6. 只在 owner 可見的本機 UI 顯示衍生結果；
7. 遵守何種 attribution、刪除、到期、修訂與重新同步義務；
8. 上述許可是否適用 Free plan，且是否有 dataset-specific 例外。

Alpaca support request 同樣逐一詢問 bars、corporate actions／symbol changes、calendar／early close，並要求對方說明 Basic individual/nonprofessional 帳號是否准許上述本機保存、備份、建模與衍生用途，以及 SIP／IEX／exchange 或其他 third-party terms 是否另有限制。

接受證據必須是 provider 官方 domain 的 ticket/email 或可下載 account agreement，能識別方案、帳號主體分類、資料範圍、用途與日期。模糊回答、社群貼文、客服口頭回答、auth success 或「personal use」推論都維持 `not_qualified`。

### 6. 建立持久化正式 operator runtime

正式 runtime 至少需要：

- 持久化 PostgreSQL 與 object repository；
- 持久化、加密且不進 repo 的 `SOURCE_SECRET_ROOT`；
- stable owner local API identity 與 source-adapter workload identity；
- formal authorization policies；
- `/operations/source-credentials` 可在重啟後保留 readiness；
- collector checkpoint、immutable raw archive、outbox 與 audit state 可重啟恢復。

`ticket-09-operator` 是專用的 loopback-only profile，使用獨立的 PostgreSQL、object、owner identity、source-adapter identity 與 encrypted source-secret volumes。owner 與 workload identity 的有效期固定為現有上限 30 日，涵蓋七日 approval 與五次 EOD；到期前必須完成本次證據鏈，不能刪除 identity volume 來偽裝同一 principal。

Windows 主機先安裝 Docker Desktop 並使用 Linux containers。可由系統管理員在 PowerShell 執行：

```powershell
winget install --exact --id Docker.DockerDesktop
```

安裝後啟動 Docker Desktop，等待 engine ready，再確認：

```powershell
docker version
docker compose version
```

在 repo root 啟動獨立 project；`OPERATOR_OWNER_PRINCIPAL` 只存非秘密 owner label：

```powershell
$env:OPERATOR_OWNER_PRINCIPAL = "owner-local"
docker compose -p stock-forecasting-ticket-09-operator --profile ticket-09-operator up -d --build --wait ticket-09-operator-cli
```

先查看 credential 狀態，再以 hidden prompt 寫入真實值。secret 不得放在 command arguments、環境變數、shell history 或公開 CI：

```powershell
docker compose -p stock-forecasting-ticket-09-operator --profile ticket-09-operator run --rm ticket-09-operator-cli python -m stock_forecasting.cli operator source-credentials status
docker compose -p stock-forecasting-ticket-09-operator --profile ticket-09-operator run --rm ticket-09-operator-cli python -m stock_forecasting.cli operator source-credentials configure --provider finmind-free-api
docker compose -p stock-forecasting-ticket-09-operator --profile ticket-09-operator run --rm ticket-09-operator-cli python -m stock_forecasting.cli operator source-credentials configure --provider alpaca-market-data-basic
```

在兩家 provider 的逐資料集書面權利回覆尚未審核通過前，validation 預期 fail closed，response 必須是 HTTP 403 `authorization_denied`，且不得呼叫 provider。Python CLI 直接執行時回傳 `3`；Compose wrapper 應回傳非零，但不同 Compose 版本可能將容器的非零結果正規化（Docker Compose v5.3.1 實測為 `1`），因此操作證據以「非零 + 403 problem body」為準：

```powershell
docker compose -p stock-forecasting-ticket-09-operator --profile ticket-09-operator run --rm ticket-09-operator-cli python -m stock_forecasting.cli operator source-credentials validate --provider finmind-free-api
$LASTEXITCODE
docker compose -p stock-forecasting-ticket-09-operator --profile ticket-09-operator run --rm ticket-09-operator-cli python -m stock_forecasting.cli operator source-credentials validate --provider alpaca-market-data-basic
$LASTEXITCODE
```

每日停機只停止 containers，不刪 volumes：

```powershell
docker compose -p stock-forecasting-ticket-09-operator --profile ticket-09-operator stop
docker compose -p stock-forecasting-ticket-09-operator --profile ticket-09-operator up -d --wait ticket-09-operator-cli
```

重啟後再次執行 `status`，兩筆 metadata 應維持 `configured`。正常停機與重啟禁止使用 `down --volumes`；那會刪除 canonical state、encrypted credentials 與 stable principals。現有 `ticket-06`、`ticket-07`、`ticket-08`、`ticket-09-acceptance` profiles 仍只是工程 acceptance，真實憑證不得寫入那些 disposable volumes。

### 7. 執行正式歷史資格

只有 Stage 5 和 6 都通過後，才能用真實 provider 呼叫收集：

- raw immutable receipts、request/response digests 與 actual `first_observed_at`；
- schema、coverage、calendar、early close、corporate actions、delisting、symbol lifecycle；
- corrections/revisions 與 source-policy binding；
- 每市場 exact feature-row digest 可用的 Ticket 08 claim chain。

目前 repo 有 qualification domain classes，但沒有供 operator 使用的正式 CLI／REST command。這是軟體 prerequisite；不能用手動 DB insert 或直接呼叫 private class 繞過。

### 8. 訓練正式候選並執行 hard gates

正式 `ForecastLab` 必須消費 Stage 7 的合格雙市場 claim，產生真實 immutable artifacts。Logistic 相對 class-prior 的 equal-cell macro-F1 至少改善 1 percentage point，且以下十類 hard gates 全部由可解析的 formal reports 通過：

- qualification
- point-in-time
- leakage
- calibration
- economics
- stability
- coverage
- operational
- security
- reproducibility

工程 `engineering_example` report 不能轉名成 `formal_evidence`。目前也缺 operator-facing command 來串起正式歷史 claims、訓練及完整 gate artifact issuance。

### 9. Owner 核准與五次 EOD shadow

Gate 全通過後，用政策指定的同一 stable owner principal 建立 `owner_operated` decision：

- `independent_review=false`；
- exact artifact、evaluation、gate policy、approval policy、理由及 expected assignment 全數綁定；
- decision 不能 override hard gate，且受七日有效期與 assignment CAS 約束。

其後在五個不同且遞增的 eligible EOD 日期完成兩市場 shadow。每日可以有排定停機；停機不算 cycle。重啟後從 committed checkpoint 補抓，但晚到資料的 `first_observed_at` 不得回填到停機前。任何同日 replay 都不能增加 cycle。

### 10. 對帳並更新 AC

逐項核對 immutable IDs、checksums、REST／UI projection 與 Ticket 09：

- AC 5 只有真實 logistic improvement 及全部 formal hard gates 通過才可勾選。
- AC 6 只有實際 owner decision 綁定 exact evidence 且 query/UI 顯示 `owner_operated`、`independent_review=false` 才可勾選。
- AC 7 只有五個真實、不同、遞增 eligible EOD shadow cycles，且停機／checkpoint 行為有證據才可勾選。

`SHA256SUMS` 只證明檔案未變；它不會把未核准的內容變成正式證據。所有條件通過前維持 serving blocked，且不要建立 production assignment。

## Wizard

互動式本機引導位於 `.artifacts/ticket-09-ac5-7/setup-wizard.sh`。它只保存非秘密 metadata、provider 回覆與 checksums；不收集 token/key，也不寫 GitHub secrets。從 Git Bash 執行：

```bash
bash .artifacts/ticket-09-ac5-7/setup-wizard.sh
```

wizard 會誠實標記尚缺的 operator seams；不會修改 Ticket 09 checkboxes，也不會執行 production assignment。

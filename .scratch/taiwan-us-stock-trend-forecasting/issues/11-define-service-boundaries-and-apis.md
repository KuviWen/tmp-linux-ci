# 定義服務模組邊界與 API 契約

Type: grilling
Status: resolved
Blocked by: 05, 08, 09, 10

## Question

資料擷取、標準化、特徵、訓練、推論、回測、模型治理、研究查詢及營運監控應如何切成深模組；哪些同步 REST、排程資產、不可變 artifact 與事件是穩定介面，如何避免首版被拆成難以本機運行的微服務網路？

## Comments

### Grilling round 1

使用者以「全部採建議」確認：

- 採資料供應、文件情報、特徵工廠、預測實驗室、正式預測、模型治理、研究查詢及營運控制八個同程序深模組；不以逐步 ETL 或通用 shared service 建立淺模組。
- REST 首版只穩定提供唯讀研究、唯讀營運健康及具稽核的人工模型核准／拒絕；管線執行留在 Dagster／CLI。
- 同步呼叫只處理唯讀查詢及小型交易；長任務是持久化工作，未來若由 REST 觸發只回傳 202 與工作識別碼。
- 模組間只交換冪等命令、小型結果、不可變 artifact／manifest reference 及版本化 outbox 事件；禁止跨模組 schema 寫入。
- 所有模組共用 application image，依 API、每日關鍵、維護、回填／訓練與 outbox relay 分程序；只有量測證明擴縮、故障或安全隔離需要時才拆部署。

### Grilling round 2

使用者以「全部採建議」確認：

- 資料供應外部只提供 `materialize(SourcePartitionRequest)` 與 `resolve(DataSelection)`；擷取、解碼、checkpoint、驗證與發布順序隱藏於模組內。
- 文件情報以 `process(DocumentBatchRequest)` 將已發布文件版本轉為不可變文件情報資料集；去重、模型與授權判斷是內部 seam。
- 特徵工廠以 `build(FeatureSnapshotRequest)` 供訓練、回測及正式預測共用，不輸出可變 DataFrame 或接受 provider 原生欄名。
- 預測實驗室以 `develop(TrainingIntentRef)` 封裝訓練、有限調參、baselines、ablations、校準與滾動回測，產生候選證據但不能治理模型。
- 正式預測以 `run(ForecastRunCommand)` 固定服務指派、建立特徵、推論、歸因及原子發布；研究 REST 不得即時執行模型。
- 模型治理只接受具前置條件的生命週期命令，不提供模型狀態 CRUD 或 `set_current_model`。
- 研究與營運 REST 只讀各自的 PostgreSQL projection，不在 request time 跨模組 fan-out；回應明示 projection 更新與 stale 狀態。

### Grilling round 3

使用者以「全部採建議」確認：

- `/api/v1` 只提供掛牌搜尋、研究預測／歷史／回測、營運健康及人工核准決定；掛牌路徑使用內部不可變 ID，ticker 只供搜尋。
- 比較矩陣固定同一資訊截止點、股票池版本、正式預測批次及 projection 版本；cursor 綁定快照，回應明示 resolved cutoff、更新時間、stale 與 ETag。
- HTTP 錯誤採 RFC 9457 `application/problem+json`；治理命令以 idempotency key、command ID 及 expected aggregate version／`If-Match` 防止重複與 lost update。
- Outbox 事件使用小型版本化封套、at-least-once 投遞、event ID 去重及 aggregate version 排序；大型內容只以 artifact／record ID 引用。
- Dagster 明確編排主流程；事件只啟動已登記後續工作、建立 projection、監控告警及支援未來跨程序 adapter，不以隱式事件編舞取代 workflow。
- Dagster asset 對齊 published dataset、文件情報、特徵快照、正式預測發布、成熟標籤、候選證據及 read projection 等深模組產物。
- 單模組以 PostgreSQL 交易提交權威狀態與 outbox；物件採 staging／verify／publish，跨模組採持久化 workflow 與冪等補償，不使用分散式交易。
- REST major path 只在破壞相容性時升版；REST、事件、命令與 artifact schema 各自版本化，新增可選欄位屬相容變更。

### Grilling round 4

使用者以「全部採建議」確認：

- 程式依賴固定為 entrypoint adapters → application workflows → module interfaces → implementations → infrastructure adapters；小型 `contracts` 只放不可變 ID、時間參照、封套及跨模組 DTO，禁止循環依賴與 shared ORM／repository。
- 長任務使用 `requested → leased → running → succeeded | failed | blocked | cancelled`；重試建立新 attempt，不覆寫舊證據，取消不回滾已發布 artifact。
- REST adapter 以 repo 內 OpenAPI 3.2.0 文件為契約來源，只產生傳輸 DTO／驗證器／客戶端，不產生領域模組。
- 核心研究 projection 與預測紀錄同交易發布；跨模組證據內容經 outbox 非同步豐富，回應分別標示核心與 evidence projection 版本。
- 只有完整、版本有效、政策／entitlement 仍可證明且未超過 freshness 上限的最後一致快照可以降級服務；否則 503，治理及譜系不完整一律 fail closed。
- 測試以深模組 interface 為主要表面，並包含真實 provider contract、OpenAPI、事件相容及端到端事故情境；只有 mock 不算 seam 已驗證。
- 模組拆為微服務前必須具備量測需求、程序內／遠端兩個 adapter、完整故障語意、非聊天式介面及本機 Compose 端到端能力，且不得改變領域契約。

### Grilling round 5

使用者以「全部採建議」確認：

- 掛牌搜尋接受 query、market 與 valid-at，歧義時回傳候選而不自動選取；後續資源一律使用不可變 `listing_id`，ticker 只作顯示別名。
- 預測 JSON 以具名 `up`／`flat`／`down` 的 0–1 數值表示機率，信心亦為 0–1；不可預測時省略機率並回傳結構化阻斷原因。
- Instant 使用具時區 RFC 3339 UTC，並另帶市場時區、交易日曆版本與明確 anchor／target session ID，不混用自然日期與首次取得時間。
- 集合使用綁定完整快照狀態的 opaque cursor，預設 50、上限 200，排序以不可變 ID 作 tie-breaker；單筆資源不嵌入無上限歷史或原文。
- 模組 outcome 統一為 invalid、not-found、conflict、blocked、policy-denied、transient-failure、permanent-failure 與 unavailable，由外圍 adapter 映射；只有 transient failure 自動重試。
- 來源政策與 entitlement 在 projection 建立及查詢時執行；REST 不回傳內部物件 URI、bucket、任意 presigned URL 或禁止展示原文。
- 本票券只固定監控、安全與部署所需的 seam；SLO／告警、RBAC／entitlement 及 Compose／Kubernetes 分別留給票券 12、13、14。

## Answer

共有理解由使用者以「繼續下一步」確認。首版採八個同程序深模組：資料供應、文件情報、特徵工廠、預測實驗室、正式預測、模型治理、研究查詢及營運控制；模組共用 application image，只以普通 interface、不可變 ID／artifact、具冪等鍵的命令、小型結果及 transactional outbox event 協作，禁止跨 schema 寫入、內部 HTTP 網路與隱式事件編舞。

Dagster／CLI 明確啟動持久化長任務；REST 只提供唯讀研究、唯讀營運健康及具條件前置與稽核的人工核准／拒絕。正式預測只能由批次 interface 固定服務指派、建立特徵、推論、歸因並原子發布，研究 REST 永不即時執行模型。核心研究 projection 與 PredictionRecord 同交易發布，跨模組證據可由 outbox 非同步豐富但必須保留版本及 stale 語意。

REST 固定 `/api/v1`、OpenAPI 3.2.0、RFC 9457 Problem Details、RFC 3339 UTC、內部不可變 ID、snapshot-bound opaque cursor、ETag／條件請求與具名 0–1 三分類機率；不可預測結果省略機率並提供結構化原因。事件採小型版本化封套、at-least-once 與 consumer 去重。微服務拆分必須先通過實際量測、穩定 interface、程序內／遠端雙 adapter、完整故障語意、非聊天式傳輸與 Compose 可運行等閘門。

- Design contract: [`docs/design/service-boundaries-and-api-contracts.md`](../../../docs/design/service-boundaries-and-api-contracts.md)
- ADR: [`docs/adr/0010-in-process-deep-modules-with-explicit-workflows.md`](../../../docs/adr/0010-in-process-deep-modules-with-explicit-workflows.md)

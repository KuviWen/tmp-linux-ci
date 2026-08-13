# 台美個股趨勢預測系統決策地圖

Label: wayfinder:map

## Destination

形成一份可直接交給 `/to-spec` 的建置決策集合：在既定產品與模型契約下，釐清資料來源、時間點資料模型、服務邊界、模型與回測、操作介面、監控、安全、容量及部署方案，讓後續規格與 tracer-bullet 實作票券不必再猜測關鍵設計。

## Notes

- 這是單一情境 repo；所有工作須先使用 [CONTEXT.md](../../CONTEXT.md) 的共用語彙，並遵守 [既有 ADR](../../docs/adr/)。
- 已確認的產品契約：生產導向 MVP、日終批次、台美可設定股票池、1／5／20 交易日趨勢、REST API 與繁體中文研究介面、不執行交易。
- 已確認的模型契約：時間點正確的三分類標籤、晚期多模態融合、校準機率與熵信心、SHAP 類解釋、週增量／月重訓／季調參、滾動回測、自動評估及人工升版。
- 已確認的營運契約：合法授權來源、七年證據鏈、收盤後兩小時內完成、資料與模型漂移監控、通用 webhook／SMTP 告警、Docker Compose 可完整運行且雲端中立。
- 研究票券只使用官方文件、原始規格、法規或第一方 API 文件；決策票券使用 `/grilling` 與 `/domain-modeling`，架構票券同時使用 `/codebase-design`，原型票券使用 `/prototype`。
- 本地票券以 `Blocked by` 表達依賴；研究代理各自只修改自己的研究文件與票券，地圖索引由主代理更新，避免並行衝突。

## Decisions so far

<!-- 決策細節只存在於已解決票券；此處僅保留一句摘要與連結。 -->

- [盤點可合法使用的台灣市場資料來源](issues/01-inventory-taiwan-data-sources.md) — OGDL 資料可依顯名義務保存衍生，但一般交易所網頁不得爬取；七年行情／完整財報與新聞全文須以明確契約取得保存及模型使用權。
- [盤點可合法使用的美國與全球資料來源](issues/02-inventory-us-global-data-sources.md) — SEC、BLS、BEA 與資料集級核准的國際統計可構成公開基線；全美行情、公司行動、新聞與法人共識必須使用部署者自備商業授權的 adapter。
- [比較雲端中立的資料與 MLOps 元件](issues/03-compare-cloud-neutral-platform-components.md) — 後續選型應以獨立可測的編排、物件、SQL、tracking／registry、品質／漂移及 OTLP ports 比較候選，並納入 MinIO 封存、CockroachDB 授權與 Windows／Kubernetes 營運成本。
- [定義標的身分、交易日曆與時間點資料契約](issues/04-define-point-in-time-data-contracts.md) — 以發行人／證券／掛牌不可變身分及 append-only 雙時間證據鏈建立時間點視圖，並以 Collector／Decoder seam 統一來源政策、版本、checkpoint 與完整性語意。
- [選擇資料擷取、編排與儲存架構](issues/05-choose-data-platform-architecture.md) — 採 Dagster 編排的模組化單體，以 PostgreSQL 保存權威狀態、不可變 Parquet／物件保存大量資料，並以深模組、manifest 與 provider contracts 隔離儲存和分析 adapter。
- [定義新聞、公告與財報處理管線](issues/06-define-document-processing-pipeline.md) — 以版本化文件證據、授權模式、可 abstain 的多語處理與不可變處理組合建立可追溯文件情報，去重、標的連結、事件及市場影響均為不覆寫來源的衍生版本。
- [驗證趨勢標籤與防洩漏回測契約](issues/07-validate-labels-and-backtest-contract.md) — 採版本化波動門檻與精確 realized-session 端點，並以 7 年訓練、1 年校準／驗證、固定 20-session purge／embargo 及季度測試保存可稽核的防洩漏回測摺。
- [選擇多模態模型與訓練設計](issues/08-choose-multimodal-model-design.md) — 採共享台美 encoder／gated fusion 加市場 adapter 與校準器，透過深 TrendForecaster seam 產生可降級、可解釋且離線可復現的三期間預測，並以有限算力、baselines 及消融約束複雜度。
- [設計模型登錄、升版與復現流程](issues/09-design-model-lifecycle.md) — 以 canonical append-only ledger、內容定址成品、版本化 hard gates、職責分離核准、五次 shadow 及原子服務指派治理所有訓練觸發，registry／cache 只作可重建投影且緊急權限不能繞過升版。
- [原型化預測解釋與研究介面](issues/10-prototype-research-experience.md) — 首版採比較矩陣作跨市場篩選入口、研究工作台作單一掛牌深度視圖；證據卷宗延後為唯讀匯出，且信心、資料支援與阻斷狀態維持獨立語意。
- [定義服務模組邊界與 API 契約](issues/11-define-service-boundaries-and-apis.md) — 首版以八個程序內深模組、明確 Dagster workflow、不可變 artifact 及 transactional outbox 協作；REST 只讀已發布研究／營運 projection 與提交稽核核准，並以拆分閘門避免提早形成微服務網路。
- [定義可觀測性、來源健康與異常處理](issues/12-define-observability-and-source-health.md) — 以 application ledger 保存七年來源健康、SLO、漂移、事故與通知真相，短期 telemetry 經標準 seam 投影；採分層健康、日終 SLO、集中恢復、因果事故、漂移治理及六層 dashboard。
- [定義安全、憑證、授權與保存控制](issues/13-define-security-and-entitlement-controls.md) — 正式 OIDC／工作負載身分經單一授權 module 交集行動權限、來源使用資格、來源政策及資料保護類別；secret、七年證據鏈、政策性刪除、防竄改稽核與簽章供應鏈均 fail closed。
- [選擇部署拓撲與容量邊界](issues/14-choose-deployment-topology.md) — Compose提供完整單機dev／pilot，Kubernetes提供單區三failure-domain HA與異地DR；相同深module經隔離runtime roles、簽章部署成品、復原集合及容量報告驗證T+105 baseline。
- [核准分階段架構並交付規格化](issues/15-approve-phased-architecture.md) — 以五個雙市場垂直階段、穩定trace IDs、歷史證據等級、bootstrap gate、外部dependency與acceptance bundles形成唯一交接契約，地圖現已可直接交給`/to-spec`。

## Remaining deployment inputs and product blockers

- 實際入選來源確定後，需重新檢視各來源的特殊更正語意、回補限制、速率限制與服務時限。
- 台美歷史行情／公司行動、台美新聞與公司級法人 consensus 的商業授權，是對應產品階段的外部 dependency gate；它們已有 fail-closed adapter 契約，不是尚待選擇的架構形狀。
- 代表性資料與模型基準量測完成後，需把全市場擴展所需的算力、儲存與成本上限轉成可驗收容量目標。
- 正式部署環境確定後，需把雲端中立拓撲映射成供應商特定的 IaC 與受管服務替代方案。

## Out of scope

- 此地圖只產生建置決策，不撰寫正式功能程式；實作在 `/to-spec`、`/to-tickets` 與 `/implement` 階段進行。
- 自動下單、券商連線、投資組合代客管理及個人化投資建議。
- 盤中即時資料、低延遲交易訊號與高頻模型。
- 代替部署者購買資料授權、建立付費供應商帳號或接受供應商契約。
- 法律、稅務、投資或法遵認證意見。

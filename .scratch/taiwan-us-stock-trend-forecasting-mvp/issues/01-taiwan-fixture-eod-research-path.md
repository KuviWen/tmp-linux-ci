# 01 — 台股 fixture 完整日終研究路徑

**What to build:** 讓一個 XTAI fixture 掛牌從版本化來源政策、時間點證據與公司行動，經資料集、特徵快照及固定 fixture 服務指派，發布不可變三期間預測紀錄，並能在 REST、繁中比較矩陣／標的研究頁、來源健康與安全稽核中完整展示。這是 P1 最小工程脊柱，不得被視為正式資料或正式預測。

**Blocked by:** None — can start immediately

**Trace IDs:** `P1-ENTRY-01`, `P1-TRACE-TW-01`

Status: ready-for-agent

- [ ] 乾淨環境能啟動完成此 tracer 所需的 Compose、PostgreSQL、filesystem ObjectRepository、Dagster workflow、REST 與繁中研究介面。
- [ ] Fixture 以內部發行人、證券及掛牌身分表示，包含 ticker 有效期、XTAI 交易日曆、至少 253 個未調整 sessions、公司行動與內部調整版本。
- [ ] Fixture collection 產生原始資料物件、來源紀錄版本、正規化紀錄版本、擷取收據、涵蓋報告、真實平台控制的首次取得時間及 committed checkpoint。
- [ ] 日終 workflow 固定 `fixture` 執行用途、不可變資料選擇、FeatureSnapshot、FixtureTrendForecaster artifact 與 fixture 服務指派，並交易發布每個 1／5／20 期間的結果或機器可讀不可用原因。
- [ ] 可用結果含具名 up／flat／down 機率、獨立信心分數與資料支援狀態；必要資料不足時不回傳替代機率。
- [ ] 比較矩陣與標的研究頁能以不可變 listing ID 顯示 cutoff、三期間結果、fixture 標章、FeatureSnapshot、ModelArtifact、服務指派、資料集及原始證據 ID，且 URL 重載保留狀態。
- [ ] Fixture 結果無法進入 production route、production PredictionRecord、正式匯出或模型升版，嘗試時回傳穩定拒絕並留下 audit／health 證據。
- [ ] 端到端測試只由外部可觀察的 workflow、REST、UI、權威紀錄、health 與 audit 驗收，不依賴模組內部呼叫順序或手改資料庫。

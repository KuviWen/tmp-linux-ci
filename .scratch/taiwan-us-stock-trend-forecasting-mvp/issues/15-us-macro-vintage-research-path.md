# 15 — 美國總體 vintage 研究路徑

**What to build:** 將 BLS 與 BEA 的核准 release／vintage 經合法擷取、時間點版本、資料集資格及特徵建構，進入美股 shadow 預測支援與繁中研究介面，並證明 revision 不改寫舊 FeatureSnapshot 或 PredictionRecord。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Trace IDs:** `P3-TRACE-MACRO-01`

Status: ready-for-agent

- [ ] BLS 與 BEA 資料集分別記錄政策、release calendar、series／table 身分、頻率、單位、季調、revision、保存及模型資格。
- [ ] 每個取得版本保存 RawArtifact、RetrievalReceipt、first-observed time、business-valid period、release／vintage ID、CoverageReport 及 policy version。
- [ ] Historical reconstruction 只接受合格 platform-observed／archive-attested vintage；只有目前最終值或發布日期的資料維持 isolated research。
- [ ] FeatureFactory 產生版本化美國總體特徵並保存 age、availability、quality、actual version 及 calendar／processing lineage。
- [ ] 美股 shadow 預測與繁中研究頁顯示總體模態支援、實際 vintage、政策與 lineage；late／missing／policy-blocked 狀態不冒充有效空集合。
- [ ] BLS／BEA 後續修訂建立新資料集與 FeatureSnapshot，既有 prediction history、backtest fold 與研究頁歷史版本保持不變。
- [ ] Provider、time-zone、revision、coverage 與 REST contract tests 證明美國路徑使用與票 14 相同的外部資料與支援語意。

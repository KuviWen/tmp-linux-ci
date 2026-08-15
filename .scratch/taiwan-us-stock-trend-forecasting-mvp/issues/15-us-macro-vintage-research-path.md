# 15 — 美國總體 vintage 研究路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 將免註冊的 BLS v1 官方 release／revision 經合法擷取、時間點版本、資料集資格及特徵建構，進入美股 shadow 預測支援與繁中研究介面，並證明 revision 不改寫舊 FeatureSnapshot 或 PredictionRecord；需要 API key 的 BEA API、FRED／ALFRED 與需登入的 IMF 介面固定為 excluded／unavailable，不形成待辦。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Trace IDs:** `P3-TRACE-MACRO-01`

Status: ready-for-agent

- [ ] 逐一 allowlist 的 BLS v1 series 記錄官方政策、release calendar、series 身分、頻率、單位、季調、revision、保存及模型資格；只使用免註冊請求邊界。
- [ ] 每個取得版本保存 RawArtifact、RetrievalReceipt、first-observed time、business-valid period、release／vintage ID、CoverageReport 及 policy version。
- [ ] Historical reconstruction 只接受合格 platform-observed／archive-attested vintage；只有目前最終值或發布日期的資料維持 isolated research。
- [ ] FeatureFactory 產生版本化美國總體特徵並保存 age、availability、quality、actual version 及 calendar／processing lineage。
- [ ] 美股 shadow 預測與繁中研究頁顯示總體模態支援、實際 vintage、政策與 lineage；late／missing／policy-blocked 狀態不冒充有效空集合。
- [ ] BLS 後續修訂建立新資料集與 FeatureSnapshot，既有 prediction history、backtest fold 與研究頁歷史版本保持不變；被排除來源不建立 collector、credential 或等待狀態。
- [ ] Provider、time-zone、revision、coverage 與 REST contract tests 證明美國路徑使用與票 14 相同的外部資料與支援語意。

# 15 — 美國總體 vintage 研究路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 將 BLS v1 官方 release／revision 經合法擷取、時間點版本、資料集資格及特徵建構，進入美股 shadow 預測支援與繁中研究介面，並證明 revision 不改寫舊 FeatureSnapshot 或 PredictionRecord；BEA API、FRED／ALFRED、IMF 或其他零付費介面不因需要自助憑證而固定排除，但只有用途資格與憑證就緒者可啟用，其他維持 optional `credential_required`／unavailable。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Trace IDs:** `P3-TRACE-MACRO-01`

Status: ready-for-agent

- [ ] 逐一 allowlist 的 BLS v1 series 記錄官方政策、release calendar、series 身分、頻率、單位、季調、revision、保存及模型資格；其他 provider 只有通過相同 dataset-specific 零付費用途資格才可加入。
- [ ] 每個取得版本保存 RawArtifact、RetrievalReceipt、first-observed time、business-valid period、release／vintage ID、CoverageReport 及 policy version。
- [ ] Historical reconstruction 只接受合格 platform-observed／archive-attested vintage；只有目前最終值或發布日期的資料維持 isolated research。
- [ ] FeatureFactory 產生版本化美國總體特徵並保存 age、availability、quality、actual version 及 calendar／processing lineage。
- [ ] 美股 shadow 預測與繁中研究頁顯示總體模態支援、實際 vintage、政策與 lineage；late／missing／policy-blocked 狀態不冒充有效空集合。
- [ ] BLS 後續修訂建立新資料集與 FeatureSnapshot，既有 prediction history、backtest fold 與研究頁歷史版本保持不變；未啟用的 credentialed optional source 明示 `credential_required`／unavailable，不阻斷本 ticket 或產生採購等待狀態。
- [ ] Provider、time-zone、revision、coverage 與 REST contract tests 證明美國路徑使用與票 14 相同的外部資料與支援語意。

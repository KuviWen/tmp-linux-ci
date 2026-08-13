# 31 — 台灣 600 掛牌完整產品資料資格路徑

**What to build:** 將台灣股票池擴展到 600 個掛牌，逐一驗證身分、交易日曆、symbol history、公司行動、session、來源使用資格、行情、官方文件、新聞、基本面與總體涵蓋，並從正式 EOD 到繁中研究介面保留 full／degraded／unavailable／policy-blocked 的精確支援語意。

**Blocked by:** 30 — 發布 P4 受治理神經模型 acceptance bundle

**External gates:** `DEP-MKT-TW-01`, `DEP-NEWS-TW-01`

**Trace IDs:** `P5-ENTRY-01`, `P5-ENTRY-02`

Status: ready-for-agent

- [ ] 版本化台灣 600 股票池依產業、規模、流動性、掛牌年齡、股別、ticker／轉板／下市生命週期、文件密度及支援狀態具代表性，不只選存活大型股。
- [ ] 每個掛牌逐一通過發行人／證券／掛牌身分、外部識別碼有效期、XTAI calendar、unadjusted sessions、公司行動、AdjustmentVersion、來源政策、entitlement 與 CoverageReport qualification。
- [ ] 240 以上有效 sessions 為 full price support、60–239 為 degraded、少於 60 或 anchor 缺價為 unavailable；結果保留機器原因，不從股票池靜默移除。
- [ ] 官方文件、新聞、FinancialFact 與總體 optional modalities 的每個 expected partition 區分 valid empty、uncovered、late、policy blocked、processing failure 及 qualified content。
- [ ] `DEP-MKT-TW-01` 或 `DEP-NEWS-TW-01` 未合格時，相關資料集／特徵／完整產品資格 fail closed；不得以網站爬蟲、測試 key 或人工下載補足。
- [ ] 既有 current ModelArtifact 不因股票池擴大自動取得服務資格；FeatureSnapshots、OOD／support 及 shadow 結果保存新 support-profile lineage。
- [ ] 正式／shadow EOD 對 600 掛牌逐一產生三期間結果或機器原因，REST／繁中矩陣與標的頁能查到實際支援、cutoff、assignment、datasets 及政策狀態。
- [ ] OperationsControl 提供 600 掛牌 qualification summary、缺漏、來源健康、事故、恢復及 audit，任何錯誤身分／政策／譜系或錯誤發布為 hard failure。

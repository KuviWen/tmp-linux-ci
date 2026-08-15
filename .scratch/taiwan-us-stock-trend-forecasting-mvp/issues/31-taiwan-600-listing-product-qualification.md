# 31 — 台灣零成本完整支援池資料資格路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 將台灣股票池擴展到官方零成本來源實際可完整資格審查的最大支援集合，逐一驗證身分、交易日曆、名稱／symbol history、公司行動、session、公開來源使用依據、行情、官方文件、基本面與總體涵蓋，並從正式 EOD 到繁中研究介面保留 full／degraded／unavailable／policy-blocked 的精確支援語意。

**Blocked by:** 30 — 發布 P4 受治理神經模型 acceptance bundle

**Source scope:** 官方零成本來源與 `official-documents-only`；不存在 external gate

**Trace IDs:** `P5-ENTRY-01`, `P5-ENTRY-02`

Status: ready-for-agent

- [ ] 版本化台灣支援池依官方零成本來源的實際 listing coverage 建立，記錄產業、規模、流動性、掛牌年齡、股別、名稱／轉板／下市生命週期、文件密度及支援狀態；不承諾固定 600，也不只選存活大型股。
- [ ] 每個掛牌逐一通過發行人／證券／掛牌身分、外部識別碼有效期、XTAI calendar、unadjusted sessions、公司行動、AdjustmentVersion、公開來源政策與 CoverageReport qualification，不要求 principal entitlement。
- [ ] 240 以上有效 sessions 為 full price support、60–239 為 degraded、少於 60 或 anchor 缺價為 unavailable；結果保留機器原因，不從股票池靜默移除。
- [ ] 官方文件、FinancialFact 與總體 optional modalities 的每個 expected partition 區分 valid empty、uncovered、late、policy blocked、processing failure 及 qualified content；商業新聞固定為 excluded／not-applicable。
- [ ] 公開來源政策或實得涵蓋不足時，相關資料集／特徵／掛牌資格 fail closed；不得以網站爬蟲、測試 key、人工下載、付費來源或採購待辦補足。
- [ ] 既有 current ModelArtifact 不因股票池擴大自動取得服務資格；FeatureSnapshots、OOD／support 及 shadow 結果保存新 support-profile lineage。
- [ ] 正式／shadow EOD 對支援池每個掛牌逐一產生三期間結果或機器原因，REST／繁中矩陣與標的頁能查到實際支援、cutoff、assignment、datasets 及政策狀態。
- [ ] OperationsControl 提供完整支援池 qualification summary、缺漏、來源健康、事故、恢復及 audit，任何錯誤身分／政策／譜系或錯誤發布為 hard failure。

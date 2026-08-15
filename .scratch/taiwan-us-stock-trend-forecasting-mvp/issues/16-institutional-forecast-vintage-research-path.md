# 16 — 國際機構預測 vintage 研究路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 若 OECD 或另一機構提供用途合格的零付費 forecast dataflow，從 dataset allowlist、必要 credential readiness、vintage qualification 與來源政策轉為不可變跨市場總體預測特徵；否則此 optional 模態端到端 `credential_required`／`unavailable`，不阻斷完整產品。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Optional source basis:** 合格零付費 forecast dataflow；不存在付費、採購或 sales gate

**Trace IDs:** `P3-TRACE-MACRO-01`, `GATE-POLICY-01`

Status: ready-for-agent

- [ ] Source steward 對選定 dataflow 建立有效 allowlist、來源使用條款、方案／部署主體分類、保存、模型使用、vintage／revision 及顯名證據；可要求自助帳號與來源憑證但不得要求付費、sales／人工核准或協商契約，其他 dataflow 不自動取得相同資格。
- [ ] Adapter 保存每個 release／vintage 的原始證據、first-observed time、period、geography、measure、unit、revision、涵蓋及 policy version。
- [ ] 合格 `platform_observed`／`archive_attested` vintage 可形成正式歷史特徵；current-only、unknown 或 self-asserted 版本只能隔離或阻斷。
- [ ] FeatureFactory 將核准 forecast 特徵提供給台美掛牌，保存實際 vintage、age、availability、quality 與 source-policy lineage，不把最新預測回填到舊 cutoff。
- [ ] 台美 shadow 預測及研究頁顯示相同的 institutional 模態支援與實際 vintage；無合格零成本 dataflow 時兩者均明示 optional unavailable 且不產生衍生內容或產品 blocker。
- [ ] 來源資格到期或政策撤回時阻止新的特徵／推論／展示，建立影響評估並保留舊決策，不由 authorization module 直接刪除內容。
- [ ] 端到端驗收同時展示合格 dataflow 與未合格 dataflow 的 allow／deny 路徑，證明 dataset-level qualification 不可被 provider-wide 設定繞過。

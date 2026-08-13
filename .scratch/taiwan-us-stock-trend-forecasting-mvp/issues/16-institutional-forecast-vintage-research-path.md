# 16 — 國際機構預測 vintage 研究路徑

**What to build:** 將一個權利核准的 OECD Economic Outlook forecast dataflow 從 dataset allowlist、vintage qualification 與來源政策，轉為不可變跨市場總體預測特徵並呈現在台美 shadow 預測支援及研究頁；未取得合格權利／vintage 時必須端到端 `policy_blocked`。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**External gate:** `DEP-INSTITUTIONAL-01`

**Trace IDs:** `P3-TRACE-MACRO-01`, `GATE-POLICY-01`

Status: ready-for-agent

- [ ] Source steward 對選定 dataflow 建立有效 allowlist、條款／契約、保存、模型使用、vintage／revision 及顯名證據；其他 OECD dataflow 不自動取得相同資格。
- [ ] Adapter 保存每個 release／vintage 的原始證據、first-observed time、period、geography、measure、unit、revision、涵蓋及 policy version。
- [ ] 合格 `platform_observed`／`archive_attested` vintage 可形成正式歷史特徵；current-only、unknown 或 self-asserted 版本只能隔離或阻斷。
- [ ] FeatureFactory 將核准 forecast 特徵提供給台美掛牌，保存實際 vintage、age、availability、quality 與 source-policy lineage，不把最新預測回填到舊 cutoff。
- [ ] 台美 shadow 預測及研究頁顯示相同的 institutional 模態支援與實際 vintage；缺資格時兩者均明示 policy blocked 且不產生受限衍生內容。
- [ ] 來源資格到期或政策撤回時阻止新的特徵／推論／展示，建立影響評估並保留舊決策，不由 authorization module 直接刪除內容。
- [ ] 端到端驗收同時展示合格 dataflow 與未合格 dataflow 的 allow／deny 路徑，證明 dataset-level qualification 不可被 provider-wide 設定繞過。

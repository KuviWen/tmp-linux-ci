# 23 — 基本面與總體 neural 增量路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 在 neural price-only 路徑上分別加入合格 FinancialFact 與總體 vintage 表示，使每個增量候選完成相同回測、校準、消融、shadow 及研究支援展示；optional modality 缺失仍能降級，且不一次導入文件或完整 fusion。

**Blocked by:** 22 — Neural price-only TrendForecaster 路徑

**Trace IDs:** `P4-TRACE-NEURAL-01`

Status: ready-for-agent

- [ ] Fundamentals 使用資訊截止點前合格的共同 FinancialFact schema、最多八季 vintage、period／unit／currency／revision／age／quality／policy masks，不讀 provider-native 欄位。
- [ ] Macro 使用最多 24 個月頻與八個季頻合格 vintages，保留 release／revision、age、availability、quality、source policy 及市場對應。
- [ ] Fundamentals-only increment 與 fundamentals＋macro increment 分別建立 immutable TrainingIntent、ModelArtifact、EvaluationReport 及 ablation，不把兩步藏在單一最終結果。
- [ ] 所有 normalization、missing statistics、winsorization 與 class weights 只由各 fold training 資料擬合並包入 artifact。
- [ ] Optional fundamentals／macro missing、late、stale、uncovered 或 policy blocked 使用不同 masks／support reasons，不以零值或中性值替代；價量仍是必要 residual anchor。
- [ ] 每個增量使用相同三 seeds、具樣本支援的 market／horizon loss／calibration、verified-history manifest 可形成的 eligible tests、cost scenarios 與 hard-gate evidence，並與 price-only neural 及 logistic 比較。
- [ ] 完整 measured support pool 的 shadow predictions、research matrix／listing page 與 OperationsControl 能顯示實際模態支援、age、vintage、候選差異及完整 lineage。
- [ ] 任一增量未改善或 gate 失敗時不阻止保存其研究證據，也不取代現行 baseline 或擴張 TrendForecaster interface。

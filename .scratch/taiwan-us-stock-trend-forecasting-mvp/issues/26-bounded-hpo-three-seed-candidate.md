# 26 — 受限 HPO 到正式三-seed 候選

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 對共享 gated forecaster 執行只見 validation、最多 30 trials 的受限 HPO；依預先登錄規則選出設定後，另建正式 TrainingIntent 並以三 seeds 從頭訓練、校準、回測，產生可進 gate 的候選，任何 trial checkpoint 都無法核准或服務。

**Blocked by:** 25 — 共享台美 quality-aware gated forecaster

**Trace IDs:** `P4-TRACE-HPO-01`

Status: ready-for-agent

- [ ] HPO search space 只含已核准 channels、hidden size、learning rate、weight decay、dropout、modality dropout 與 auxiliary-loss ranges，不搜尋 label、calendar、input windows 或資料資格。
- [ ] 所有 trials 使用相同 chronological training／validation 邊界、預先登錄 seeds 與本機資源限制；fold manifest 中所有 once-only test labels、metrics、class balance 及 economics 完全不可見。
- [ ] 每次最多 30 個可 early-stop trials，保存 parameters、validation metrics、resource、attempt、artifact checksum 及 trial lineage，重試不覆寫前次證據。
- [ ] 選定設定後建立新的 immutable TrainingIntent，固定全部 data／fold／policy／runtime／hardware／precision manifests 並從頭訓練三 seeds。
- [ ] Trial artifact、checkpoint、registry alias 或操作者選定 run 均無法進 GateDecision、ApprovalDecision、ServingAssignment 或 production／shadow route。
- [ ] 正式候選只對具足夠樣本的 cells 擬合 calibrators，執行 verified-history manifest 中全部 eligible tests、baselines／ablations、cost scenarios、support slices 與 operational checks，產生 immutable ModelArtifact／EvaluationReport；不足時不建立候選。
- [ ] ForecastLab／ModelGovernance／research governance view 能清楚區分 HPO intent、trial attempts、selected config intent 與正式候選，並保留失敗／superseded outcomes。
- [ ] 端到端測試證明任何 test-data access、超出 search space、超過 trial budget、missing manifest 或直接 promote trial 的行為均 fail closed 並留下 audit。

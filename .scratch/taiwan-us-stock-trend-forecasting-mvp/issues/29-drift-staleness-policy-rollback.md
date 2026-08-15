# 29 — 漂移、過時、政策撤回與安全回退路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 將完整候選走過 hard gates、人工核准、五次 shadow 與原子 assignment，並以正式 EOD 展示漂移週窗、45／90／120 日過時階梯、來源政策撤回、可立即驗證 serving failure 及核准 rollback target 的不同處置；漂移只建立候選，不能自行升版或回退。

**Blocked by:** 27 — 無網路 ModelArtifact 重現路徑, 28 — Integrated Gradients 到文件證據研究路徑

**Trace IDs:** `P4-TRACE-DRIFT-01`, `GATE-MODEL-01`, `GATE-OPS-01`

Status: ready-for-agent

- [ ] 候選通過 immutable GatePolicyVersion 的 eligibility／reproduction、statistical、calibration、economic、stability／coverage、operational／security 及 material-improvement veto gates 後，才可進人工核准與 shadow。
- [ ] ApprovalDecision 採職責分離、exact evidence binding、expected assignment、非空理由與七日效期；artifact／gate／policy／security 改變會使核准失效。
- [ ] 五次 eligible EOD shadow 完成 load、schema、10-minute SLA、ForecastBatch invariants、attribution 及 incumbent-difference evidence，下一個未開始批次才原子切換 assignment。
- [ ] Feature／support／prediction／mature-label drift 依既定兩個連續週窗與資料充足門檻只建立 `drift_early` TrainingIntent；來源 outage／schema／coverage／policy 問題先修資料。
- [ ] Artifact load／checksum、整批 schema、invalid probability、不可用率增加或連續 CPU SLA 等可立即驗證 failure 才可 compare-and-swap 到每日驗證仍合格的 approved rollback target。
- [ ] 現行模型 45／90／120 日過時狀態依契約告警、顯示或停止正式預測，不因 stale 自動改寫機率或選擇未核准 artifact。
- [ ] 來源政策撤回建立 PolicyImpactAssessment，quarantine 受影響 dataset／artifact／assignment；只有仍 eligible approved target 可接手，否則停止正式預測。
- [ ] REST／UI、OperationsControl、canonical lifecycle、incident、notification 與 audit 分別呈現 drift、stale、serving failure、rollback、policy quarantine 及 blocked serving 的正確狀態。
- [ ] Promotion、rollback、policy stop 或 retrospective replay 都不重算既有正式 PredictionRecord；break-glass 只能 stop 或切回 eligible approved target，不能繞 gate。

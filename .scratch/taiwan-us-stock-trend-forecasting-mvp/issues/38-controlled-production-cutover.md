# 38 — 受控 production cutover 路徑

**What to build:** 使用同一簽章 deployment artifact 與經重驗的 formal data／ModelArtifacts，按正式信任根、唯讀研究、ingestion／projection、五次台美 EOD shadow、原子 production assignment、正式 publication／notification 的順序完成 pilot 到 production 切換；每個階段具 smoke、觀察、單一部署世代及可驗證回退。

**Blocked by:** 37 — 復原集合與跨區 failover／failback 路徑

**Trace IDs:** `P5-TRACE-CUTOVER-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] Cutover 只提升通過 gates 的 signed deployment artifact、typed configuration、qualified content-addressed datasets／features／models，不複製 pilot volume、test identity／secret 或未審查資料。
- [ ] Production trust roots、OIDC clients、workload identities、KMS／secret references、database／object／backup、source policies／entitlements、audit／telemetry 在啟動前獨立建立與驗證。
- [ ] 先開 read-only research 並驗 2,000-listing search／matrix／listing page、authorization、policy display、ETag、latency、lineage 及 accessibility，不啟用 ingestion 或 publication。
- [ ] 再開 source ingestion、qualification、outbox 與 projections，驗 source health、coverage、policy、audit 及 deletion state；不完整必要來源仍阻止下游。
- [ ] 以正式資料完成至少五次 eligible dual-market EOD shadow，每次通過 T+105、10-minute inference＋attribution、REST SLO、result-or-reason、notification、audit 及 no-leakage checks。
- [ ] Model approver 核准後，只在下一個未開始 EOD boundary 原子建立 production assignment；pilot、shadow、recovery region 不得同時發布同一市場 batch。
- [ ] 正式 publication／notification 開啟後，研究 UI、PredictionRecords、incidents、notifications、audit 及 capacity／security signals 在觀察窗內符合既定 gates。
- [ ] 每階段失敗有明確 stop／rollback，恢復上一個 signed compatible artifact 或 approved assignment 而不回滾 canonical ledger、datasets、audit 或既有 predictions。
- [ ] 完整 cutover／rollback walkthrough 由接手 platform、data、model、source、security owners 操作並產生 immutable handoff evidence。

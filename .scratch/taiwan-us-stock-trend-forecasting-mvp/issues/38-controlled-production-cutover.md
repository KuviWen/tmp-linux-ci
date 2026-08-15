# 38 — 受控 production cutover 路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 使用同一內容定址 deployment artifact 與經重驗的 formal data／ModelArtifacts，按本機信任設定、唯讀研究、ingestion／projection、五次台美 EOD shadow、原子 production assignment、正式 publication／notification 的順序完成內部 cutover；每個階段具 smoke、觀察、單一部署世代及可驗證回退。

**Blocked by:** 37 — 復原集合與本機 backup／restore 路徑

**Trace IDs:** `P5-TRACE-CUTOVER-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] Cutover 只提升通過 gates 的 content-addressed deployment artifact、typed configuration、qualified datasets／features／models，不複製 test identity／secret 或未審查資料；optional local signature 不是必要條件。
- [ ] 本機 trust configuration、workload action grants、source credential readiness／local secret references、database／object／backup、source-use policies、audit／telemetry 在啟動前獨立建立與驗證，不要求 OIDC／KMS 或付費 entitlement。
- [ ] 先開 read-only research 並驗完整支援池 search／matrix／listing page、authorization、policy display、ETag、latency、lineage 及 accessibility，不啟用 ingestion 或 publication。
- [ ] 再開 source ingestion、qualification、outbox 與 projections，驗 source health、coverage、policy、audit 及 deletion state；不完整必要來源仍阻止下游。
- [ ] 以正式資料完成至少五次 eligible dual-market EOD shadow，每次通過 T+105、10-minute inference＋attribution、REST SLO、result-or-reason、notification、audit 及 no-leakage checks。
- [ ] Model approver 核准後，只在下一個未開始 EOD boundary 原子建立 production assignment；prior generation 與 shadow 不得同時發布同一市場 batch。
- [ ] 正式 publication／notification 開啟後，研究 UI、PredictionRecords、incidents、notifications、audit 及 capacity／security signals 在觀察窗內符合既定 gates。
- [ ] 每階段失敗有明確 stop／rollback，恢復上一個 content-addressed compatible artifact 或 approved assignment 而不回滾 canonical ledger、datasets、audit 或既有 predictions。
- [ ] 完整 cutover／rollback walkthrough 由接手 platform、data、model、source、security owners 操作並產生 immutable handoff evidence。

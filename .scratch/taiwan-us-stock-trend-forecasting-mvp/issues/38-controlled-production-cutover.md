# 38 — 受控 production cutover 路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 使用同一內容定址 deployment artifact 與經重驗的 formal data／ModelArtifacts，按本機信任設定、唯讀研究、ingestion／projection、五次台美 EOD shadow、原子 production assignment、正式 publication／notification 的順序完成內部 cutover；每個階段具 smoke、觀察、單一部署世代及可驗證回退。

**Blocked by:** 37 — 復原集合與本機 backup／restore 路徑

**Trace IDs:** `P5-TRACE-CUTOVER-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] Cutover 只提升通過 gates 的 content-addressed deployment artifact、typed configuration、qualified datasets／features／models，不複製 test identity／secret 或未審查資料；optional local signature 不是必要條件。
- [ ] 本機 trust configuration、workload action grants、local secret references、database／object／backup、public-source policies、audit／telemetry 在啟動前獨立建立與驗證，不要求 OIDC／KMS／entitlement。
- [ ] 先開 read-only research 並驗完整支援池 search／matrix／listing page、authorization、policy display、ETag、latency、lineage 及 accessibility，不啟用 ingestion 或 publication。
- [ ] 再開 source ingestion、qualification、outbox 與 projections，驗 source health、coverage、policy、audit 及 deletion state；不完整必要來源仍阻止下游。
- [ ] 以正式資料完成至少五次 eligible dual-market EOD shadow，每次通過 T+105、10-minute inference＋attribution、REST SLO、result-or-reason、notification、audit 及 no-leakage checks。
- [ ] Model approver 核准後，只在下一個未開始 EOD boundary 原子建立 production assignment；prior generation 與 shadow 不得同時發布同一市場 batch。
- [ ] 正式 publication／notification 開啟後，研究 UI、PredictionRecords、incidents、notifications、audit 及 capacity／security signals 在觀察窗內符合既定 gates。
- [ ] 每階段失敗有明確 stop／rollback，恢復上一個 content-addressed compatible artifact 或 approved assignment 而不回滾 canonical ledger、datasets、audit 或既有 predictions。
- [ ] 完整 cutover／rollback walkthrough 由接手 platform、data、model、source、security owners 操作並產生 immutable handoff evidence。

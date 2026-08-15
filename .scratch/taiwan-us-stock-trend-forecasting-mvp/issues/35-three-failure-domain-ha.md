# 35 — 本機 Compose 韌性與故障恢復路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 在已通過 baseline 容量的本機 Compose profile 中，實際注入 API、relay、Dagster、worker、container、PostgreSQL connection／restart 與 ObjectRepository 故障，從 EOD 到研究服務證明 lease／fencing、append-only correctness、repair、backup restore 與受控 unavailable；不宣稱 Kubernetes、三 failure domains 或 HA。

**Blocked by:** 34 — 零成本內部 baseline 容量路徑

**Trace IDs:** `P5-TRACE-HA-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`

Status: ready-for-agent

- [ ] Compose profile 部署 PostgreSQL、filesystem ObjectRepository、application roles、Dagster、relay 與 observability，使用獨立 local identities／roles／resource limits。
- [ ] API／BFF、relay、Dagster webserver／code location 與 telemetry 關鍵程序具 restart／health 契約；Dagster daemon 保持 single-active 語意。
- [ ] 注入 process／container failure 時，work lease、fencing token、outbox idempotency、pinned batch assignment 與 single-writer authority 防止雙重或 stale 發布。
- [ ] PostgreSQL connection／restart 與 connection-budget 故障測試驗證 transaction correctness、已提交 state 保存及可恢復 service，不聲稱未量測 RPO。
- [ ] ObjectRepository missing／corruption／permission 故障測試驗證 checksum、dataset degraded／unavailable、backup restore 及禁止 silent latest fallback。
- [ ] 故障期間 baseline EOD 仍產生完整 result-or-reason 或受控事故，不發生錯誤 prediction、policy bypass、lost audit、duplicate notification 或 OOM loop。
- [ ] Daily-critical correctness、protection window、backfill checkpoint／recreate 與 API 行為在故障／repair 期間依核准本機容量報告驗證。
- [ ] 每個 fault scenario 產生 canonical incident、health、SLO、recovery、audit 及 immutable resilience evidence，dashboard／logs／CI output 只作 projection。

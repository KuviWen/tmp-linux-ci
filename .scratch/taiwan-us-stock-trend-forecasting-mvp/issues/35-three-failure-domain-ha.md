# 35 — 三 failure-domain HA 故障路徑

**What to build:** 在已通過 baseline 容量的單區三 failure-domain production staging 中，實際注入 API、relay、Dagster、worker、pod、node、zone、PostgreSQL、PgBouncer 與 object storage 故障，從 EOD 到研究服務證明 topology、PDB、lease／fencing、同步資料保護、repair 與保留容量，而不是以 replica 數量宣稱 HA。

**Blocked by:** 34 — 2,000 掛牌 production baseline 容量路徑

**Trace IDs:** `P5-TRACE-HA-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`

Status: ready-for-agent

- [ ] Production staging 跨三 failure domains 部署 stateless roles、CloudNativePG reference 或合格 managed adapter、PgBouncer、replicated object storage、Dagster、relay 與 observability，使用獨立 identities／quotas／topology constraints。
- [ ] API／BFF、relay、Dagster webserver／code location、OTel gateway 等關鍵程序有 topology spread、anti-affinity、PDB 與至少一個 ready replica；Dagster daemon 保持 single-active 語意。
- [ ] 注入 process／pod／node／zone failure 時，work lease、fencing token、outbox idempotency、pinned batch assignment 與 single-writer authority 防止雙重或 stale 發布。
- [ ] PostgreSQL primary／standby、PgBouncer 及 connection budget 故障測試驗證已提交 application state 的區域內 RPO≈0、transaction correctness 及可恢復 service。
- [ ] Object server／gateway／replica 故障測試驗證 read-after-write、checksum、replica repair、dataset degraded／unavailable 行為及禁止 silent latest fallback。
- [ ] 故障期間 baseline EOD 仍產生完整 result-or-reason 或受控事故，不發生錯誤 prediction、policy bypass、lost audit、duplicate notification 或 OOM loop。
- [ ] Daily-critical capacity、protection window、backfill checkpoint／recreate 與 API SLO 在故障／repair 期間依核准容量報告外部驗證。
- [ ] 每個 fault scenario 產生 canonical incident、health、SLO、recovery、audit 及 immutable HA evidence，dashboard／logs／CI output 只作 projection。

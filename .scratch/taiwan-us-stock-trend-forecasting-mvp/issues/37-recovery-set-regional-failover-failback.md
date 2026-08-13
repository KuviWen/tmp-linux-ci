# 37 — 復原集合與跨區 failover／failback 路徑

**What to build:** 建立把 application database target、object replication watermark、deletion-ledger sequence、設定與 deployment digests 綁定的版本化 RecoverySet；由正式 PredictionRecord 完整還原證據鏈、先重播政策性刪除，再以人工 SEV1／雙人核准完成暖資料冷應用次要區域的 failover 與 failback，實測 RPO／RTO 及單一部署世代。

**Blocked by:** 35 — 三 failure-domain HA 故障路徑, 36 — 正式安全、供應鏈與政策性刪除路徑

**Trace IDs:** `P5-TRACE-DR-01`, `GATE-POLICY-01`, `GATE-SEC-01`, `GATE-DEPLOY-01`

Status: ready-for-agent

- [ ] RecoverySet 固定 PostgreSQL recovery target、object inventory／watermark、deletion-ledger sequence、configuration、image／deployment digests、signatures 及建立／核准 evidence。
- [ ] Restore 隔離舊部署並前進 deployment generation，恢復 identity／keys、PostgreSQL、objects、audit，再重播 deletion ledger、驗 reference graph／checksums、重建 projections。
- [ ] 缺失 object／artifact 引用明示 unavailable／degraded，只有 reference graph 完整的 PredictionRecord／batch 可重新服務，不讓較新 database state 偽裝完整。
- [ ] 恢復順序先開 read-only research，再開 ingestion／projection／scheduling，最後才允許 formal publication；每步都有 smoke、health、audit 與 rollback point。
- [ ] Region failover 綁 SEV1，由 platform admin＋security admin 雙人核准，source steward 確認地域來源資格，並驗 OIDC、KMS／secret、egress、DNS、certificates、signatures、notifications。
- [ ] 實測 application PostgreSQL RPO `<=15 分鐘`、object／artifact RPO `<=24 小時`、完整 research-service RTO `<=4 小時`，報告資料缺口及有效完整能力水位。
- [ ] Failback 前停止新寫入、排空 outbox、複寫增量、驗 reference graph，並撤銷舊 active deployment 的 workload identity／fencing authority；任一時點只有一個 deployment generation 可寫入／發布。
- [ ] 演練涵蓋遺失 object、損壞 backup、KMS outage、audit chain、含 tombstone backup 及 restore 後重新刪除，結果進 immutable DR／incident／audit evidence。

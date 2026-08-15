# 37 — 復原集合與本機 backup／restore 路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 建立把 application database target、object backup inventory／watermark、deletion-ledger sequence、設定與 deployment digests 綁定的版本化 RecoverySet；由正式 PredictionRecord 完整還原證據鏈、先重播政策性刪除，再在本機隔離 restore 環境完成受控回復，量測實際資料損失邊界、restore time 及單一部署世代。

**Blocked by:** 35 — 本機 Compose 韌性與故障恢復路徑, 36 — 正式安全、供應鏈與政策性刪除路徑

**Trace IDs:** `P5-TRACE-DR-01`, `GATE-POLICY-01`, `GATE-SEC-01`, `GATE-DEPLOY-01`

Status: ready-for-agent

- [ ] RecoverySet 固定 PostgreSQL recovery target、object inventory／watermark、deletion-ledger sequence、configuration、image／deployment digests 及建立／核准 evidence；optional local signature 不影響可還原性。
- [ ] Restore 隔離舊部署並前進 deployment generation，恢復 identity／keys、PostgreSQL、objects、audit，再重播 deletion ledger、驗 reference graph／checksums、重建 projections。
- [ ] 缺失 object／artifact 引用明示 unavailable／degraded，只有 reference graph 完整的 PredictionRecord／batch 可重新服務，不讓較新 database state 偽裝完整。
- [ ] 恢復順序先開 read-only research，再開 ingestion／projection／scheduling，最後才允許 formal publication；每步都有 smoke、health、audit 與 rollback point。
- [ ] Restore 由 platform admin＋security responsibility 雙重 action grants 核准，重驗 local identity／secret、source policies、egress、notifications 與 deployment generation，不要求區域、OIDC、KMS、DNS 或 certificate 服務。
- [ ] 實測並記錄 application PostgreSQL／object backup 的資料損失邊界與完整 research-service restore time；threshold 由實際本機 profile 的 CapacityReport 固定，不預設雲端 RPO／RTO。
- [ ] 返回服務前停止舊 generation 寫入、排空 outbox、驗 reference graph，並撤銷舊 active deployment 的 fencing authority；任一時點只有一個 deployment generation 可寫入／發布。
- [ ] 演練涵蓋遺失 object、損壞 backup、local secret unavailable、audit chain、含 tombstone backup 及 restore 後重新刪除，結果進 immutable recovery／incident／audit evidence。

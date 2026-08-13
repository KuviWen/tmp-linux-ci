# 36 — 正式安全、供應鏈與政策性刪除路徑

**What to build:** 在完整台美產品資料路徑上實施 production OIDC AAL2／WebAuthn、工作負載身分、單一授權交集、SecretProvider、default-deny network、簽章供應鏈、append-only audit 與 policy deletion，從登入／workflow 到資料、模型、PredictionRecord、研究展示及刪除證明完整驗收。

**Blocked by:** 31 — 台灣 600 掛牌完整產品資料資格路徑, 32 — 美國 1,400 掛牌完整產品資料資格路徑, 33 — 國際預測與公司級法人 consensus 路徑

**Trace IDs:** `P5-TRACE-SEC-01`, `GATE-POLICY-01`, `GATE-SEC-01`

Status: ready-for-agent

- [ ] 正式互動式登入使用核准 OIDC Authorization Code＋PKCE、AAL2、server-side session；高風險 grant／policy／approval／promotion／export／deletion 要求 15 分鐘內 WebAuthn step-up。
- [ ] REST、Dagster、source、document sandbox、feature、training、inference、governance、relay 使用獨立短效 workload identities、database roles、object prefixes 及最小 ActionGrants。
- [ ] AuthorizationPolicy 對 action grant、source entitlement、source policy、purpose、environment、DataProtectionClass 及 resource state 取交集，未知／到期／conflict fail closed；完整 decision matrix 通過。
- [ ] Secret values 不進 repository、configuration、database、work command、artifact、REST、outbox、logs／traces；rotation、revocation、provider outage 及 redaction 具端到端 evidence。
- [ ] Production network default deny；source／notification／OIDC／KMS／secret／time egress 分離 allowlist，training／inference／document sandbox 無網路，SSRF／redirect／DNS-rebinding scenarios 被拒。
- [ ] Application、UI、model 及 deployment artifacts 以 digest pin、signature、SBOM、provenance、dependency／base locks、CVE／license evidence 驗證；unsafe model format、unsigned artifact、Critical／High finding 是 veto。
- [ ] SecurityAudit 對 authentication、authorization、secret checkout metadata、restricted read、governance、export、deletion、deployment 100% 記錄，transactional append-only sequence／hash chain／daily signature 可驗證。
- [ ] Policy deletion 從 verify、tombstone／block、lineage impact、dual approval、primary／replica／cache／index／derived／model deletion、backup replay 到 DeletionCertificate 完成；沒有合格 rollback model 時停止 formal prediction。
- [ ] 獨立 penetration assessment 覆蓋 authentication、authorization、IDOR、CSRF、SSRF、injection、malicious file、model artifact、network、supply chain、audit 與 deletion；Critical／High 修復並重測前不得通過。

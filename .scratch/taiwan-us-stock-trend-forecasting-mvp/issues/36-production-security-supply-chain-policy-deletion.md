# 36 — 正式安全、供應鏈與政策性刪除路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 在完整零成本台美資料路徑上實施本機可信身份、工作負載 action grants、單一授權交集、本機 secret handling、可行的 default-deny／egress allowlist、內容定址供應鏈、append-only audit 與 policy deletion，從登入／workflow 到資料、模型、PredictionRecord、研究展示及刪除證明完整驗收。

**Blocked by:** 31 — 台灣零成本完整支援池資料資格路徑, 32 — 美國零成本完整支援池資料資格路徑, 33 — 官方機構預測 optional 路徑與 consensus 排除

**Trace IDs:** `P5-TRACE-SEC-01`, `GATE-POLICY-01`, `GATE-SEC-01`

Status: ready-for-agent

- [ ] 互動式登入使用僅限 loopback／內網的本機可信身份與 server-side session；高風險 grant／policy／approval／promotion／export／deletion 要求重新驗證及獨立 action grant，不要求外部 OIDC／WebAuthn 服務。
- [ ] REST、Dagster、source、document sandbox、feature、training、inference、governance、relay 使用獨立短效 workload identities、database roles、object prefixes 及最小 ActionGrants。
- [ ] AuthorizationPolicy 對 action grant、來源使用資格／SourcePolicyVersion、purpose、environment、DataProtectionClass 及 resource state 取交集；公開與零付費方案皆逐 dataset 判定，憑證或 `open_data_terms` 不會取代用途資格，未知／到期／conflict 仍 fail closed。
- [ ] Source credential 與其他 secret values 不進 repository、configuration、database、work command、artifact、REST response、outbox、logs／traces；程式頁面的 write-only set／rotate／revoke／validate、provider-owned reapplication link、SecretProvider outage 及 redaction 具端到端 evidence。
- [ ] 本機 runtime 在可支援範圍 default deny；source／notification／time egress 分離 allowlist，training／inference／document sandbox 無網路，SSRF／redirect／DNS-rebinding scenarios 被拒，不要求 OIDC／KMS egress。
- [ ] Application、UI、model 及 deployment artifacts 以 digest pin、SBOM、provenance、dependency／base locks、免費 CVE／license evidence 驗證；optional local signature 不成為外部依賴，unsafe format 與 Critical／High finding 是 veto。
- [ ] SecurityAudit 對 authentication、authorization、secret metadata、restricted read、governance、export、deletion、deployment 100% 記錄，transactional append-only sequence／hash chain 可驗證；外部簽章服務非必要。
- [ ] Policy deletion 從 verify、tombstone／block、lineage impact、dual approval、primary／replica／cache／index／derived／model deletion、backup replay 到 DeletionCertificate 完成；沒有合格 rollback model 時停止 formal prediction。
- [ ] 免費 open-source static／dependency／container／dynamic scanners 加內部手動 abuse-case assessment 覆蓋 authentication、authorization、IDOR、CSRF、SSRF、injection、malicious file、model artifact、network、supply chain、audit 與 deletion；Critical／High 修復並重測前不得通過，不要求付費獨立滲透測試。

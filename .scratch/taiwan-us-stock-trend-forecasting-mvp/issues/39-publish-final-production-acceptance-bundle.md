# 39 — 發布 P5 最終零成本內部 acceptance bundle

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 將完整產品公開來源使用依據、實際支援池資格、資料與模型譜系、現行／回退模型、shadow、本機容量、韌性、安全、政策性刪除、backup／restore、SLO／事故、受控 cutover、繁中 UI／REST 無障礙與內部核准，封裝為內容定址的最終 Go／No-Go acceptance bundle。

**Blocked by:** 38 — 受控 production cutover 路徑

**Source scope:** 官方零成本來源、official-documents-only、optional institutional forecast、consensus excluded；不存在 external gates

**Trace IDs:** `P5-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] 每項承諾來源具 dataset-level public-terms allowlist、policy version、coverage／vintage／deletion qualification；不支援市場或模態明示 unavailable／excluded，不等待契約或 entitlement。
- [ ] Bundle 綁定 P4 bundle、content-addressed deployment artifact、typed config、migrations、SBOM／provenance、source／support-pool／dataset／feature／fold manifests 及所有 trace IDs。
- [ ] 完整支援池 qualification、current／rollback ModelArtifacts、gates／approvals、five shadows、production assignments、PredictionRecords、research projections 與完整 lineage 可由 bundle 重現。
- [ ] CapacityReport、本機故障 evidence、免費工具／內部 security assessment、policy deletion／backup re-delete、RecoverySet、restore 及 cutover 全部通過 hard thresholds。
- [ ] 每市場 EOD SLO、REST／evidence projection SLO、source health、quality／drift、incidents、notifications、runbook drills、meta-observability 與 audit-chain verification 有 canonical evidence。
- [ ] 繁中 research matrix／listing page 在實測完整支援池 load 下正確顯示 probabilities、confidence、support、influences、allowed evidence、history、backtests、lineage、policy、stale／blocked states 及 accessibility。
- [ ] Platform、data、model、source 及 security 內部 responsibilities 的 approvals 被記錄；model approver、source policy、安全雙重 action grants 及 hard-gate veto 不被一般 ready flag 取代。
- [ ] 任何 erroneous publication、policy bypass、data loss、OOM loop、incomplete lineage、failed restore、missed RPO／RTO 或未修 Critical／High finding 都使 final bundle 為 No-Go。
- [ ] Passing／failed／blocked bundle 都內容定址、依引用生命週期保存並保留重現命令、responsibilities、validity、previous-bundle ID 及全部例外；optional local signature 不是外部依賴，後續失敗不修改既有 bundle。

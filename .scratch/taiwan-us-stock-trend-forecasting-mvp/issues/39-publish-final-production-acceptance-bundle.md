# 39 — 發布 P5 最終 production acceptance bundle

**What to build:** 將完整產品來源契約、2,000 掛牌資格、資料與模型譜系、現行／回退模型、shadow、容量、HA、安全、政策性刪除、restore／DR、SLO／事故、受控 cutover、繁中 UI／REST 無障礙與共同核准，封裝為內容定址、簽章、至少保存七年的最終 Go／No-Go acceptance bundle。

**Blocked by:** 38 — 受控 production cutover 路徑

**External gates:** `DEP-MKT-TW-01`, `DEP-MKT-US-01`, `DEP-NEWS-TW-01`, `DEP-NEWS-US-01`, `DEP-INSTITUTIONAL-01`, `DEP-CONSENSUS-01`

**Trace IDs:** `P5-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] 全部六項 external dependencies 有有效契約／allowlist、entitlement tests、policy versions、coverage／vintage／deletion qualification；缺任一項只能維持 restricted pilot，不能發布完整產品 passing bundle。
- [ ] Bundle 綁定 P4 bundle、signed deployment artifact、typed config、migrations、SBOM／provenance、source／stock-pool／dataset／feature／fold manifests 及所有 trace IDs。
- [ ] 2,000 listings qualification、current／rollback ModelArtifacts、gates／approvals、five shadows、production assignments、PredictionRecords、research projections 與完整 lineage 可由 bundle 重現。
- [ ] CapacityReport、三 failure-domain fault evidence、security／penetration assessment、policy deletion／backup re-delete、RecoverySet、restore、regional failover／failback 及 cutover 全部通過 hard thresholds。
- [ ] 每市場 EOD SLO、REST／evidence projection SLO、source health、quality／drift、incidents、notifications、runbook drills、meta-observability 與 audit-chain verification 有 canonical evidence。
- [ ] 繁中 research matrix／listing page 在 2,000-listing load 下正確顯示 probabilities、confidence、support、influences、allowed evidence、history、backtests、lineage、policy、stale／blocked states 及 accessibility。
- [ ] Platform、data、model、source 及 security owners 共同簽署；model approver、source steward、安全雙人控制及任何 hard-gate owner 的 veto 不被多數票取代。
- [ ] 任何 erroneous publication、policy bypass、data loss、OOM loop、incomplete lineage、failed restore、missed RPO／RTO 或未修 Critical／High finding 都使 final bundle 為 No-Go。
- [ ] Passing／failed／blocked bundle 都內容定址、簽章、保存至少七年並保留重現命令、owners、validity、previous-bundle ID 及全部例外；後續失敗不修改既有 bundle。

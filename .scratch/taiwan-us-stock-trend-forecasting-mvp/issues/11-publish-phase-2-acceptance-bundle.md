# 11 — 發布 P2 正式價量 baseline acceptance bundle

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 從通過的 P1 bundle 與合格官方零成本市場來源重現可支援的 10＋10 價量 baseline，展示歷史資格、bootstrap logistic、人工核准、shadow、assignment、正式 EOD、回退與故障恢復，並發布不可變 P2 acceptance bundle；任一市場缺乏足夠公開資料或 hard gate 不成立時發布精確 unavailable／blocked 證據。

**Blocked by:** 10 — 正式 EOD 服務指派與預測發布

**Public source bases:** 市場別官方公開資料使用依據；不存在 external gate

**Trace IDs:** `P2-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] Acceptance run 從 P1 passing bundle 與版本化 10＋10 manifest 重建兩市場合格歷史資料集、AdjustmentVersion、labels、FeatureSnapshots、folds、artifacts 及 evaluations。
- [ ] 台美市場的官方 dataset／distribution、公開條款／license、顯名、policy version、實得歷史深度及 backfill qualification 被綁入 bundle；未支援市場保持 unavailable，但不建立採購依賴。
- [ ] Logistic 通過 BootstrapGatePolicy、隔離重現、職責分離人工核准、rollback-target 驗證及兩市場各五次 eligible EOD shadow，才可建立首個 production assignment。
- [ ] 至少一個完整正式 EOD 展示 T+90 pin、T+120 result-or-reason、REST／UI、history／lineage、source health、notification、incident 與 audit。
- [ ] 故障驗收涵蓋來源 retry／circuit、quarantine／recovery、deadline、clock、checksum、policy expiry、projection、MLflow outage、promotion conflict 及安全回退。
- [ ] Compose small smoke 通過相同 module、REST、artifact、identity、object、telemetry 及 policy contracts；Kubernetes smoke 只可作不影響完成狀態的零成本選配。
- [ ] Bundle 綁定 P1 bundle ID、public-source-basis evidence、source／stock-pool／fold manifests、model／gate／approval／shadow／assignment、EOD、REST／UI、failure、security、deployment 及重現命令。
- [ ] Bundle 只在所有適用 hard gates 通過時標為 passing；logistic 未勝 class-prior 或任一資格失敗時保留現有 fixture／blocked 狀態並發布新的不可變失敗證據。

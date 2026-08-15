# 30 — 發布 P4 受治理神經模型 acceptance bundle

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 從 P3 passing bundle 重現 shared neural 候選的增量建構、三 seeds、verified-history manifest 中所有 eligible folds、baselines／ablations、bounded local HPO、具樣本支援的 calibrators、Integrated Gradients、無網路重現、hard gates、內部核准、shadow、promotion／rollback／stale／drift／policy 情境，並發布不可變 P4 acceptance bundle；歷史不足時不建立候選，neural 未勝出時允許 logistic 合法留任。

**Blocked by:** 29 — 漂移、過時、政策撤回與安全回退路徑

**Trace IDs:** `P4-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] Acceptance run 從 P3 bundle、精確 measured support-pool manifests、processing bundles、eligible baseline assignment 與 approved local runtime 重建 price-only、modality increments 及完整 shared candidate；無 eligible baseline 時維持 unavailable。
- [ ] 三 seeds、verified-history manifest 中全部 eligible folds、class-prior／logistic／price-only／逐模態 ablations、具樣本支援的 calibrators、cost／support slices、HPO lineage 及 immutable reports 全部可重現。
- [ ] No-network ReproductionRun、safe artifact formats、signature／SBOM／provenance、IG determinism／evidence／completeness 與 10-minute CPU inference＋attribution gate 通過。
- [ ] 全部 hard gates、職責分離核准、五次 eligible shadow、staged cold load、atomic assignment、rollback target、registry／cache reconciliation 及 prediction immutability 有外部證據。
- [ ] Drift、source outage、gate collapse、45／90／120-day stale、policy withdrawal、artifact corruption、schema／probability failure、automatic rollback 及 break-glass prohibition 情境通過。
- [ ] 研究介面在完整 measured support pool 展示 neural／baseline assignment 或 unavailable reason、三期間預測、信心、支援、主要影響、gate reliance、文件證據、history、backtests、gate／reproduction lineage 及繁中無障礙。
- [ ] Neural 未達任何 gate 或 material improvement 時，bundle 可在治理路徑全部通過且 logistic 留任的條件下標為 passing；不得為宣稱 neural 完成而降低 gate。
- [ ] Bundle 綁定 P3 bundle、all intents／attempts、artifacts、evaluations、gates、approvals、shadows、assignments、rollback、drift／stale／policy、SLO／security／UI 及重現命令，失敗只建立新證據。

# 21 — 發布 P3 多模態研究 pilot acceptance bundle

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 從 P2 passing bundle 重現實際支援池的多模態 pilot，展示台美官方文件、FinancialFact、總體 vintage、商業新聞排除、雙語文件品質、sandbox、review、multimodal ablation、五次 EOD shadow 及完整繁中研究 MVP，並發布不可變 P3 acceptance bundle。

**Blocked by:** 20 — Multimodal logistic 到完整研究介面

**Source scope:** 官方文件是完整範圍；institutional forecast 是 optional，商業新聞沒有 external gate

**Trace IDs:** `P3-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] Acceptance run 從 P2 bundle 與 measured support-pool manifest 建立合格市場的正式官方文件、FinancialFact、總體 vintage、DocumentIntelligence、FeatureSnapshots、eligible multimodal candidates 及 research projections；不合格市場維持 unavailable。
- [ ] 中英文 golden thresholds、sandbox hostile-input tests、document correction／withdrawal、policy modes、review decisions、source deletion 及 backup restore then re-delete 全部通過。
- [ ] Multimodal logistic 在實際 verified-history manifest 可形成的所有 eligible folds 完成 price-only 與逐模態 ablations、三 seeds、具樣本支援的 calibrators、hard gates、內部核准及合格市場 shadow；歷史或樣本不足時不建立候選，未勝出時 baseline 可合法留任。
- [ ] 研究 MVP 在完整 measured support pool 展示搜尋／矩陣／標的頁、三期間預測或 unavailable reason、信心、支援、影響因素、允許證據、fundamentals、vintage、history、backtest、lineage 及繁中無障礙。
- [ ] 每市場 EOD T+120、REST SLO、evidence projection、文件／來源健康、incident／notification 及 audit 有 canonical evidence，錯公司、錯證據、錯政策為 hard failure。
- [ ] 產品與 bundle 明示 `official-documents-only` 是完整產品範圍，新聞模態為 excluded／not-applicable 且不保留 P5 blocker。
- [ ] Bundle 綁定 P2 bundle、public-source bases、source／processing／stock-pool／feature／fold manifests、goldens、sandbox、review、ablation、shadow、SLO、security、deletion、UI／REST 與重現命令。
- [ ] 任何失敗建立新的 blocked／failed bundle evidence，不修改 P2 bundle、既有 datasets、models、decisions 或 production predictions。

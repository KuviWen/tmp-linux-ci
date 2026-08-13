# 21 — 發布 P3 多模態研究 pilot acceptance bundle

**What to build:** 從 P2 passing bundle 重現 100＋100 多模態 pilot，展示台美官方文件、FinancialFact、總體 vintage、授權新聞或明確阻斷、雙語文件品質、sandbox、review、multimodal ablation、五次 EOD shadow 及完整繁中研究 MVP，並發布不可變 P3 acceptance bundle。

**Blocked by:** 20 — Multimodal logistic 到完整研究介面

**External gates:** `DEP-INSTITUTIONAL-01`; `DEP-NEWS-TW-01` 與 `DEP-NEWS-US-01` 決定是否可宣稱完整新聞整合

**Trace IDs:** `P3-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] Acceptance run 從 P2 bundle 與 100＋100 manifest 建立台美正式文件、FinancialFact、總體 vintage、DocumentIntelligence、FeatureSnapshots、multimodal candidates 及 research projections。
- [ ] 中英文 golden thresholds、sandbox hostile-input tests、document correction／withdrawal、policy modes、review decisions、source deletion 及 backup restore then re-delete 全部通過。
- [ ] Multimodal logistic 完成 price-only 與逐模態 ablations、三 seeds、八季 folds、六 calibrators、hard gates、人工核准及兩市場各五次 EOD shadow；未勝出時 baseline 可合法留任。
- [ ] 研究 MVP 在 100＋100 規模展示搜尋／矩陣／標的頁、三期間預測、信心、支援、影響因素、允許證據、fundamentals、vintage、history、backtest、lineage 及繁中無障礙。
- [ ] 每市場 EOD T+120、REST SLO、evidence projection、文件／來源健康、incident／notification 及 audit 有 canonical evidence，錯公司、錯證據、錯政策為 hard failure。
- [ ] 若任一新聞 entitlement 未合格，產品與 bundle 明示 `official-documents-only` 並保留 P5 blocker；不得宣稱新聞整合完成，但 P3 official-document pilot 可在其他 gates 通過時發布。
- [ ] Bundle 綁定 P2 bundle、dependencies、source／processing／stock-pool／feature／fold manifests、goldens、sandbox、review、ablation、shadow、SLO、security、deletion、UI／REST 與重現命令。
- [ ] 任何失敗建立新的 blocked／failed bundle evidence，不修改 P2 bundle、既有 datasets、models、decisions 或 production predictions。

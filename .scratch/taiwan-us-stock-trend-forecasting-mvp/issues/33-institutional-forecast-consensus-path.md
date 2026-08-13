# 33 — 國際預測與公司級法人 consensus 路徑

**What to build:** 將至少一個權利核准的國際機構 forecast dataflow 與一個涵蓋台美目標、具歷史 vintage 及建模／衍生權的公司級法人 consensus 來源，從政策與 revision qualification 走到特徵、模型支援、正式／shadow 預測及研究證據；任一依賴不足時阻斷完整產品資格。

**Blocked by:** 30 — 發布 P4 受治理神經模型 acceptance bundle

**External gates:** `DEP-INSTITUTIONAL-01`, `DEP-CONSENSUS-01`

**Trace IDs:** `P5-ENTRY-01`, `GATE-POLICY-01`

Status: ready-for-agent

- [ ] Institutional 與 consensus dataset 分別建立有效契約／allowlist、保存、vintage、revision、model／derived、display、attribution、delete 及 coverage evidence，不共享模糊 provider-wide 權利。
- [ ] Adapter 保存原始 release／snapshot、first-observed time、business-valid period、vintage、revision／withdrawal、unit、currency、horizon、coverage、policy 及 receipts。
- [ ] Historical reconstruction 只使用合格 platform-observed／archive-attested vintages，current-final consensus 或自行宣稱 as-of 版本只能隔離研究。
- [ ] Consensus 與 institutional 特徵保存實際 vintage、age、availability、quality、coverage、source policy 及 issuer／listing linkage，不以最新值回填舊 cutoff。
- [ ] 台美 FeatureSnapshots、shadow／formal support 與研究頁能顯示實際來源類別、vintage、支援／阻斷原因及 lineage，不洩漏合約禁止的 raw consensus。
- [ ] Entitlement 缺失、到期、revision 語意不足或 coverage 不合格時，完整產品 qualification、new training／inference／display 一致 fail closed，現行受影響 artifacts 進 impact assessment。
- [ ] 來源更正、撤回與政策刪除建立新 versions、quarantine／deletion evidence 及必要的 model／assignment 處置，不改寫既有 predictions。
- [ ] 端到端測試證明一個符合與一個不符合資格的 series／consensus snapshot 會在 DataSupply、FeatureFactory、ForecastExecution、ResearchQuery、OperationsControl 與 audit 得到一致結果。

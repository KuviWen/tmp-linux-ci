# 07 — 美股合格行情到研究資格狀態

**What to build:** 將一個部署者契約提供的美股 EOD、公司行動與 symbol history adapter 接入共同 DataSupply 契約，建立合格美股資料集、內部調整版本及研究／營運資格狀態；外部權利不足時維持可驗證的 `policy_blocked` 路徑。

**Blocked by:** 05 — 發布 P1 雙市場工程脊柱 acceptance bundle

**External gate:** `DEP-MKT-US-01`

**Trace IDs:** `P2-ENTRY-01`, `P2-ENTRY-02`, `P2-TRACE-US-01`

Status: ready-for-agent

- [ ] 10＋10 manifest 的美股部分涵蓋主要交易所普通股、股別／ADR、ticker 變更、公司行動、半日市、暫停及歷史下市標的。
- [ ] 契約 adapter 通過與台灣相同的 Collector／Decoder、checkpoint、rate、policy、coverage、revision、identity 及 reference-graph contracts。
- [ ] 未調整 EOD、公司行動、symbol history 與美國交易日曆建立不可變資料集及內部 AdjustmentVersion，不以免費網站或 provider latest adjusted close 補足。
- [ ] 合格資料集的來源政策、涵蓋、schema、integrity、權利與譜系能由研究支援及 OperationsControl 查詢外部驗證。
- [ ] `DEP-MKT-US-01` 未核實、到期或限制用途時，新的擷取、特徵、訓練及研究展示一致 `policy_blocked`，且不洩漏來源 entitlement 細節。
- [ ] Correction、symbol reuse、跨掛牌身分衝突、公司行動缺失與不完整分區不會形成合格正式輸入。
- [ ] 端到端展示證明美國來源 adapter 可替換但共同資料集、預測支援、REST 與 audit 語意不變。

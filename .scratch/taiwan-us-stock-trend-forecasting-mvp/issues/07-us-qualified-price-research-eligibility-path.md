# 07 — 美股合格行情到研究資格狀態

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 評估並接入符合零成本邊界的美國官方 EOD、公司行動與 symbol-history distributions；若沒有單一官方免帳號來源提供必要證據，則透過共同 DataSupply 介面發布可驗證的 `unavailable`／`policy_blocked` 資格狀態，而不是等待付費 feed。

**Blocked by:** 05 — 發布 P1 雙市場工程脊柱 acceptance bundle

**Public source basis:** 待官方零成本 dataset qualification 建立；不存在 external gate

**Trace IDs:** `P2-ENTRY-01`, `P2-ENTRY-02`, `P2-TRACE-US-01`

Status: ready-for-agent

- [ ] 10＋10 manifest 的美股部分涵蓋主要交易所普通股、股別／ADR、ticker 變更、公司行動、半日市、暫停及歷史下市標的。
- [ ] 官方零成本 adapter 或明確 unavailable adapter 通過與台灣相同的 Collector／Decoder、checkpoint、rate、policy、coverage、revision、identity 及 reference-graph interface contracts。
- [ ] 未調整 EOD、公司行動、symbol history 與美國交易日曆建立不可變資料集及內部 AdjustmentVersion，不以免費網站或 provider latest adjusted close 補足。
- [ ] 合格或 unavailable 資料集的公開使用依據、涵蓋、schema、integrity、實得歷史深度與譜系能由研究支援及 OperationsControl 查詢驗證。
- [ ] 沒有合格官方零成本來源、公開條款撤回或限制用途時，新的擷取、特徵、訓練及研究展示一致 unavailable／policy-blocked，且不產生申請、契約或 entitlement 待辦。
- [ ] Correction、symbol reuse、跨掛牌身分衝突、公司行動缺失與不完整分區不會形成合格正式輸入。
- [ ] 端到端展示證明美國來源 adapter 可替換但共同資料集、預測支援、REST 與 audit 語意不變。

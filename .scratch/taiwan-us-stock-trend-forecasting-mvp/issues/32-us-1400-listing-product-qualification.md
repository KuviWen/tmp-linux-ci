# 32 — 美國 1,400 掛牌完整產品資料資格路徑

**What to build:** 將美國股票池擴展到 1,400 個掛牌，逐一驗證身分、交易日曆、symbol history、公司行動、session、來源使用資格及所有承諾模態，並從正式 EOD 到繁中研究介面保留與台灣相同的支援及 fail-closed 語意。

**Blocked by:** 30 — 發布 P4 受治理神經模型 acceptance bundle

**External gates:** `DEP-MKT-US-01`, `DEP-NEWS-US-01`

**Trace IDs:** `P5-ENTRY-01`, `P5-ENTRY-02`

Status: ready-for-agent

- [ ] 版本化美國 1,400 股票池涵蓋產業、規模、流動性、掛牌年齡、普通股／股別／ADR、ticker changes、delistings、document density 及支援狀態。
- [ ] 每個掛牌通過 issuer／security／listing、CIK／其他外部識別碼有效期、交易日曆、symbol history、unadjusted sessions、company actions、AdjustmentVersion、policy、entitlement 及 coverage qualification。
- [ ] Price support 使用相同 240／60 boundaries，半日市、暫停、臨時休市及 anchor 缺價不因方便而順延或移除掛牌。
- [ ] SEC／fundamentals、licensed news、BLS／BEA／international macro 等 optional partitions 均區分 valid empty、uncovered、late、policy blocked 與 processing failure。
- [ ] `DEP-MKT-US-01` 或 `DEP-NEWS-US-01` 未合格時，相關正式 dataset／feature／完整產品資格明確阻斷，不使用免費行情／新聞網站替代。
- [ ] 擴大股票池後建立新 FeatureSnapshots、support profile、training／evaluation evidence；100＋100 artifact 只能隔離 OOD／shadow，不能自動服務新增掛牌。
- [ ] 正式／shadow EOD、REST／繁中矩陣與標的頁對 1,400 掛牌逐一展示結果或原因、support、cutoff、assignment、datasets、document evidence 及 policy。
- [ ] Qualification summary、source health、缺漏、事故、恢復及 audit 使用與台灣相同的 OperationsControl 語意，任一市場平均不能掩蓋美國 slice failure。

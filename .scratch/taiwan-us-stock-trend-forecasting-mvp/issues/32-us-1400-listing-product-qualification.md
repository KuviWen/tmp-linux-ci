# 32 — 美國零成本完整支援池資料資格路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 將美國股票池擴展到合格零付費來源或 source bundle 實際可完整資格審查的最大支援集合，逐一驗證身分、交易日曆、symbol history、公司行動、session、來源使用依據、credential readiness 及所有承諾模態，並從正式 EOD 到繁中研究介面保留與台灣相同的支援及 fail-closed 語意。

**Blocked by:** 30 — 發布 P4 受治理神經模型 acceptance bundle

**Source scope:** 合格零付費行情來源與 `official-documents-only`；不存在付費、採購或 sales gate

**Trace IDs:** `P5-ENTRY-01`, `P5-ENTRY-02`

Status: ready-for-agent

- [ ] 版本化美國支援池依合格零付費來源的實際 listing coverage 建立，記錄產業、規模、流動性、掛牌年齡、普通股／股別／ADR、ticker changes、delistings、document density 及支援狀態，不承諾固定 1,400。
- [ ] 每個掛牌通過 issuer／security／listing、CIK／其他外部識別碼有效期、交易日曆、symbol history、unadjusted sessions、company actions、AdjustmentVersion、來源使用 policy、credential readiness 及 coverage qualification；憑證不等同 principal-specific 使用資格。
- [ ] Price support 使用相同 240／60 boundaries，半日市、暫停、臨時休市及 anchor 缺價不因方便而順延或移除掛牌。
- [ ] SEC／fundamentals、BLS v1／其他合格零付費 international macro 等 optional partitions 均區分 valid empty、uncovered、late、credential required、policy blocked 與 processing failure；BEA API、FRED／ALFRED、IMF 不因需要自助憑證而固定排除，付費商業新聞仍 excluded／not-applicable。
- [ ] 無合格零付費 EOD、有效來源憑證或其他承諾來源時，相關 dataset／feature／掛牌資格明確 `credential_required`／unavailable／blocked，不使用網站、借用／測試帳號、付費 feed 或採購 fallback。
- [ ] 支援池變更後建立新 FeatureSnapshots、support profile、training／evaluation evidence；前一 support-profile artifact 只能隔離 OOD／shadow，不能自動服務新增掛牌。
- [ ] 正式／shadow EOD、REST／繁中矩陣與標的頁對支援池每個掛牌逐一展示結果或原因、support、cutoff、assignment、datasets、document evidence 及 policy。
- [ ] Qualification summary、source health、缺漏、事故、恢復及 audit 使用與台灣相同的 OperationsControl 語意，任一市場平均不能掩蓋美國 slice failure。

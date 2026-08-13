# 06 — 台股合格行情到研究資格狀態

**What to build:** 將 TWSE 明確允許的當期來源與一個契約歷史行情 adapter 接入相同 DataSupply 契約，從未調整 EOD、公司行動與 symbol history 建立可追溯台股資料集、內部調整版本及研究／營運資格狀態；外部權利未成立時，完整路徑必須以 `policy_blocked` 結束而不使用替代網站。

**Blocked by:** 05 — 發布 P1 雙市場工程脊柱 acceptance bundle

**External gate:** `DEP-MKT-TW-01`

**Trace IDs:** `P2-ENTRY-01`, `P2-ENTRY-02`, `P2-TRACE-TW-01`

Status: ready-for-agent

- [ ] 版本化 10＋10 manifest 中的台股涵蓋普通股、ticker 變更、公司行動、暫停／半日市及訓練歷史中的下市案例。
- [ ] TWSE 當期與契約歷史 adapter 通過共同 Collector／Decoder、checkpoint、rate、policy、coverage、revision、identity 及 reference-graph contracts，供應商原生型別不外洩。
- [ ] 原始未調整價格、公司行動及掛牌生命週期建立不可變資料集與內部 AdjustmentVersion，供應商 adjusted close 只能交叉驗證。
- [ ] 來源權利完整時，資料集資格、涵蓋、schema、integrity、政策及歷史深度能由營運查詢與研究支援狀態追溯到原始證據。
- [ ] `DEP-MKT-TW-01` 未核實、到期或用途／保存不足時，adapter 維持 disabled／policy-blocked，REST／UI 顯示穩定原因且不暗用爬蟲、測試 key、人工下載或其他免費來源。
- [ ] Late、correction、withdrawal、身分歧義、缺公司行動與不完整涵蓋分別產生新版本、隔離或阻斷證據，不覆寫已發布資料。
- [ ] 台股資料路徑可由外部展示一個合格或受阻掛牌從來源政策、資料集與調整版本到研究／營運狀態的完整譜系。

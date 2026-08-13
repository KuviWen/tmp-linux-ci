# 02 — 美股 fixture 共用契約研究路徑

**What to build:** 讓一個美國主要交易所 fixture 掛牌沿用票 01 的共同身分、時間點、資料集、特徵、預測、研究與營運契約完成端到端日終路徑，同時把美國交易日曆、時區及公司行動差異限制在既有 adapter 與版本化規則內。

**Blocked by:** 01 — 台股 fixture 完整日終研究路徑

**Trace IDs:** `P1-TRACE-US-01`

Status: ready-for-agent

- [ ] 美國 fixture 使用同一組發行人、證券、掛牌、外部識別碼主張及 PredictionRecord 語意，不新增美國專用的平行領域模型。
- [ ] Fixture 包含美國交易場所時區、版本化交易日曆、至少 253 個未調整 sessions、ticker 有效期、公司行動、late／revision／missing 情境與明確來源政策。
- [ ] 同一 workflow 從擷取證據、資料集發布、FeatureSnapshot、fixture 推論到權威預測發布完成美國日終路徑，且日曆／時區不經 XTAI 或其他市場時區轉換。
- [ ] REST 與繁中介面能在同一比較矩陣同時顯示台灣與美國掛牌，兩者使用相同三期間機率、信心、支援、cutoff 與譜系欄位。
- [ ] 美國掛牌必要資料不足、公司行動缺件或日曆無法解析時，只影響該結果並提供穩定不可用原因，不拖垮已成功的台股結果。
- [ ] Provider／module contract tests 證明兩個市場經相同外部 interface 產生相同形狀的 outcomes、manifests、REST resources 與 audit evidence。
- [ ] 端到端驗收能具體展示美國 adapter 差異，但不存在模組間 HTTP、美國專用 prediction schema 或以 ticker 作權威路由。

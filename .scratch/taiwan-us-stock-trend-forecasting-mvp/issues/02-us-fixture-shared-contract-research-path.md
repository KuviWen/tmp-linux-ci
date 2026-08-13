# 02 — 美股 fixture 共用契約研究路徑

**What to build:** 讓一個美國主要交易所 fixture 掛牌沿用票 01 的共同身分、時間點、資料集、特徵、預測、研究與營運契約完成端到端日終路徑，同時把美國交易日曆、時區及公司行動差異限制在既有 adapter 與版本化規則內。

**Blocked by:** 01 — 台股 fixture 完整日終研究路徑

**Trace IDs:** `P1-TRACE-US-01`

Status: ready-for-agent

- [x] 美國 fixture 使用同一組發行人、證券、掛牌、外部識別碼主張及 PredictionRecord 語意，不新增美國專用的平行領域模型。
- [x] Fixture 包含美國交易場所時區、版本化交易日曆、至少 253 個未調整 sessions、ticker 有效期、公司行動、late／revision／missing 情境與明確來源政策。
- [x] 同一 workflow 從擷取證據、資料集發布、FeatureSnapshot、fixture 推論到權威預測發布完成美國日終路徑，且日曆／時區不經 XTAI 或其他市場時區轉換。
- [x] REST 與繁中介面能在同一比較矩陣同時顯示台灣與美國掛牌，兩者使用相同三期間機率、信心、支援、cutoff 與譜系欄位。
- [x] 美國掛牌必要資料不足、公司行動缺件或日曆無法解析時，只影響該結果並提供穩定不可用原因，不拖垮已成功的台股結果。
- [x] Provider／module contract tests 證明兩個市場經相同外部 interface 產生相同形狀的 outcomes、manifests、REST resources 與 audit evidence。
- [x] 端到端驗收能具體展示美國 adapter 差異，但不存在模組間 HTTP、美國專用 prediction schema 或以 ticker 作權威路由。

## Implementation notes

- 公共 seam：`Application.run_fixture_eod(FixtureEodCommand(market=...))`、共用
  `FixtureMarketAdapter.load(cutoff)`、既有 REST/OpenAPI，以及
  `stock-forecasting acceptance ticket-02` 的 Compose 外部驗收入口。
- XNAS fixture 使用 `America/New_York`、版本化 XNAS session facts、split 調整與
  synthetic／非正式來源政策；兩市場仍發布同一 normalized schema 與研究資源形狀。
- 驗證：`pytest` 52 passed；`mypy src tests`、`ruff check .`、
  `ruff format --check .`、Alembic upgrade 與離線 wheel build 均通過。
- Compose：`docker compose config --quiet`、全服務 `up --build --wait` 與
  `docker compose --profile acceptance run --build --rm acceptance` 均通過；外部報告的
  13 個 checks 全為 true，完成後以 `docker compose down` 停止服務並保留 named volumes。
- Review 修正：provider market/date wiring 改為 fail-closed；缺失日曆不發布有效
  calendar artifact；兩市場沿共同 identity assertion shape 發布 fixture-only 外部識別碼；
  acceptance runner 的 application/scenario/HTTP 重複流程已抽為共用 helper；calendar 與
  company action 改由 immutable typed specs 作唯一來源，衍生 artifact、projection 與調整；
  provider 邊界驗證 selection/calendar 的 session kind 與開收盤時間完全一致。

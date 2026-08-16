# 08 — 雙市場歷史證據與回填資格路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 將台美行情、公司行動與身分回填依歷史證據等級進行資格審查，產生可重現的 historical-reconstruction 資料集、內部調整版本、成熟標籤、FeatureSnapshot 及 qualification report；不合格的目前最終值或自行宣稱證據必須一路阻斷到研究與營運介面。

**Blocked by:** 06 — 台股合格行情到研究資格狀態, 07 — 美股合格行情到研究資格狀態

**Trace IDs:** `P2-TRACE-PIT-01`, `GATE-PIT-01`, `GATE-DATA-01`

Status: ready-for-agent

- [x] 平台 qualification workflow 建立 HistoricalAvailabilityClaim，保存官方公開 archive／platform observation、版本／修訂語意、涵蓋、checksum、公開條款、有效期與證據等級；adapter 或人工輸入不能直接自我核准。
- [x] `platform_observed` 可供 production cutoff 與正式歷史；`archive_attested` 只供明示 historical reconstruction；`published_current_only` 及 unknown／self-asserted 不得進正式特徵、標籤或回測。
- [x] 新增、升級、撤銷或失效的歷史可得性主張只建立新資格與影響證據，不修改 first-observed time、observation sequence 或既有時間點視圖。
- [x] Qualification report 逐掛牌驗證 session、listing 生命週期、unadjusted price、公司行動、AdjustmentVersion、精確端點、修訂、來源政策及 claim ID，並列出每個排除原因。
- [x] 趨勢標籤依 XTAI／美國 realized sessions、固定 1／5／20 期間、版本化波動門檻及精確 t+h 端點產生；缺端點、20-session 歷史或政策資格時不順延或補值。
- [x] Historical-reconstruction FeatureSnapshot 與後續 fold manifest 保存實際資料集、claim、evidence level、calendar、adjustment、label rule、source policy 與 code provenance。
- [x] REST／UI 與 OperationsControl 能展示合格、隔離、invalid endpoint、policy blocked 及 claim 失效的狀態，而不把 retrospective reconstruction 顯示成當時 production 預測。
- [x] 端到端測試涵蓋 late backfill、current-final extract、archive 修訂、更正／撤回、臨時休市、ticker reuse 與公司行動缺件，並證明舊正式結果不被改寫。

## Implementation notes

- 公共 seam：`HistoricalEvidenceWorkflow.execute` 負責候選證據資格化與 append-only 影響證據；Research eligibility REST/UI 與 `OperationsControl.list_historical_qualifications` 負責外部投影；Compose `ticket-08-acceptance` 驗證部署邊界。
- 歷史資料以 `listing_id`／`security_id` 保存身分，ticker 僅是具有效期的 symbol；標籤只使用證據列出的 realized sessions，並以內部公司行動調整版本計算。
- 驗證：受影響測試 49 passed；非 PostgreSQL 全套 368 passed／1 deselected；PostgreSQL 1 passed／368 deselected；`mypy src tests`、`ruff check .`、`ruff format --check .`、Compose config、wheel build 均通過。
- 部署 acceptance：`docker compose -f compose.yaml --profile ticket-08-acceptance run --build --rm ticket-08-acceptance` 回傳 `status=passed`，六項 checks 全為 true；輸出標示 `evidence_kind=engineering_acceptance` 與 `formal_source_qualification=not_claimed`，不宣稱 fixture 為正式來源資格。

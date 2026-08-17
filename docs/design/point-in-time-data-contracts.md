# 標的身分、交易日曆與時間點資料契約

> **2026-08-15 product boundary:** ADR 0017、0018 與主 spec 的 `COST-0-01` 取代本文對付費／協商契約 backfill 與固定七年來源深度的必要假設；只有 platform-observed 或依合格零付費來源使用依據建立的 archive-attested evidence 可用，credential readiness 不會改寫 first-observed time，實際深度不足即 fail closed。本文的 first-observed time、business-valid time、revision 與不可回寫原則仍有效。

本文件記錄「定義標的身分、交易日曆與時間點資料契約」的決策。它規定所有來源 adapter 與下游資料處理都必須遵守的身分、時間、版本及完整性語意；來源特有欄位可以擴充，但不得削弱這些共同不變量。實作次序、歷史證據 entry gate 與 tracer bullet 由[分階段架構交接契約](phased-architecture-and-spec-handoff.md)固定。

## 身分模型

系統使用三個永不重用的內部 UUID：

| 身分 | 定義 | 生命週期 |
| --- | --- | --- |
| 發行人 `Issuer` | 發行證券的法律實體 | 更名沿用；法律實體合併或消滅依事件關係連接新舊身分 |
| 證券 `Security` | 一組特定經濟權利 | 普通股、不同股別與 ADR 分開；分拆、合併或權利實質改變時建立新身分 |
| 掛牌 `Listing` | 證券在特定交易場所及幣別下的可交易呈現 | ticker 變更或暫停／恢復交易時沿用；轉板、變更交易幣別或下市後重新上市時建立新身分 |

Ticker、CIK、LEI、ISIN、FIGI、CUSIP 及交易所代碼都只是外部識別碼。它們以版本化的 `IdentityAssertion` 連到發行人、證券或掛牌，並保存來源、來源政策版本、有效期間、證據、信任等級及建立者。只有無歧義的官方精確對照可自動生效；碰撞、期間重疊、關鍵欄位缺失或模糊名稱比對必須隔離待查。

每筆預測只對一個掛牌及其原生幣別價格負責。搜尋可以依發行人聚合，但不同股別、ADR 及跨市場掛牌不合併為同一價格序列。

生產導向 MVP 的預測標的包括台灣上市櫃普通股，以及美國主要交易所掛牌的普通股、不同普通股股別、REIT 與 ADR。ETF、ETN、特別股、權證、權利、債券、期貨、選擇權及美國 OTC 商品不產生個股預測；它們仍可在授權允許時作市場特徵。

## 時間模型

所有瞬間以 UTC 保存，另保留來源原始時間文字與其時區依據。來源紀錄版本使用下列時間欄位：

| 欄位 | 必填 | 語意 |
| --- | --- | --- |
| `first_observed_at` | 是 | 平台第一次成功保存含有該內容版本之原始證據的時間；決定production與平台觀測歷史可用性 |
| `retrieved_at` | 是（每張擷取收據） | 該次成功取得來源內容的時間 |
| `event_time`／有效期間 | 視紀錄種類 | 內容所描述的事件時間、交易時段或報表期間 |
| `published_at` | 否 | 來源明示的發布時間；不可由期間末或 HTTP header 猜測 |
| `source_time_text` | 有來源時間時 | 來源原始時間文字，不經改寫 |
| `time_precision` | 是 | 瞬間、日期、交易時段、月、季、年或區間 |
| `timezone_basis` | 是 | UTC、交易場所時區、來源指定時區或未知 |

查詢資訊截止點 `T` 的平台觀測時間點視圖時，只能選 `first_observed_at <= T` 且業務有效時間涵蓋目標事件／期間的版本，再排除於 `T` 以前已被更正或撤回者。`T` 以後抵達的資料、更正或撤回不得改變該歷史視圖；production 特徵與預測只接受此種視圖。

每個資料集在持久化時配置單調遞增的 `observation_sequence`。同一時間的版本依 `(first_observed_at, observation_sequence)` 排序，截止點使用包含端點的 `<=`。跨來源衝突依身分主張、來源優先級與證據規則解決，不能以抵達順序推定真相。

## 歷史可得性主張與證據等級

平台建置後的日常資料永遠依真實 `first_observed_at` 判定。對平台建置前取得的歷史回填，另建立不可變 `HistoricalAvailabilityClaim`；它至少綁定來源archive與資料集、涵蓋紀錄／分區、宣稱as-of區間、可定位版本、修訂／撤回語意、內容或manifest雜湊、證明機制、來源政策／契約、建立者、資格審查及有效期。發布時間、事件時間、回填時間或adapter自行提供的時間都不能單獨構成主張。

| `evidence_level` | 成立條件 | 允許用途 |
| --- | --- | --- |
| `platform_observed` | 有平台保存的原始物件、擷取收據與真實 `first_observed_at` | Production時間點視圖、正式歷史訓練／回測、追溯重播及研究 |
| `archive_attested` | 可信archive能證明指定時點可取得的精確版本、修訂／撤回語意與完整性，且來源政策允許 | 只限明示的正式歷史重建、訓練／回測與追溯重播 |
| `published_current_only` | 只有目前發布內容或發布日期，無法證明歷史版本及修訂語意 | 只限隔離研究，不得形成正式特徵、標籤或評估 |
| `unknown`／`self_asserted` | 證據缺失、未審查或由adapter／操作者自行宣稱 | `policy_blocked`或隔離，不得用於模型 |

正式歷史重建使用獨立、版本化的 `historical_reconstruction` 資料選擇模式，只能解析 `platform_observed` 或有效 `archive_attested` 主張，並在資料集、特徵、標籤與回測manifest保存主張ID及證據等級。此模式不得建立當時的production預測紀錄，也不得改寫 `first_observed_at`、`observation_sequence`、擷取收據或既有平台觀測時間點視圖。

主張的archive、版本／修訂語意、政策或完整性證明失效時，建立新的資格與影響評估；既有正式相依產出依譜系轉為不合格或政策隔離，而不是修改舊主張。`published_current_only` 升級為 `archive_attested` 必須建立新主張與新資料集版本。

## Append-only 證據鏈

資料分為四個不可變層次：

1. `RawArtifact` 保存來源回應或檔案的原始位元組、內容雜湊、媒體型別、去敏感化來源 URI、來源政策版本及擷取證據。
2. `SourceRecordVersion` 保存原始資料物件中的來源原生紀錄片段、來源紀錄鍵、身分規則版本與內容雜湊。
3. `NormalizedRecordVersion` 保存特定 decoder／正規化規則對來源紀錄版本的結構化解讀。
4. `RetrievalReceipt` 保存每次擷取嘗試、回應 metadata、時間、outcome 與 checkpoint 證據。

相同來源紀錄鍵及內容雜湊只增加擷取收據，不建立重複來源紀錄版本。內容改變才建立新版本。解析器升版只建立新的正規化紀錄版本，不改變來源紀錄版本或首次取得時間；新研究回測若採新版正規化結果，必須建立新的特徵快照，既有預測則仍綁定原版本。

版本以 `revision_kind` 區分 `initial`、`update`、`correction` 與 `withdrawal`，並以 `supersedes_record_version_id` 形成單向鏈。來源未說明變更原因時只能記為 `update` 並設 `reason_unknown=true`，不得自行推斷為更正。

每個原始資料物件與紀錄版本都綁定擷取當時的來源政策版本。未核准、到期或不允許目標用途的政策版本不得啟動擷取。

## 來源模組與 seam

來源套件提供兩個可獨立測試及替換的 adapter：

### `SourceCollector.collect(request)`

Collector 隱藏來源的認證、速率限制、分頁、游標與下載行為。共同請求只包含：

- `dataset_id`
- `source_policy_version_id`
- `run_id` 與 `trace_id`
- `mode`：`incremental` 或 `backfill`
- `requested_window`
- `committed_checkpoint`
- 可選 `scope`，例如股票池

Adapter manifest 宣告 adapter／資料綱要版本、支援的 mode、時間粒度及 scope 能力。Collector 串流輸出原始內容、去敏感化來源 URI、媒體型別、允許保存的來源回應 metadata、擷取時間與下一 checkpoint，並以涵蓋報告及結構化 outcome 結束。

### `SourceDecoder.decode(raw_artifact)`

Decoder 不連線到來源，只決定性地解析已保存的原始資料物件。每筆紀錄草稿至少輸出：

- `source_record_key` 與 `identity_rule_version`
- `record_kind` 與 `source_schema_version`
- `raw_artifact_id`、來源片段定位及片段內容雜湊
- `decoder_version`
- `event_time`／有效期間及 `published_at`
- `source_time_text`、`time_precision` 及 `timezone_basis`
- 外部識別碼主張
- `revision_kind`、來源前版識別碼、撤回原因及 `reason_unknown`
- `source_policy_version_id`
- 解析警告及資料品質旗標

平台而非 adapter 建立 `first_observed_at`、`retrieved_at`、`observation_sequence`、內部版本 ID、身分主張及前版連結。這讓來源 adapter 無法偽造時間點可用性或繞過身分治理。

歷史可得性主張也由平台的獨立qualification workflow依已保存archive證據與有效政策建立；Collector manifest、Decoder欄位或人工輸入不得直接指定 `evidence_level=archive_attested`。

## Checkpoint、outcome 與完整性

只有原始資料物件與擷取收據成功持久化後，平台才原子提交相應 checkpoint。解析失敗從已保存內容重播，不重新下載；分頁中途失敗不得越過最後完整保存頁。游標過期必須進入受控重新同步，不能靜默歸零。

每次擷取或解析以一個結構化 outcome 結束：

- `completed`
- `partial`
- `retryable_failure`
- `terminal_failure`
- `policy_blocked`
- `quarantined`

網路錯誤、429 與多數 5xx 可重試；未核准條款、無效 entitlement 或保存權到期屬 `policy_blocked`；schema 漂移、身分歧義與內容碰撞屬 `quarantined`。

HTTP 成功不是資料完整證明。每次執行必須產生 `CoverageReport`，記錄預期／實得分區、頁數、筆數、時間範圍、股票池涵蓋率、缺漏項目及來源完成標記。只有 outcome 為 `completed` 且涵蓋規則通過的執行，才能供下游建立正式特徵快照。

## 交易日曆與公司行動

交易日曆保存交易場所、當地 `session_date`、時區、session 開閉時間、正常／半日／臨時休市狀態及版本。官方交易所日曆與公告是第一權威，供應商資料只能交叉驗證。

預測紀錄綁定當時已知的 projected calendar version；趨勢標籤按事後實際發生的第 1、5 或 20 個 realized session 成熟，並綁定 realized calendar version。臨時休市會延後成熟日期，但不改寫原預測。

行情保存未調整原始價格。公司行動以版本化事件保存，內部決定性規則產生 `AdjustmentVersion`；特徵快照與趨勢標籤都綁定調整版本。供應商 adjusted close 只用於交叉驗證，不能作不可追溯的歷史真相。

## 必須通過的契約情境

- 同一 ticker 在不同有效期間屬於不同證券時，不會串接價格序列。
- 單純 ticker 更名保留掛牌身分，轉板及下市後重新上市建立新掛牌。
- 系統停機後補抓舊公告，不會讓公告進入停機前的時間點視圖。
- 單機輪廓可在每日排定停機後從 committed checkpoint 補抓停機期間公開的資料；`first_observed_at` 是重新上線後平台真正取得的時間，不能回填成來源發布時間或停機期間的推測觀測時間。
- 合格archive能以 `archive_attested` 重建平台建置前的正式訓練摺，但不能產生或冒充當時的production預測紀錄。
- 只有發布日期或目前最終值的回填維持 `published_current_only`，不能進入正式特徵、標籤或回測。
- 新增或撤銷歷史可得性主張不會改變來源版本的 `first_observed_at` 或既有平台觀測時間點視圖。
- 來源更正財報後，舊時間點仍可看到當時版本，新的資訊截止點看到更正版。
- 同一原始內容重抓只增加擷取收據；新版 decoder 重播只增加正規化紀錄版本。
- 分頁擷取在中段失敗後，checkpoint 不會越過未保存頁，重跑不產生重複來源版本。
- 未核准來源政策、身分歧義、schema 漂移及不完整涵蓋都無法進入正式特徵快照。
- 臨時休市會改變成熟標籤日期，但原預測的日曆、資料與模型譜系保持不變。

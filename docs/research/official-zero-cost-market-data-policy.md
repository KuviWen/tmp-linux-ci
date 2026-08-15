# 官方零成本行情資料政策

> **2026-08-15 scope note:** ADR 0018 已放寬全產品的免帳號／免 API key 限制；本文仍是 ticket 06 的台灣 OGDL source-specific 研究，所列匿名存取事實保持有效，但不再代表所有後續來源都必須匿名。

查證日期：2026-08-15

政策建議版本：`official-zero-cost-market-data-policy-v1`

## 結論

本專案可以在不申請帳號、不使用 API key、不另簽書面授權、不購買商業 entitlement 的前提下，使用**逐一在政府資料開放平臺標示為「政府資料開放授權條款第 1 版（OGDL 1.0）」的資料集**，建立從現在開始的 TWSE／TPEx 當期快照。OGDL 1.0 明示不限目的、時間及地域、免授權金，允許重製、散布、公開傳輸、編輯、改作與再授權，且無須另向資料提供機關取得書面或其他方式授權；但使用資料即表示同意條款，並必須履行顯名義務，因此本政策不把它描述成「完全無契約／無條件」。[政府資料開放授權條款第 1 版](https://data.gov.tw/license)

這條零成本路徑目前只能安全支援：

- TWSE／TPEx 每日當期 EOD 快照與從首次觀察日起的 platform-observed archive；
- 個別 OGDL 資料集明列的當期除權息、股利、基本資料、新上市等欄位；
- 個人／內部研究中的保存、轉換、建模、備份及內部顯示，但限於**該 OGDL 資料集實際釋出的內容**，並持續保留 attribution 與來源版本。

截至查證日，沒有找到任何官方零成本資料集承諾在上述免申請條件下，同時提供 ticket 06 所需的：

1. 足以建立最新八個季度 folds 的 7+ 年未調整個股日 OHLCV；
2. 同期完整、可版本化的公司行動；
3. 同期完整的 listing／name／security-code history；
4. corrections／withdrawals 的歷史版本或 archive attestation。

因此零成本來源不能讓系統聲稱已有七年完整歷史。依 2026-08-15 的產品決定（ADR 0017），`DEP-MKT-TW-01` 已被 `TWSE-OGDL-OPEN-DATA-01` 公開資料使用依據取代；ticket 06 criterion 4 改為驗收使用依據、實得深度及不足時 fail-closed 的可追溯性，而不是要求當下已具七年 backfill。現有 observed-history path／qualification gate／REST／UI 必須在深度不足時繼續 `policy_blocked`，且不得把 TWSE 歷史互動查詢頁、人工下載、未文件化端點或 TPEx 資料拿來暗補 XTAI 正式歷史。

## 授權邊界

### OGDL 能支持的用途

OGDL 1.0 的授權文字可支持本專案 `PRICE_RESEARCH_REQUIRED_USES` 的權利層需求，但只適用於資料集頁面明示採 OGDL 的那一份資料：

| 專案用途 | OGDL 依據 | 政策判斷 |
|---|---|---|
| `ingest` | 允許取得與重製 | 可允許 |
| `retain_7_years` | 利用不限時間，授權不可撤回 | 對**已合法取得的 OGDL 快照**可允許長期保存；不表示來源提供七年回填 |
| `transform` | 允許編輯、改作及製作衍生物 | 可允許 |
| `model` | 不限目的並允許開發產品／服務型態衍生物 | 可允許內部模型用途 |
| `internal_display` | 允許散布及公開傳輸 | 可允許；仍須顯名 |
| `backup_restore` | 允許重製 | 可允許 |

來源：[OGDL 1.0 授權範圍、無須另行書面授權及顯名義務](https://data.gov.tw/license)。

政府資料開放平臺的資料品質指引把「可直接下載」定義為無須登入或額外操作；Open API 指引則說，需要身分驗證、會員註冊、API key、額外申請或付費的介面不屬於其 Open API 範圍。因此，下列資料集的免帳號／免申請判斷只適用於資料集頁面列出的直接 distribution 與 OAS 介面，不延伸到同機關其他產品或頁面。[政府資料品質提升流程：直接下載](https://data.gov.tw/about/doc?chapter=17&doc=6)；[共通性應用程式介面指引：Open API 存取條件](https://data.gov.tw/about/doc?chapter=31&doc=9)

上述對應是本研究對明文授權的工程政策解讀，不是法律意見。資料提供機關可因公共利益或第三人權利疑慮停止後續提供；已取得資料仍應保存取得時的 dataset metadata、license version、terms hash 與 attribution。OGDL 不授與商標或專利權，且未履行顯名義務會被視為自始未取得授權。

### 不能把 OGDL 擴張到整個交易所網站

TWSE 一般網站條款禁止非依交易所同意方式，以自動程式、蜘蛛、爬蟲或擷取程式下載資料；智慧財產權條款只排除已授權政府資料開放平臺提供公眾使用的資料。[TWSE 使用條款](https://www.twse.com.tw/zh/terms/use.html)

TPEx 條款採相同邊界：未經同意不得用自動程式或爬蟲下載，只有已授權政府資料開放平臺提供的資料例外。[TPEx 網站使用條款](https://www.tpex.org.tw/zh-tw/gtsm_disclaimer.html?l=zh-tw)

所以政策單位是 `dataset_id + distribution URL + license version`，不是 `twse.com.tw` 或 `tpex.org.tw` 網域。某個互動頁面公開可讀、某個參數可回傳 CSV，或 OpenAPI 有相似欄位，都不足以證明它落在另一份 OGDL 資料集授權內。

## Taiwan verified capabilities

### TWSE：可直接啟用的當期資料

| 官方資料集 | 已核實能力 | 存取／費用 | 歷史與完整性限制 | 建議狀態 |
|---|---|---|---|---|
| [11549 上市個股日成交資訊](https://data.gov.tw/dataset/11549) | 日期、證券代號／名稱、未調整開高低收、成交股數／金額／筆數；每日更新 | 資料集標示免費、OGDL 1.0，並連到 TWSE CSV／OpenAPI；公開介面未文件化 API key、帳號或申請程序 | metadata 只承諾每日更新與這組欄位，未承諾歷史查詢參數、七年 archive、revision log 或 delisted coverage | `enabled_current_only` |
| [89748 上市股票除權除息預告表](https://data.gov.tw/dataset/89748) | 除權息日期、代號／名稱、配股率、現增認購與現金股利等預告欄位 | 免費、OGDL 1.0；TWSE CSV／OpenAPI | 不定期、面向預告；不是七年完整 company-action ledger，也未涵蓋所有合併、分割、減資、換股與事後修訂 | `enabled_current_only` |
| [31612 上市公司股利分派情形](https://data.gov.tw/dataset/31612) | 股利年度／期間、決議進度、現金與股票股利等申報欄位 | 免費、OGDL 1.0；每日更新；MOPS/TWSE CSV／OpenAPI | 動態 current extract；沒有官方七年 completeness、point-in-time revisions 或全部公司行動保證 | `enabled_current_only` |
| [18419 上市公司基本資料](https://data.gov.tw/dataset/18419) | 當期公司代號／名稱、上市日期、普通／特別股等基本欄位 | 免費、OGDL 1.0；每月更新 | current master，不是 time-bounded alias／name／code history；下市後持續存在與否沒有保證 | `enabled_current_only` |
| [11542 最近上市公司](https://data.gov.tw/dataset/11542) | 最近上市公司、申請／審議／上市買賣日期 | 免費、OGDL 1.0；不定期更新 | 「最近」清單不是全期間 listing lifecycle archive，也不證明同一 listing 的 code change | `selection_support_only` |

TWSE 的 OAS 2.0 文件公開列出 `STOCK_DAY_ALL`、`TWT48U_ALL`、公司基本資料、最近上市與終止上市等 GET 介面，並表示歡迎介接；查證時部分 JSON endpoint 可在不帶 credential 的請求下回應。[TWSE OpenAPI](https://openapi.twse.com.tw/) 這只能證明查證當下的技術可達性，不是 SLA，也不能替未在 data.gov.tw 明示授權的 endpoint 補上 OGDL。

### 歷史深度不是歷史使用權

TWSE 個股日成交互動頁稱資料自 2010-01-04 起提供，技術上超過七年。[TWSE 個股日成交資訊](https://www.twse.com.tw/zh/trading/historical/stock-day.html) 但該頁：

- 不是 dataset 11549 metadata 所承諾的歷史 API；
- 受 TWSE 一般網站自動下載限制；
- 沒有在頁面上明示其歷史回應逐筆適用 OGDL；
- 沒有提供 archive attestation、revision history 或 ticket 所需的完整 company-action／symbol-history 契約。

因此不能由「查得到 2010 年資料」推論「可自動批次下載、保存七年並用於正式模型」。人工逐月下載也不符合 ticket 06 已鎖定的 adapter、checkpoint、coverage 與不可變 provenance 契約。

### 官方歷史商品證明目前的合約路徑

TWSE Data E-Shop 的「每日收盤行情」提供自 2003-12-01 起的日檔，含 OHLC、成交量值、證券名稱與暫停交易註記；頁面列內部使用 NT$1,000／月、外部使用 NT$1,500／月，單筆最多訂購五年，超過需分筆訂購。[TWSE 每日收盤行情](https://eshop.twse.com.tw/zh/product/detail/cfec9a1470e448ec91bfde006db361e8)

這不是零成本來源。使用商店須會員註冊、訂購、付費並接受用途限制；訂購條款規定內部版不得對外公開或移作他用，且未經事前書面同意不得另行取樣編製指數或其他衍生性商品。[商店使用條款](https://eshop.twse.com.tw/zh/home/terms)；[網路線上訂購條款](https://eshop.twse.com.tw/zh/shopping/finishOrder?show=true)

商品頁足以證明交易所有可回填的付費歷史商品，但它明確落在本專案零成本邊界之外，也不足以自行證明模型訓練、衍生特徵、長期保存、完整公司行動與 symbol history。後續 tickets 不得把它轉成採購、契約或 entitlement gate。

### TPEx：相同授權形狀，但不是 XTAI 替代來源

- [11370 上櫃股票行情](https://data.gov.tw/dataset/11370) 是每日收盤後的 TPEx OHLC、成交量值／筆數、發行股數與次日參考價，標示免費與 OGDL 1.0。
- [11633 上櫃股票除權除息計算結果](https://data.gov.tw/dataset/11633) 每日提供次一營業日除權息資訊，標示免費與 OGDL 1.0。
- [25036 上櫃公司基本資料](https://data.gov.tw/dataset/25036) 每日提供當期公司代號／名稱、上櫃日期、普通／特別股等基本欄位，標示免費與 OGDL 1.0；它仍是 current master，不是完整 alias history。
- [48665 上櫃歷史公布暫停／恢復交易股票](https://data.gov.tw/dataset/48665) 提供證券代號／名稱與暫停、恢復日期時間，標示免費與 OGDL 1.0；metadata 未承諾自何時起完整涵蓋所有事件。
- [TPEx OpenAPI](https://www.tpex.org.tw/openapi/) 公開列出當期行情、除權息、暫停／恢復及部分歷史統計端點；沒有在查得文件中看到 API key 要求。
- TPEx 個股歷史頁稱自 1994 年 1 月提供，技術深度遠超七年。[TPEx 個股日成交資訊](https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html)

但 TPEx 歷史互動頁同樣受一般網站自動擷取限制，且沒有找到明示其完整歷史回應落在某個 OGDL dataset distribution 的證據。更重要的是，TPEx 是另一 venue；它不能補 XTAI 十檔的價格、掛牌生命週期或公司行動。

## Coverage matrix

| ticket 06 必要能力 | 零成本官方資料能否支援 | 證據狀態 |
|---|---|---|
| 當日 XTAI 未調整 EOD | 能；dataset 11549 | `verified_current_only` |
| 從今天起累積七年 | 法律上可保存已取得的 OGDL snapshots；需每日不可變歸檔七年 | `prospective_only` |
| 立即取得 7+ 年 XTAI EOD | 公開歷史頁技術上有資料，但未找到免申請自動取用與 OGDL 歷史 distribution 的明示授權 | `not_verified` |
| 完整七年 company actions | 當期除權息／股利有 OGDL；未找到包含全部事件與修訂的七年官方零成本 archive | `not_verified` |
| 完整 listing／name／code history | current master、最近上市與個別公告可支持部分 assertion；沒有完整 time-bounded history | `not_verified` |
| 下市股 `2448` 的訓練歷史價格 | current snapshot 不包含已下市股；歷史頁不可被默認為合格 adapter | `not_verified` |
| correction／withdrawal／PIT archive | 可從首次 platform observation 起自行版本化；不能重建觀察前 revisions | `prospective_only` |
| 固定 rate limit／SLA | TWSE、TPEx 的 dataset metadata、OpenAPI 首頁與一般條款未公布固定 quota | `unknown` |

## Rate、revision 與重現政策

「未公布 rate limit」不等於不限流量。零成本 adapter 應固定：

- 每個 host 單一低併發 collector，EOD 一日一次為預設；
- 遇 `429`、`403`、`Retry-After`、連續 timeout 或結構變更立即 deferred／circuit-open，不輪替 IP、不繞過防護；
- 保存 response headers、取得時間、source URL、dataset ID、license／terms hash、raw content hash 與 retrieval receipt；
- 同日 payload 改變時新增 source-record version，不覆寫原始內容；
- 動態 endpoint 只作查證日觀察，不把 URL 當不可變證據；acceptance bundle 必須引用本地 content-addressed raw object；
- 每季重驗 data.gov.tw dataset metadata、TWSE／TPEx terms 與 OAS schema；資料下架或授權變更時停止後續收集並產生 policy evidence。

## Spec-safe recommendation

### 現在可以安全聲稱與實作

1. 新增一個 `twse-ogdl-current` source policy，只 allowlist dataset `11549`、`89748`、`31612`、`18419`、`11542` 的明示 distributions；每個 dataset 分立 `open_data_terms` evidence，不用 principal entitlement 或網域級 blanket policy。
2. 將 current adapter 設為可收集與長期保存，但其 historical evidence status 固定為 `platform_observed_since_<first_receipt_date>`，不能標成 archive-attested。
3. 對這些特定 OGDL datasets 可聲稱免授權金、無須另行書面授權、官方頁面未要求 API key／帳號；不得稱完全無契約、無條件、無 attribution 或有 SLA。
4. `coverage` 必須以實際 receipts 計算；沒有取得的交易日、下市股、company action 或 code history 都保持缺失，不能從 provider adjusted close 或其他網站補值。
5. 這條路徑可供個人／內部探索、prospective archive 及共同 Collector／Decoder contract 驗證；只有實得歷史、公司行動、身分生命週期與 revision evidence 達到版本化 fold／model gate 時，才可產出 formal training dataset 或 production prediction，沒有外部 gate 或採購 fallback。

### `TWSE-OGDL-OPEN-DATA-01` 與 criterion 4 的 fail-closed 契約

修訂後 spec 明定 Taiwan 是「allowed OGDL current distributions + platform-observed archive」。公開條款足以建立無 principal entitlement 的來源政策，但 current snapshots 仍未達正式歷史深度與完整性，因此：

- `source_basis_id = TWSE-OGDL-OPEN-DATA-01`，並公開 license／terms／attribution 與 no-account／no-application／no-fee flags；
- historical source 是 `twse-open-data-observed-history`，只反映實際 receipts；
- listing aggregate 在 formal evidence 不足時保持 `policy_blocked`；
- REST／UI 顯示 `source_basis_unverified` 或 `qualification_evidence_unverified`，並逐項呈現 policy／coverage／schema／integrity／depth；
- criterion 4 可在上述 traceability 與 fail-closed 行為通過後勾選，但 `formally_qualified` 仍必須為 false，直到實際歷史足夠。

不能只因 OGDL 權利廣泛，就把 current data 的永久保存權誤當成來源已交付多年歷史。未來只有官方資料集頁面明示同一公開條款、distribution 直接交付完整歷史且 schema／coverage／revision evidence 可保存時，該內容才可經既有 governance workflow 進入 archive-attested qualification；不申請、不付費的既定路徑則從現在起每日保存 OGDL snapshots，並接受無法補 `2448` 下市前訓練價格的支援限制。

## United States／future-ticket implications

Phase 2 的 United States path 同樣受零付費、用途合格及 fail-closed 邊界控制。下列匿名官方免費來源只能補局部 reference evidence：

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) 免 authentication／API key，提供 filer submissions、former names、exchange／ticker metadata 與 XBRL facts；它是 filings／facts source，不提供未調整交易所 OHLCV，也不是完整 corporate-action price-adjustment feed。
- [Nasdaq Trader Symbol Directory](https://www.nasdaqtrader.com/Trader.aspx?id=symbollookup) 的 current symbol files 會在日內更新；頁面另明示 Nasdaq Events Data（Security Status、Ex-Date、When Issued／When Distributed、Nasdaq Listed）可按現況使用而無額外 licensing requirement。但頁面同時說 Symbol Lookup 是 current trading day 狀態，這些事件子集不是七年 EOD archive。
- Nasdaq 完整 Daily List 涵蓋上市、下市、名稱／symbol 變更及股利，歷史到 1999 年，但它是 secured monthly subscription，而非免費公共 feed。[Nasdaq Daily List](https://nasdaqtrader.com/Trader.aspx?id=DailyListPD) Nasdaq 的官方費率公告亦將 Daily List／Fundamental Data 列為每組織月費產品。[Nasdaq 2024 fee notice](https://www.nasdaqtrader.com/TraderNews.aspx?id=DN2024-2)
- NYSE 官方歷史產品頁將 TAQ Closing Prices 定義為各交易日 OHLC／volume 檔；Daily TAQ 規格明示客戶須執行適用 agreement，訂購核准後才發 credentials。[NYSE Historical Data](https://www.nyse.com/market-data/historical)；[Daily TAQ client specification](https://www.nyse.com/publicdocs/nyse/data/Daily_TAQ_Client_Spec_v3.0a.pdf)

所以 SEC 或 Nasdaq current events 可以成為 future ticket 的獨立、範圍受限 source policies，但不能被誤稱為完整 EOD、company-actions 與 symbol-history path。依 ADR 0018，美國 formal price path 可另用用途合格的零付費 authenticated provider／source bundle；缺少來源資格時明示 `policy_blocked`，缺少憑證時明示 `credential_required`，兩者都不是付費、採購或 sales gate。

## 未知事項

- TWSE／TPEx OpenAPI 的固定每秒、每日與併發配額；官方文件未查得明文數字。
- data.gov.tw current distributions 對歷史修訂、撤回與補發的服務保證。
- TWSE Data E-Shop 實際訂單是否允許模型訓練、衍生特徵、七年以上保存及離約後留存；公開商品頁沒有完整回答。
- 是否會新增明示 OGDL、可自動批次取得且具 7+ 年完整 history 的 TWSE dataset。
- Nasdaq 免費 Events Data 的歷史保存深度與 completeness；官方 current page 的「無額外 licensing requirement」不能擴張到付費 Daily List 或交易行情。

這些未知事項一律不以樂觀推定通過 policy gate。

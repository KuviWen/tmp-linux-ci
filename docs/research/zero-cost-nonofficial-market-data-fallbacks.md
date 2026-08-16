# 零成本非官方市場資料來源與參考層評估

日期：2026-08-16

## 產品決策（取代本研究的隔離路徑建議）

2026-08-16 決定把 FinMind 免費 API 納入台灣行情的**正式資格候選**，沿用 Ticket 07 的 credential-managed provider seam。這表示 FinMind 可以接受正式 source-use、歷史、schema、calendar、action、lifecycle 與 live-contract 審查；不表示目前已正式合格。下列研究所記錄的權利與完整性缺口仍然有效，缺一即在單一正式路徑 fail closed。

Yahoo／`yfinance` 維持排除。產品不採用研究階段曾提出的 `experimental_reference_only` 或 `experimental_forecast` 兩條隔離路徑，也不建立自動 fallback；所有候選只有「通過完整正式 gates」或「在同一正式路徑明示 blocked」兩種結果。本文後段保留隔離路徑分析只作 rejected-alternative 紀錄，不是規格或後續實作要求。

## 結論

本次評估沒有找到一個零成本、可自行註冊、且能單獨滿足台灣與美國正式價格資格契約的非官方來源。正式資料管線應繼續 fail closed；不能把「僅供參考」當成授權豁免，也不能因正式來源缺席而靜默切換到未合格資料。

最值得繼續驗證的候選如下：

- 台灣：FinMind 是技術覆蓋最強的非官方候選，日價歷史足夠長，也提供下市等資料集；產品已把它列為正式資格候選，但尚缺逐資料集的上游權利、修訂、更正、名稱／代碼生命週期及內部團體使用證據，因此仍不能宣告正式合格。
- 美國：Twelve Data Business Basic 是本次新發現中最接近零成本內部團體使用的 EOD 候選；其條款明載 Internal Use、儲存及不可逆 Derived Data 的部分權利，但免費層不含完整 corporate actions 與交易所日曆，且仍須保存當時方案、註冊流程、備份和模型用途的具體證據。
- 美國：Alpaca Basic 的技術覆蓋最接近既有工程契約，具有 2016 年起歷史、raw adjustment、公司行動和 symbol mapping 能力；然而免費帳戶以 individual／nonprofessional 為主，其對長期備份、內部團體展示、模型訓練及模型產物存續的權利仍不夠明確。
- 美國：Tiingo 的歷史和公司行動很完整，但免費 Starter 禁止將原始資料寫入任何持久化儲存，且代表組織使用 API 必須採 Commercial plan。不可逆、非替代性的 Derived Products 雖可能免另行書面核准，但無法補足正式管線必需的 raw retention、backup 與重現性，因此不符合本專案「內部團體、零成本、無人工申請」邊界。
- Yahoo Finance／`yfinance`：不應納入自動化備用項。`yfinance` 的 Apache 程式碼授權不授予 Yahoo 資料權利，而 Yahoo 條款禁止未經事先明示許可的自動擷取。即使加上「僅供參考」標籤，也不能消除這個限制。

原研究曾建議新增隔離的 `experimental_reference_only` 狀態；產品決策已明確否決，規格與實作不得採用。

## 評估邊界

本研究接受：

- $0 方案；
- 自助註冊、click-through 條款、API token 或 secret；
- 有速率或歷史範圍限制，但足以重現七年以上 EOD 的方案。

本研究排除：

- 要求付款方式或付費試用；
- 業務洽談、人工核准、採購、議約或付費授權；
- 只因 wrapper／SDK 是開源，就推定其上游資料可以留存、備份、訓練或內部分享；
- 以「個人非商業」權利推定「內部團體」也獲授權；
- 以技術可下載推定資料可成為正式訓練或預測輸入。

正式資格仍須逐一證明：自動存取、原始資料留存、不可變重現、備份、轉換、模型訓練、Derived Data／模型產物存續，以及本專案實際內部使用者的顯示權。資料欄位也須涵蓋至少七年的 per-listing 未調整日 OHLCV、公司行動、名稱／代碼生命週期、交易日曆、停牌／下市及修訂／更正語義。沒有證據即視為未通過，不以推測補足。

## 候選比較

| 來源 | 地區／免費歷史 | 免費存取 | 權利與產品限制 | 本次判定 |
| --- | --- | --- | --- | --- |
| FinMind | 台灣日價自 1994-10-01；另有下市等資料集 | 匿名 300 次／小時；註冊 token 600 次／小時 | 教育／參考定位；禁止對外 raw redistribution；缺逐資料集上游與修訂契約 | 台灣正式資格候選；未正式合格 |
| Fugle | 台灣歷史 K 線僅一年 | 免費 Basic、token、60 次／分 | 行情僅供參考；成交量／值排除盤後零股與鉅額；交換所規則及轉傳限制 | 只適合近期交叉檢查 |
| Alpaca Basic | 美國自 2016 年起 | $0、200 次／分 | 個人／開發者與 nonprofessional 邊界；團體、備份、模型權利不足 | 強工程候選；資格 fail closed |
| Twelve Data Business Basic | 美國 EOD 通常自首次交易；單次最多 5,000 點，可用日期分頁 | $0、8 credits／分、800／日 | Internal Use 與部分 Derived Data 條款較明確；免費層缺完整 actions／calendar；台灣 XTAI 是付費層 | 最值得驗證的美國內部 EOD 候選 |
| Tiingo Free | 美國 60+ 年，raw／adjusted、dividend、split | $0、50 次／時、1,000／日 | Starter 禁止持久化 raw data；代表組織使用須 Commercial plan；僅不可逆且非替代性的 Derived Products 可能保留 | 正式管線及團體使用不合格 |
| Alpha Vantage Free | 免費僅最近 100 點；完整歷史為 premium | 25 次／日 | 免費權利限個人非商業；代表組織使用另議 | 歷史與權利皆不合格 |
| Marketstack Free | 一年 EOD | 100 次／月 | 免費層無足夠歷史及商業／團體權利 | 不合格 |
| Financial Modeling Prep Free | 最多五年 | 250 次／日 | 個人使用；分享、顯示或衍生用途限制嚴格 | 不合格 |
| EODHD Free | 一年 | 20 次／日 | 個人用途且歷史不足 | 不合格 |
| Massive Stocks Basic | 兩年 | $0 | individual、display-only 邊界；非顯示／模型用途需額外授權 | 不合格 |
| SimFin Free | 價格約五年 | $0 | 歷史不足；訂閱終止後資料與備份刪除義務 | 不合格 |
| Stooq | 可手動下載部分 CSV | 無需 token | 找不到足以支持自動化、留存、備份和模型用途的明確第一方授權 | 只能人工 sanity check |
| Yahoo／`yfinance` | 技術上可取得多年資料 | unofficial wrapper | Yahoo 禁止未經許可的 automated collection；wrapper license 不等於 data license | 禁止自動化備援 |

## 台灣來源

### FinMind

FinMind 文件將資料定位為由公共或公開來源彙整；其 disclaimer 對政府資料提到政府資料開放授權條款（OGDL），但不能因此推定每一個 hosted dataset 都有相同權利。正式資格必須把 `TaiwanStockPrice`、公司行動、證券清單、下市及日曆各自對應到明確上游、散布權和修訂語義。[FinMind overview](https://finmind.github.io/en/)；[Disclaimer and data licensing](https://finmind.github.io/Disclaimer/)

`TaiwanStockPrice` 文件列出 1994-10-01 至今的上市、上櫃與興櫃日價欄位，可滿足七年歷史的技術前提；quickstart 說明匿名每小時 300 次、以 email 註冊 token 後每小時 600 次。官方 OpenAPI 將 data endpoint 定義為 bearer authentication，並以 HTTP 402 表示配額耗盡；實作因此使用 authorization header，且將 402／429 都投影成可重試 quota evidence。[TaiwanStockPrice](https://finmind.github.io/tutor/TaiwanMarket/Technical/)；[Quickstart](https://finmind.github.io/en/quickstart/)；[official OpenAPI](https://github.com/FinMind/FinMind.github.io/blob/master/openapi.yaml)

FinMind 也提供 `TaiwanStockDelisting` 等生命週期相關資料，但現有文件不足以證明名稱歷史、跨 listing-code transition、停牌、公司行動與價格修訂可形成完整 point-in-time 契約。[Taiwan fundamental datasets](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)

服務條款／隱私頁面的使用定位是教育與參考，並限制 raw data 的公開再散布、鏡像與未授權展示。FinMind repository 的 Apache-2.0 只授權程式碼；它不能替代 hosted data 與各上游資料的使用權。[Privacy policy and terms](https://finmind.github.io/en/PrivacyPolicy/)；[FinMind source repository](https://github.com/FinMind/FinMind)

因此，FinMind 可列入正式資格候選，但只有在保存註冊當時條款並確認本專案的內部團體、留存、備份、模型與展示用途後才能正式啟用。它目前不能：

- 補齊正式 canonical partition；
- 單獨證明七年正式 coverage；
- 產生正式 AdjustmentVersion、訓練集或發布預測；
- 把未證明的 ticker／名稱關係當成永久身分；
- 將資料的可存取性當成授權或修訂可重現性的證據。

### Fugle

Fugle Basic 可免費註冊並取得 token，但 historical candles 只提供最近一年，無法建立七年歷史。[Pricing](https://developer.fugle.tw/docs/pricing/)；[Historical candles getting started](https://developer.fugle.tw/docs/data/http-api/getting-started/)

其資料介紹明示行情僅供參考，且成交量／成交值不含盤後零股與鉅額交易；同時要求使用者遵循交易所資料規範並限制未授權轉讓、轉售、再授權或傳送。因此它最多是近期觀測的人工或隔離交叉檢查，不是歷史價格來源。[Market-data introduction and usage statement](https://developer.fugle.tw/docs/data/intro/)

## 美國來源

### Twelve Data Business Basic

Twelve Data 的 Business Basic 標示 $0，提供 8 credits／分、800／日及 US equities reference／EOD 能力。這是本次最接近「內部團體」語義的免費方案，而不是把 individual account 擴張解讀成團體授權。[Business pricing](https://twelvedata.com/pricing-business)；[Basic subscription](https://twelvedata.com/subscribe/plan/basic-8)

其條款明示在方案容許範圍內可為 Internal Use 存取、接收、處理及儲存資料，也容許建立無法反向還原來源資料的 Derived Data；但保留期與終止後刪除義務仍受訂閱和方案約束。正式採用前仍須確認：

- Business Basic 確為持續 $0 而非限時 trial，註冊不要求付款方式；
- 原始 EOD partitions 與備份可在訂閱期間依本專案保留策略保存；
- 模型訓練、模型參數、評估產物和訂閱終止後存續是否屬允許的 Derived Data；
- 團隊 UI 的 display／non-display 行為符合方案；
- 當時版本條款及方案證據可以不可變留存。

[Twelve Data terms](https://twelvedata.com/terms)

美國 EOD 支援文件稱資料在美東午夜後提供、涵蓋所有上市 equities，並聚合全市場成交量；日資料通常從首個交易日開始。API 的 `adjust=none` 可請求未調整價格，而不是依賴預設 split-adjusted 行為。[US equities market data](https://support.twelvedata.com/en/articles/9935903-us-equities-market-data)；[Historical prices](https://support.twelvedata.com/en/articles/5656039-how-to-get-historical-prices)；[API documentation](https://twelvedata.com/docs)

不過免費 Basic 不含完整 dividends／splits endpoints，也不含 historical exchange schedule；這兩類能力位於較高付費層。XTAI 亦需 Pro／Venture 以上，不能解決台灣零成本需求。[XTAI coverage](https://twelvedata.com/exchanges/xtai?group=regulatory)

所以 Twelve Data 最多可成為美國 raw EOD source bundle 的一員，而非單一完整合格來源。公司行動、生命週期、日曆、停牌／下市及修訂必須由同樣通過權利與重現性審查的其他來源補齊。

### Alpaca Basic

Alpaca Basic 標示 $0、200 API calls／min、美國股票 7+ 年歷史、aggregates 與 corporate actions；官方文件說免費歷史自 2016 年起，超過 15 分鐘的 SIP historical data 可在免費層取得。[Market-data plans](https://alpaca.markets/data)；[About Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api)；[Market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)

API 支援 `raw` adjustment，也提供 `asof` 以處理部分 symbol mapping，因此技術面強於多數免費候選。[Historical API](https://docs.alpaca.markets/us/docs/historical-api)；[Historical-data example](https://alpaca.markets/learn/fetch-historical-data)

但免費層文件主要面向 individual traders／developers，客戶協議亦以 nonprofessional 使用者為界，對複製、散布、銷售或商業利用設有限制。這些文字沒有充分回答本專案的內部團體展示、長期 raw retention／backup、模型訓練及模型產物存續。故工程 adapter 可存在，但正式 qualification 必須保持 false，直到實際帳戶主體和用途權利有可保存的明確證據。[Alpaca customer agreement](https://files.alpaca.markets/disclosures/library/AcctAppMarginAndCustAgmt.pdf)

### Tiingo Free

Tiingo 免費方案技術上很吸引：60+ 年歷史、raw／adjusted OHLCV、dividends、splits，限額 50 次／小時及 1,000 次／日。[End-of-day product](https://www.tiingo.com/products/end-of-day-stock-price-data)

但現行條款規定 Starter／trial plan 只能在 volatile memory 或 temporary non-persistent cache 處理資料，工作結束前必須刪除，明確涵蓋 database、object store、log、backup 與 DR；代表 organization／business 使用 API 也必須採 Commercial plan。條款確實容許無法反向還原、也不能替代原始資料的 Derived Products（例如符合條件的 forecasts 或 model parameters）在不另取書面核准下保留，但這不授予持久保存 raw OHLCV、建立不可變歷史或讓內部團體使用免費帳戶的權利。[Pricing](https://www.tiingo.com/pricing)；[Terms of service](https://api.tiingo.com/tos/)

這直接違反本專案的零成本、自助、內部團體及無人工核准邊界。Tiingo 不應因技術品質高而被降格包裝成團體用「參考來源」。

### 其他美國候選

- Alpha Vantage 的 daily API 可有 20+ 年資料，但 `outputsize=full` 和 adjusted 能力屬 premium；免費層只有最近 100 點、每日 25 次，條款也以個人非商業為主。[Documentation](https://www.alphavantage.co/documentation/)；[Premium](https://www.alphavantage.co/premium/)；[Terms](https://www.alphavantage.co/terms_of_service/)
- Marketstack Free 只有一年 EOD、100 次／月，歷史不足。[Pricing](https://marketstack.com/pricing)
- Financial Modeling Prep 免費層最多五年、250 calls／day，且條款不支持團體分享、展示或衍生用途。[Pricing](https://site.financialmodelingprep.com/pricing-plans)；[Terms](https://site.financialmodelingprep.com/developer/docs/terms-of-service)
- EODHD Free 只有一年與 20 calls／day，歷史不足。[Historical-data API](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes)
- Massive Stocks Basic 只有兩年，且免費權利以 individual／display use 為中心。[Stocks pricing](https://massive.com/pricing?product=stocks)；[Market-data terms](https://massive.com/legal/market-data-terms-of-service)
- SimFin 免費價格歷史約五年，且訂閱終止後有刪除下載資料與備份的義務。[Pricing](https://www.simfin.com/en/prices/)；[Data download](https://www.simfin.com/en/fundamental-data-download/)
- Stooq 可供人工下載與 sanity check，但未找到足以支持自動化、保留、備份、內部團體展示和模型用途的明確第一方授權，因此不應建立 adapter。[Stooq](https://stooq.com/q/?a=lg&b=0&c=5d&s=nvda.us&t=l)

## Yahoo Finance／`yfinance` 專項判定

`yfinance` README 清楚說明它不隸屬、不受 Yahoo 認可或審核，並以研究／教育及 Yahoo API 的 personal-use 提示要求使用者自行查核 Yahoo 條款。repository 的 Apache license 只涵蓋 wrapper 程式碼，並沒有把 Yahoo 資料授權給使用者。[yfinance README](https://github.com/ranaroussi/yfinance/blob/main/README.md?plain=1)

Yahoo Terms of Service 禁止未經事先明示許可，使用 robots、spiders、scrapers、data mining tools 或其他 automated means 存取或收集服務資料，也限制建立替代性資料庫或 archives。Yahoo API Terms 也不能反向把 `yfinance` 的 unofficial endpoints 變成已授權 API。[Yahoo Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html)；[Yahoo API Terms](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apitnc/index.html)

因此結論是：

- 不新增自動化 `yfinance` provider；
- 不以「reference-only」、低頻率、內部使用或不收費為例外理由；
- 不下載、快取、留存、備份、訓練或展示自動擷取結果；
- 若產品需要，可保留導向 Yahoo 公開頁面的人工外部連結，但該頁面不得回寫任何正式或參考資料狀態。

## 已否決的隔離參考層方案（非需求）

以下是研究階段為比較風險而寫下的原始方案。產品已決定不新增 `experimental_reference_only`（或 `unqualified_reference`），也不在正式來源失敗時自動 fallback；以下內容不得轉成 acceptance criteria 或實作。

允許的行為應限制為：

- 在與正式 canonical storage 分離的命名空間保存短期、可刪除的 observation；
- 比對同日 OHLCV 或 actions 並產生 discrepancy report；
- 在 UI 顯示永久水印「非合格參考來源」，同時顯示 provider、terms snapshot、抓取時間、coverage 與 TTL；
- 支援人工作業調查，但不自動覆寫 identity、calendar、actions 或正式價格真相；
- 若來源權利不允許留存或團體展示，則完全不建立 observation。

它必須永遠不能：

- 滿足 source／dataset eligibility 或七年 coverage criterion；
- 補洞、覆寫或建立 canonical immutable partitions；
- 建立正式 dataset identity 或 AdjustmentVersion；
- 成為 features、labels、training、calibration、backtest、promoted model、shadow／production prediction 的輸入；
- 解除 `unavailable`、`policy_blocked` 或 publication gate；
- 解決證券永久身分、名稱／代碼 transition、公司行動、日曆、停牌或下市真相；
- 在條款不允許時進入 backup、log、model artifact 或內部團體 UI；
- 隱藏 provider 身分，或形成靜默優先順序鏈。

最重要的分界是：若未來希望以某個非官方來源訓練或顯示預測，必須另建明確的實驗 source-use、資料品質與輸出契約；不能只把 `experimental_reference_only` 放寬成訓練來源。只有通過完整正式資格契約的來源，才可進入正式訓練或發布路徑。

### 已否決的 `experimental_forecast` 方案

如果產品目標不只是差異比對，而是在沒有正式來源時仍提供明確降級的預測，需求應另設 `experimental_forecast`，不要把它混入 `experimental_reference_only`。這條路徑仍須有自己的 source-use gate：當時方案與條款必須明確允許自動存取、必要期間的 raw retention／backup、feature／label 轉換、模型訓練，以及不可逆 prediction／model artifact 的建立、保存與指定內部使用者展示。沒有上述證據時，即使資料可下載或標示「僅供參考」，也不得進入實驗模型。

`experimental_forecast` 的 dataset、feature、model、assignment、artifact lineage 與 UI 必須和正式命名空間分離；輸出永久標示「實驗／非正式預測」，不得滿足正式 eligibility、模型晉升、publication gate、SLO 或階段 exit，也不得在日後把既有實驗 artifact 重新標籤成正式 artifact。正式狀態同時仍須維持 `unavailable` 或 `policy_blocked`。

依目前證據，FinMind 與 Twelve Data Business Basic 只值得繼續做這條路徑的權利驗證，尚未通過；Alpaca 的內部團體與模型用途仍不明；Yahoo／`yfinance` 因 automated collection 本身未獲允許，連實驗路徑也不應納入。若僅是單一個人、非團體的本機實驗，Tiingo Starter 的條款可能容許在不持久化 raw data 的前提下即時計算不可逆 forecast／model parameters，但這不符合本專案既有的不可變重現與內部團體邊界，必須視為另一個更窄的產品 profile，而非本專案的預設備援。

## 已否決的需求文字（不得納入規格）

> 系統 MAY 提供與正式資料管線隔離的 `experimental_reference_only` observation tier。只有當來源的當時方案與條款明確允許該實際操作的自動存取、留存、內部使用及展示時，才可啟用；「僅供參考」標籤不構成授權。
>
> Reference observations MUST NOT satisfy source or dataset eligibility, MUST NOT fill or overwrite canonical partitions, and MUST NOT feed feature generation, labels, training, calibration, backtesting, model promotion, shadow/production prediction, or publication. Formal output MUST remain fail closed and continue to report `unavailable` or `policy_blocked` when no qualified source exists.
>
> Reference observations MUST use separate lineage and storage, disclose provider/terms snapshot/retrieval time/coverage/TTL, carry a permanent non-qualified watermark, and be removable without changing formal dataset identities or results. Yahoo Finance data obtained through unofficial automated clients, including `yfinance`, MUST NOT be enabled because the wrapper's software license does not grant the underlying data rights.

## 後續證據與決策

在不引入付費、人工核准或議約的前提下，建議依序做以下資格調查；它們是研究／法務證據工作，不是本次程式實作：

1. 對 Twelve Data Business Basic 建立 dated plan／terms snapshot，實際走完不含付款方式的註冊，向條款文字逐項對應 raw retention、backup、internal-group display、model training、Derived Data 與終止後模型產物存續；若任何一項只能靠客服或書面核准，立即停用正式資格。
2. 對 FinMind 每一個所需 dataset 建立 upstream lineage matrix，逐一記錄來源單位、原始授權、歷史起點、revision／correction 行為、名稱／代碼 transition、下市與停牌覆蓋；不可用平台層的 OGDL 概述代替逐資料集證據。
3. 保留 Alpaca 作為現有美國 provider contract 的工程候選，但在帳戶主體、內部團體、備份和模型用途未取得自助且可保存的明確權利前，正式 eligibility 維持 false。
4. 不建立獨立參考層；FinMind 只透過正式候選 provider contract 接受驗證，任何缺口都不得改善正式 coverage 或 prediction 狀態。

最終產品決定：正式 qualified pipeline 不變且不新增實驗隔離管線。Yahoo／`yfinance` 明確排除；FinMind 是台灣正式資格候選但資格維持 fail closed，直到逐資料集權利與完整歷史證據真正通過；Twelve Data Business Basic 仍只是美國研究候選，Alpaca 維持強工程候選但資格 fail closed。

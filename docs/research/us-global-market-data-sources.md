# 美國與全球市場資料來源盤點

查核日期：2026-08-13

## 結論

生產導向 MVP 可以用官方免費來源完成 SEC 申報與大部分美國／全球總體特徵，但**不能只靠公開免費端點，便宣稱已取得美股調整後行情、完整公司行動、新聞原文及法人個股預測的七年保存與團隊使用權**。行情、公司行動與新聞應做成部署者自備授權的 adapter；沒有相符書面授權時停用，而不是退回未查明權利的網頁或非官方套件。

建議的初始政策如下：

- 預設可啟用：SEC EDGAR 的申報索引與 XBRL 事實、BLS、BEA，以及逐資料集確認授權後的 World Bank、OECD、BIS 統計。
- 白名單後啟用：FRED／ALFRED、IMF 與發行人 IR。它們各有第三方資料、終止後刪除、或未查得明確再利用權等問題，不能以來源網域整體放行。
- 簽約後啟用：NYSE、Nasdaq／Nasdaq Data Link、Alpha Vantage 等行情、公司行動、新聞與法人預測服務。訂單必須明載非顯示分析、衍生特徵、內部多人使用、七年原始資料保存、備份／災難復原、模型訓練與預測結果呈現權。
- 新聞的安全預設只保存不可變的索引證據（來源、URL、標題、作者、發布／首次取得時間、雜湊、語言、標的映射）；全文只有在逐來源授權明示允許時保存。SEC 申報與公司新聞不是一般新聞媒體全文授權的替代品。

本文件是工程授權盤點，不是法律意見；表中的「可用」是依查核日公開一手文件所做的保守產品判斷。

## 來源矩陣

### 美國公司、行情、公司行動與新聞

| 來源擁有者／來源 | 資料類型與涵蓋範圍 | 歷史深度 | 更新時間 | 介面 | 認證／費用 | 速率限制 | 原始內容保存／再散布限制 | MVP 適用判斷 | 待法務／採購確認 |
|---|---|---|---|---|---|---|---|---|---|
| U.S. SEC — [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[EDGAR archives](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) | 上市公司及其他申報人的 submissions history；10-Q、10-K、8-K、20-F、40-F、6-K 等表單的標準 XBRL facts；原始 filing、附件及 daily index。API 只聚合標準 taxonomy 且適用整個申報實體的 facts，不能取代原始申報解析。 | EDGAR 自 1994/1995 起；XBRL 自 2009 年開始要求。Submissions JSON 至少含最近一年或 1,000 件並可循附加檔追溯更早歷史。 | JSON 在申報散布時日內即時更新；官方稱 submissions 通常延遲少於 1 秒、XBRL 少於 1 分鐘；bulk ZIP 約每日美東 03:00 重建。 | 無 key 的 HTTPS JSON；bulk ZIP；Archives 原始 SGML／HTML／XBRL；RSS。 | 公開存取、無 API key。 | [Fair Access](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits) 為全機器合計不超過 10 requests/s；須宣告可聯絡的 User-Agent。大量資料應用 nightly bulk。 | SEC 提供公開自動存取，不等於 SEC 擁有申報人及附件的全部著作權。內部可保存索引、XBRL factual data、回應雜湊與必要證據；不得把 filing／附件再散布權推定為已取得。美國政府網站也可能含第三方著作，[USAGov 明示應向管理機關確認](https://www.usa.gov/government-copyright)。 | **適用（核心）**：財報、公告、首次可得時間與可追溯性。不可當作完整行情、公司行動或一般新聞來源。 | 七年保存完整 filing／附件及對團隊顯示原文的依據；外國發行人附件之權利；是否只保存 hash、必要節錄與 SEC URL 即足夠。 |
| NYSE／ICE — [歷史資料](https://www.nyse.com/data-products/)、[Corporate Actions](https://www.nyse.com/market-data/corporate-actions)、[Market Event Feed API](https://www.nyse.com/market-data/corporate-actions/market-event-feed) | NYSE Group 五個美股市場的 proprietary real-time／historical trades、quotes、closing prices、security master；MEF 與公司行動套件含 60 多種事件，如股利、分割、IPO、停牌與下市。只涵蓋 NYSE Group 產品所定義的市場／listing，並非天然等於全美 consolidated market。 | 依產品而異；官方 catalog 沒有單一保證。MEF 明示含 historical corporate actions；[歷史產品價目](https://www.nyse.com/publicdocs/nyse/data/NYSE_Historical_Market_Data_Pricing.pdf)逐產品列開始日期及 back-history 費。 | 行情依所購即時或 EOD 產品；公司行動提供日內與每日更新。 | MEF HTTPS API；其他產品依 SFTP、AWS S3／Cloud Streaming 或專用 feed。 | 付費購買；entitlement 與價格依訂單／產品。 | 公開產品頁未給 MEF 通用數字；依合約與技術規格。 | 市場資料使用、non-display、derived data、internal distribution 與歷史保存受 NYSE／ICE 政策及訂單約束；公開 sample 不是生產授權。[NYSE 政策入口](https://www.nyse.com/market-data/policies)另列 non-display 與 historical-use 政策。 | **簽約後適用**：美股行情／公司行動的第一方候選 adapter；股票池含 Nasdaq-listed 標的時仍需確認 consolidated coverage 或另一來源。 | 訂單是否含模型訓練與 backtest 的 non-display analysis、七年 raw/history、備份、內部多人、衍生特徵／解釋、模型終止後 retention；標的與 tape coverage。 |
| Nasdaq, Inc. — Nasdaq exchange data／[資料產品協議與表單](https://www.nasdaqtrader.com/Micro.aspx?id=OptionsAgreements) | Nasdaq Basic／Last Sale／TotalView 等交易所資料，以及需另購的 reference／corporate action 類產品。Nasdaq Basic 是 Nasdaq market center 與 FINRA/Nasdaq TRF 的 BBO／last sale，不應誤稱 consolidated 全市場。 | 依產品／訂單；公開政策沒有對所有產品給共同歷史深度。 | 即時、延遲或歷史依 entitlement。 | 直接 data feed、經核准 vendor 或所購歷史／reference 交付服務。 | 需供應商或 Nasdaq 核准、協議與適用費用；non-display 有獨立 application／license。 | 依產品與交付管道。 | [美股資料政策](https://www.nasdaqtrader.com/content/AdministrationSupport/Policy/USEquitiesandOptionsDataPolicies.pdf)區分 display、non-display、data feed 與 redistribution；部分 display entitlement 明示不得把資料放上 shared drive 或送入另一系統。不能由「公開頁可看」推導模型可用。 | **簽約後適用**：可補 Nasdaq listing／market-center 資料；是否能單一供應商提供調整後全美 EOD 需由正式報價證明。 | 同 NYSE，且確認使用者分類、non-display analysis、derived data、consolidated coverage、公司行動與 symbol history 是否在同一訂單。 |
| Nasdaq Data Link — [API 入門](https://docs.data.nasdaq.com/v1.0/docs/getting-started)、[Data License Terms](https://data.nasdaq.com/terms) | 市集型資料平台；free／premium dataset 的來源、範圍與品質各異。不能把 Nasdaq 品牌當作每個 dataset 都由交易所擁有或都可商用。可作行情、基本面、另類資料的付費 adapter。 | dataset-specific。 | dataset-specific。 | REST Tables／Time-series API、bulk download 與 client libraries。 | free account API key；premium 逐資料集訂閱／order form。 | [官方 rate limits](https://docs.data.nasdaq.com/docs/rate-limits-1)：free authenticated Tables 300 calls/10s、2,000/10min、50,000/day、concurrency 1；premium 5,000/10min、720,000/day；bulk 另有限制。文件站公告 2026-08-31 退役，實作須追蹤新 Data Access Tools。 | Data License Terms 將實際權利交由 order form 與第三方 provider terms；一般條款禁止未授權重傳／再散布／納入 database 或 SaaS。Derived Data 雖由 client 擁有，對外散布仍需訂單明載或事前書面核准。 | **簽約後適用**，不能將 free dataset 當 MVP 生產行情授權。 | 每一 dataset 的 owner、license、歷史版本、修訂、raw retention、derived output、團隊／SaaS 使用及終止處理；2026-08-31 文件遷移後端點與 SLA。 |
| Alpha Vantage, Inc. — [API docs](https://www.alphavantage.co/documentation/)、[Terms of Service](https://www.alphavantage.co/terms_of_service/) | 全球股票 raw／adjusted OHLCV、split／dividend；market news & sentiment index、earnings estimates 等。官方稱日線與調整日線超過 20 年，新聞端點可按 ticker、主題與發布時間篩選，單次上限 1,000。 | 行情 20+ 年；新聞 docs 提供歷史查詢但未承諾固定最早日期；earnings call transcripts 稱 15+ 年。 | 歷史／延遲／即時依 endpoint 與 entitlement。 | HTTPS REST，JSON／CSV；另有官方 client examples。 | API key；多項 adjusted、即時或進階 endpoint 是 premium。標準限制為 25 requests/day，[premium](https://www.alphavantage.co/premium/)另售。 | 標準 25/day；premium 頁稱無 daily limit，但仍應以商業合約／SLA 為準。 | 免費 EULA 只授權 personal non-commercial；條款更明確把超出個人使用的 investment analysis、research、testing、monitoring，以及代表組織使用列為 commercial，需另行書面協議。Terms 沒有給新聞原文七年保存或再散布權。 | **只有商業書面授權後適用**。它技術上可快速補齊 EOD、公司行動、新聞索引與估計值，但免費 key 不符合本系統。 | 商業授權是否含原始行情／新聞 payload 七年保存、新聞全文與訓練、internal multi-user、derived embeddings／sentiment／SHAP、估計值版本、終止後保留、資料來源方的下游限制。 |
| 各上市公司 IR／newsroom／RSS | 發行人第一方新聞稿、財報簡報、逐字稿、投資人活動與公司公告；範圍只到該發行人，格式與時間欄品質不一致。 | 發行人與網站改版而異，無共同保證。 | 發行人發布時；可能在資訊截止點之後補檔或改頁。 | RSS、email、HTML、PDF 或少數 issuer API。 | 多數頁面可公開讀取，但公開讀取不是自動抓取、保存或再散布授權。 | 各網站 robots.txt、terms、CDN 限制不同。 | 必須逐網域登錄 terms／robots／聯絡許可；禁止繞過登入、CAPTCHA 或付費牆。安全預設只存 metadata、取得時間、URL、hash；全文需明示權利。 | **白名單式補充來源**，不是可全市場預設開啟的爬蟲。重大事件優先由 8-K／6-K 做可追溯主來源。 | 每家公司 robots／terms、附件權利、修改／撤稿處理、七年保存、團隊內顯示及模型訓練是否允許。 |

### 美國與全球總體、央行與國際機構預測

| 來源擁有者／來源 | 資料類型與涵蓋範圍 | 歷史深度 | 更新時間 | 介面 | 認證／費用 | 速率限制 | 原始內容保存／再散布限制 | MVP 適用判斷 | 待法務確認 |
|---|---|---|---|---|---|---|---|---|---|
| U.S. Bureau of Labor Statistics — [Public Data API](https://www.bls.gov/developers/api_FAQs.htm) | CPI、PPI、就業、薪資、生產力、JOLTS 等已發布 BLS survey series。 | series-specific；registered v2 每次 query 最多 20 年（不是資料庫總歷史上限），unregistered 最多 10 年。 | 依 [BLS release calendar](https://www.bls.gov/schedule/news_release/empsit.htm)；應保存 published/first-seen time 與每次回應以辨識修訂。 | REST JSON。 | v1 不註冊；v2 免費註冊 key，每年至少更新註冊一次。 | v2：500 queries/day、50 series/query、20 years/query；v1：25/day、25 series/query、10 years/query；兩者 50 requests/10s。 | [BLS 明示其發布物除既有著作權圖片／插圖外皆為 public domain](https://www.bls.gov/opub/copyright-information.htm)，可無須許可使用，要求標示來源；徽章／商標除外。 | **適用（核心）**：直接向權利擁有者取數，優於從 FRED 轉取相同 series；可保存 raw 與修訂快照。 | 若將 release 文稿全文對外呈現，仍排除第三方照片／插圖及 BLS 標章；資料引用格式。 |
| U.S. Bureau of Economic Analysis — [Data API user guide](https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf) | NIPA／GDP、personal income/outlays、industry、regional、international transactions／investment、international services 等官方經濟資料。 | dataset-specific；API 參數可列 available years，部分支援 `ALL`，不可用統一開始年推定。 | API 與其他 BEA 發布物遵循同一 [release schedule](https://www.bea.gov/news/schedule/)，多數 dataset 每月或依該表更新。 | HTTPS GET；JSON 或 XML。 | 免費 BEA API key。 | 100 requests/min、100 MB/min、30 errors/min；超過回 429 與 Retry-After，數字可調整。 | [BEA 明示除另有註明外網站資訊屬 public domain](https://www.bea.gov/index.php/help/faq/145)，可使用／重製且建議註明來源；logo 不在此授權內。 | **適用（核心）**：可保存 raw、metadata 與每次 release snapshot。 | 逐 dataset 檢查是否有「另有註明」或第三方來源；引用與標章規範。 |
| Federal Reserve Board — [Data Download Program](https://www.federalreserve.gov/datadownload/help/default.htm)、[2026 migration notice](https://www.federalreserve.gov/data/data-download-fred-information.htm) | Board 統計發布（利率、貨幣、工業生產等），CSV／Excel／SDMX。 | series-specific，可取 complete history。 | 隨各統計發布；Board 已公告 2026-11 移除 DDP「Build Your Package」，並將程式化 bulk 取數導向 FRED API v2；直接 XML package 仍是手動替代。 | DDP download／preformatted package；未來 FRED API v2。 | DDP 公開；FRED API 需 account/key。 | DDP 公開文件未列固定 rate。FRED 規則見下一列。 | DDP 文件未提供涵蓋所有 series 的 blanket reuse／redistribution license；聯邦政府 work 的一般規則也不涵蓋第三方內容。只保存已確認為 Board-owned 的 factual series，logo、第三方 series 及對外再散布另審。 | **只作過渡與來源核對，不新建長期 DDP adapter**。長期應在 FRED allowlist 與直接 Board package 之間取捨，且保留 source owner。 | DDP 退役後 direct package 的可用性；個別 Board series 是否含第三方權利；若轉 FRED，終止刪除義務如何與七年保存相容。 |
| Federal Reserve Bank of St. Louis — [FRED／ALFRED API](https://fred.stlouisfed.org/docs/api/fred/series/alfred.html)、[observations/vintages](https://fred.stlouisfed.org/docs/api/fred/series_observations.html) | 聚合美國及全球總體、利率與市場 series；ALFRED 的 real-time period／vintage 可重建過去某日已知資料，符合 point-in-time 特徵需求。 | series-specific；API 可取 earliest available；vintage query 提供歷次首次發布／修訂。 | 隨來源與 FRED ingest；release calendar 明示來源日期不保證即為 FRED 可用時間，故仍須記錄 first-observed-at。 | HTTPS API JSON/XML/XLSX/CSV。 | 免費 FRED account／API key。 | [Terms](https://fred.stlouisfed.org/docs/api/terms_of_use.html)不承諾固定上限，得隨時調整；不得 unreasonable bandwidth。 | **不能整站視為開放資料**：Terms 明示 series 可能屬第三方，非個人用途須取得 owner 許可；copyrighted series notes 含 `Copyright`。應用須顯示指定的非背書聲明。更重要的是，FRED API 合約終止時要求刪除 API copies，可能與七年保留衝突。 | **條件式適用**：只 allowlist 已由資料擁有者另行授權、且 retention 與終止條款可接受的 series。BLS／BEA 等優先直連；ALFRED 可作 research baseline，不可不經權利審查直接落入生產 raw store。 | 每一 series owner／notes／license；終止後刪除與法定／模型可復現保留如何處理；多人介面須加入 FRED terms link、非背書聲明與 privacy policy。 |
| World Bank — [Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392)、[Dataset Terms summary](https://data.worldbank.org/summary-terms-of-use) | 約 16,000 個指標、45+ databases；WDI、國際債務、人口、發展與部分高頻資料。適合全球景氣／國別特徵，不是個股法人目標價。 | 很多 series 超過 50 年，實際依 indicator metadata。 | dataset-specific；應使用 metadata／更新時間與本地 first-seen snapshot。 | V2 REST，XML／JSON；無需 API key。 | 免費、無認證。 | 公開 docs 只要求 reasonable usage，未給固定數字。 | Dataset 預設（metadata 未另註時）CC BY 4.0 加附加條款，可複製、散布、改作及商用並須歸屬；但第三方 dataset／indicator 可能禁止再利用，必須讀 metadata 並保留下游 attribution。 | **allowlist 後適用**：只接 metadata 明確為 World Bank open-data terms／CC BY 4.0 的 indicator。 | 每個 dataset／indicator 的第三方 source 與額外 terms；介面、報告及 sublicense 的 attribution chain。 |
| IMF — [IMF Data WEO](https://data.imf.org/Datasets/WEO)、[WEO FAQ](https://data.imf.org/en/Datasets/WEO/Frequently-Asked-Questions) | WEO 國家／群組的 GDP、通膨、失業、國際收支、財政、貿易與 commodity prices；含 IMF staff forecast，適合作「國際金融機構預測」特徵。 | 1980 至今；多數 series 預測未來五年；select key indicators 有自 1990 年的 historical forecast 檔，其他 archived WEO editions 自 1999 年。 | WEO 每年 April、October 完整發布；January、July update 只更新選定經濟體兩年 real GDP forecast，沒有完整 database。 | Data Portal download（Excel/CSV）與 portal API；API 入口會導向 sign-in。 | Portal sign-in／API entitlement 的費用與 key 規則未在無登入官方頁清楚列明。 | 未在公開 WEO／API 頁查得固定數字。 | FAQ 允許在 written work 引用 WEO data 並要求 citation，但這不足以明示授予本系統七年 raw copy、模型訓練、內部多人與再散布權；其 Copyright and Usage 頁查核時回 403，故不推定允許。 | **法律確認前不可自動啟用**。可先保留 adapter schema，待取得適用 terms／書面許可後接入；每個 WEO edition 必須保存 vintage，而不是覆寫。 | 取得可歸檔的當期 Copyright and Usage 條款；API 認證／費用／rate；raw retention、internal analytics、derived model、forecast display 與第三方 underlying data 權利。 |
| OECD — [Data Explorer API](https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html)、[Terms & Conditions](https://www.oecd.org/en/about/terms-conditions.html) | SDMX datasets，含 leading／short-term indicators 與 Economic Outlook 國家預測。EO database [每年春末、秋末兩次](https://www.oecd.org/en/topics/sub-issues/economic-outlook/key-facts-about-the-oecd-economic-outlook.html)。 | dataset／edition-specific；API 可指定 startPeriod，EO editions 有版本識別，應逐版歸檔。 | 多數 dataset 低頻更新；部分月／日；contentconstraint 提供 ValidFrom。 | public SDMX REST；XML、JSON、CSV。 | 免費；不需在請求中提供 key。 | [2026 best practices](https://www.oecd.org/en/data/insights/data-explainers/2024/11/Api-best-practices-and-recommendations.html)：最多 60 data downloads/hour；超過暫封；VPN／匿名來源流量不允許。 | Terms 原則允許下載、複製、改作、散布、分享、嵌入與商用並要求 attribution；但明示部分 Data 有第三方權利／額外限制，使用者須看 metadata/source 並自行取得許可。 | **allowlist 後適用**：EO 及 OECD-owned series 可作機構預測／全球特徵；固定 dataflow version、保存 metadata 與 citation。 | 各 dataflow 的 source tab／第三方權利；衍生模型與介面引用；VPN 限制對部署網路的影響。 |
| Bank for International Settlements — [BIS SDMX API](https://stats.bis.org/api-doc/v1/)、[statistics terms](https://www.bis.org/terms_statistics.htm) | 國際銀行、debt securities、derivatives、global liquidity、credit、property prices、exchange rates、policy rates 等全球金融統計。 | dataset-specific；bulk 可取完整可用 series，無共同開始年保證。 | 依 [BIS release calendar](https://data.bis.org/release-calendar?view=list)，從日／月到季不等。 | public SDMX REST；JSON、XML、CSV；bulk ZIP。 | 免費、無 key。 | 未公告固定數字；BIS 可限制／封鎖 IP，並監控 API 使用。Portal UI 一次 export 最多 4,500 series，bulk 可下載完整 dataset。 | BIS 稱 statistics 使用不受限制，但列六項條件：須引用、翻譯聲明、不誤導／暗示背書、商業產品內納入不得對使用者造成額外收費、免責、非投資建議；「No other use」。部分 series 另列 national／commercial source，需一起遵守特定 copyright。 | **allowlist 後適用**：全球金融狀況與信用／匯率特徵；內部研究 MVP 通常吻合，但不可忽略來源 metadata。 | 收費 SaaS／商業產品是否觸發「不得造成額外收費」；各國／商業 source 的額外條款；七年 raw 與 derived display。 |

## 資料缺口與產品決策

| 必要能力 | 已查得可直接支撐者 | 缺口／決策 |
|---|---|---|
| SEC 申報、財報與公告 | EDGAR API、bulk、Archives | 可做 MVP 核心；保存全文附件與顯示原文仍採最小化及法務審查。 |
| 美國調整後 EOD 行情、symbol history、全股票池公司行動 | NYSE、Nasdaq、Alpha Vantage 等皆有技術產品 | 沒有查得同時滿足「免費、全美、商用／團隊、七年 raw、non-display 模型訓練」的第一方授權。必須採購或由部署者自備相符 feed；未提供時行情模態應回 `data_unavailable`，不可暗中換成網頁抓取。 |
| 新聞索引／情緒 | Alpha Vantage 有技術端點；發行人 IR 可作補充 | 免費 Alpha key 不允許此研究系統；新聞全文權利未明。MVP 應以 licensed metadata feed 加授權全文白名單設計，不能把 URL 可讀等同可訓練與保存。 |
| 法人個股預測／consensus | Alpha Vantage earnings estimates 提供技術候選 | 未查得官方免費且有七年 vintage 與商用權的完整 consensus。做 optional licensed adapter；不得以當期數值覆寫歷史、造成 look-ahead。 |
| 美國總體資料 | BLS、BEA、Fed／FRED | BLS／BEA 優先直連；FRED 只 allowlist，ALFRED vintages 在授權可接受時用於 point-in-time。 |
| 全球總體與機構預測 | World Bank、OECD、BIS、IMF WEO | World Bank／OECD／BIS 逐 dataset allowlist；IMF 先補齊 usage terms 才啟用。WEO／EO 必須保存 edition/vintage 與 forecast/actual 狀態。 |

## 實作要求

### Source registry 與授權閘門

每個 adapter 必須先在 `source_registry` 登記，至少包含：`source_owner`、`dataset_id`、`source_url`、`terms_url`、`terms_checked_at`、`license_class`、`allowed_uses`、`raw_retention_days`、`redistribution_scope`、`attribution_text`、`credential_secret_ref`、`rate_policy`、`robots_checked_at`、`legal_review_status`、`enabled`。資料集而非網域是最小授權單位；World Bank、FRED、OECD、BIS 尤其如此。

`enabled=true` 的必要條件：

1. 存取方式是官方 API、bulk、RSS 或條款允許的自動存取頁面；
2. 允許用途明確包含內部研究與模型訓練；
3. retention 能滿足七年或系統另有可合法保存的最小證據方案；
4. 若介面要給多人查看，internal distribution／display 權已涵蓋；
5. 能遵守 attribution、速率、刪除、終止與來源特定限制；
6. terms URL、查核時間與合約／訂單 ID 都進版本譜系。

### Adapter 分級

- `public-domain-direct`：BLS、BEA 的明確 public-domain datasets；保存 raw response、headers、release time、hash。
- `open-data-allowlist`：World Bank、OECD、BIS；逐 dataset 驗 metadata 與第三方權利後啟用。
- `contract-required`：NYSE、Nasdaq、Nasdaq Data Link、Alpha Vantage、其他行情／新聞／consensus feed；沒有 secret 與有效 contract record 時保持 disabled。
- `legal-hold`：IMF、未核准 FRED series、未審查 issuer IR；只保留設定，不排程抓取。

### 時間點與版本

- 同時保存事件時間、來源發布時間、首次取得時間與 ingestion time；資訊截止點判斷用「當時實際可得」而不是觀察期日期。
- 宏觀資料保存每個 vintage；WEO／OECD EO 保存 edition ID、release date、latest-actual marker 與 forecast horizon。
- 行情與公司行動保留 as-traded、adjustment event 及 adjustment version，不能只存目前的 adjusted close。
- 新聞保存權不足時只存索引證據與 hash，不保留全文；模型特徵也須記錄其授權來源與可重現方法。
- 條款變更監控應定期 re-fetch `terms_url` 並比對 hash；出現變更、來源要求刪除或合約到期時立即停用 adapter、告警並依 retention policy 隔離／刪除。

## 官方來源索引

- SEC：[EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)、[Fair Access](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits)
- BLS：[API FAQ](https://www.bls.gov/developers/api_FAQs.htm)、[Copyright](https://www.bls.gov/opub/copyright-information.htm)
- BEA：[API Guide](https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf)、[Public-domain FAQ](https://www.bea.gov/index.php/help/faq/145)
- FRED／ALFRED：[API Terms](https://fred.stlouisfed.org/docs/api/terms_of_use.html)、[Real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)
- World Bank：[Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392)、[Dataset Terms](https://data.worldbank.org/summary-terms-of-use)
- IMF：[WEO dataset](https://data.imf.org/Datasets/WEO)、[WEO FAQ](https://data.imf.org/en/Datasets/WEO/Frequently-Asked-Questions)
- OECD：[API](https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html)、[Terms](https://www.oecd.org/en/about/terms-conditions.html)、[rate limits](https://www.oecd.org/en/data/insights/data-explainers/2024/11/Api-best-practices-and-recommendations.html)
- BIS：[API](https://stats.bis.org/api-doc/v1/)、[statistics terms](https://www.bis.org/terms_statistics.htm)
- NYSE：[Data Products](https://www.nyse.com/data-products/)、[Market Data Policies](https://www.nyse.com/market-data/policies)
- Nasdaq：[Agreements](https://www.nasdaqtrader.com/Micro.aspx?id=OptionsAgreements)、[U.S. data policies](https://www.nasdaqtrader.com/content/AdministrationSupport/Policy/USEquitiesandOptionsDataPolicies.pdf)
- Nasdaq Data Link：[Getting Started](https://docs.data.nasdaq.com/v1.0/docs/getting-started)、[License Terms](https://data.nasdaq.com/terms)
- Alpha Vantage：[API documentation](https://www.alphavantage.co/documentation/)、[Terms](https://www.alphavantage.co/terms_of_service/)

# 台股 10 掛牌選樣與生命週期證據

查證日期：2026-08-15

證據版本：`twse-10-selection-evidence-v1`

## 結論

下列 10 個 XTAI 掛牌是 ticket 06 台股 manifest 可採用的最小集合：8 檔為 TWSE 2026 Fact Book 所列的 2025 年底大型股票，另加入 2021-01-06 終止上市的晶電 `2448`，以及同日經股份轉換新掛牌的富采 `3714`。第一方證據足以支持：

- 10 檔均為普通股／common stock，而不是特別股、ETF、權證或存託憑證；
- `2887` 在相同外部代號下由台新金更名為台新新光金；
- `2330` 有可核對的現金股利除息事件；
- `2317` 有 2025-07-30 暫停、2025-07-31 恢復交易的完整事件；
- `2448` 是訓練歷史中的普通股終止上市案例，`3714` 是股份轉換後的新普通股掛牌；
- `2448` → `3714` 是兩個不同 listing identity 的 predecessor／successor external-code transition，不是同一 listing 的 ticker rename；
- TWSE 有 2001 年以前的星期六縮短交易制度，可作歷史 shortened-session／half-day calendar case。

限制也必須保留：本研究沒有找到**同一 listing identity** 的 security code／ticker 值變更證據；`2887` 支持的是 name／symbol history。`2448` → `3714` 則是有官方股份轉換證據的跨 listing external-code transition，不能把兩種情況混為同一件事。

這份文件只證明選樣與事件事實，**不**證明資料自動存取、保存、建模、再散布或商業使用權，也不代表 `DEP-MKT-TW-01` 已成立。

## 選樣規則

TWSE 2026 Fact Book 的「2025 年底股票市值前 30 大」表列出本選樣中的 8 檔現行股票，並明確註記 stock market capitalization **不包含特別股**。同一 Fact Book 的 2025 market highlights 又把 common stock 與 preferred stock 分立統計。因此，下列 8 檔可作 2025 年底普通股 selection evidence。[TWSE 2026 Fact Book：股票市值前 30 大](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html)；[TWSE 2025 Market Highlights](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/0.0101.html)

`as_of` 均為 2026-08-15。`listing_key` 只是 manifest 候選鍵，不應取代系統內不可重用的 issuer／security／listing ID。

| # | listing_key | 掛牌名稱／狀態 | 選樣角色 | 第一方證據 | Identity caveat |
|---:|---|---|---|---|---|
| 1 | `XTAI:2330:COMMON` | 台灣積體電路製造，現行普通股 | 一般普通股；現金股利；歷史 shortened-session 候選 | 2026 Fact Book 列 `2330` 為 2025 年底股票市值第 1；台積公司說明其股票自 1994-09-05 以 `2330` 在 TWSE 上市；TWSE 靜態公告列 2025-12-11 為 `2330` 除息日。[Fact Book](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html)；[台積公司 FAQ](https://investor.tsmc.com/schinese/faq)；[TWSE 2025-12-11 除息公告](https://wwwc.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69a3d6cf9019b078b883d0440.pdf) | `2330` 是有時間效力的外部 alias；NYSE ADS `TSM` 是另一 listing。股利事件應連到普通股 security，不可只用 ticker join。 |
| 2 | `XTAI:2317:COMMON` | 鴻海精密，現行普通股 | 一般普通股；暫停／恢復 | 2026 Fact Book 列市值第 2。TWSE 2025-07-29 公告明載「上市普通股」自 7 月 30 日暫停；7 月 30 日公告明載自 7 月 31 日恢復。[Fact Book](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html)；[TWSE 暫停公告](https://wwwc.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d697fc438f01985582a3690192.pdf)；[TWSE 恢復公告](https://wwwc.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d697fc438f01985ae1745301be.pdf) | 暫停日不是零成交的一般 session，不可補值；恢復日也不是新 security。 |
| 3 | `XTAI:2308:COMMON` | 台達電子，現行普通股 | 一般普通股基線 | 2026 Fact Book 列 `2308` 為 2025 年底股票市值第 3，表註排除特別股。[Fact Book](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html) | 外部代號只是 alias；正式 manifest 仍須配置 issuer／security／listing ID 及 validity interval。 |
| 4 | `XTAI:2454:COMMON` | 聯發科技，現行普通股 | 一般普通股基線 | 2026 Fact Book 列 `2454` 為 2025 年底股票市值第 4，表註排除特別股。[Fact Book](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html) | 同上；不能以公司名稱或代號作永久身分。 |
| 5 | `XTAI:2881:COMMON` | 富邦金控，現行普通股 | 一般普通股基線 | 2026 Fact Book 列 `2881` 為 2025 年底股票市值第 5，表註排除特別股。[Fact Book](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html) | `2881` 的普通股與該公司另發行的特別股是不同 securities，不能只按 issuer 合併。 |
| 6 | `XTAI:3711:COMMON` | 日月光投控，現行普通股 | 一般普通股基線 | 2026 Fact Book 列 `3711` 為 2025 年底股票市值第 7，表註排除特別股。[Fact Book](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html) | `3711` 是台灣掛牌；NYSE `ASX` 是另一 listing，不得因同一 issuer 而混併。 |
| 7 | `XTAI:2382:COMMON` | 廣達電腦，現行普通股 | 一般普通股基線 | 2026 Fact Book 列 `2382` 為 2025 年底股票市值第 8，表註排除特別股。[Fact Book](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html) | 外部代號與顯示名稱都必須做 versioned assertion。 |
| 8 | `XTAI:2887:COMMON` | 台新金 → 台新新光金，現行普通股 | name／symbol history；吸收合併 | TWSE 2024-08-22 公告以舊稱「台新金」、代號 `2887` 指稱其上市普通股；發行人公告以 2025-07-24 為合併基準日，台新金為存續公司並更名「台新新光金控」；2026 Fact Book 以相同 `2887` 列 TS Financial Holding，TWSE 當期報表顯示「台新新光金」。[TWSE 舊名稱普通股證據](https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6913637e401917a50fb650117.pdf)；[發行人合併／更名公告](https://www.tsholdings.com.tw/tsh/relations/major/1743420780000/)；[Fact Book](https://www.twse.com.tw/downloads/zh/about/company/factbook/2026/1.04.html)；[TWSE 當期市場報表](https://www.twse.com.tw/exchangeReport/MI_INDEX?response=html&type=ALLBUT0999) | 必須保存舊名與新名的 validity interval。本案**不是**已證實的同一 listing security-code/ticker value change；不可把 name change 寫成代號變更。合併發行的新特別股也不能混入 common-share listing。 |
| 9 | `XTAI:2448:COMMON` | 晶電，歷史普通股，已終止上市 | 訓練歷史下市；predecessor | TWSE 新聞稿明載 `2448` 普通股於 2021-01-06 終止上市，並以每 1 股換發 `3714` 普通股 0.5 股；TWSE 終止上市公司表獨立列出 `2448`。[TWSE 股份轉換新聞稿](https://investoredu.twse.com.tw/Mobile_pages/..%2FFileSystem%2FFileUpload%2F3b0709ec-90bf-4051-975b-8f28b6c035aa.pdf)；[TWSE 終止上市公司表](https://www.twse.com.tw/company/suspendListingCsvAndHtml?lang=zh&startYear=&type=html) | listing validity 於 2021-01-06 結束。`2448` 不因換股而變成 `3714`；兩者必須是不同 listing IDs，以 predecessor／successor relationship 連接。 |
| 10 | `XTAI:3714:COMMON` | 富采，現行普通股 | 股份轉換後新掛牌；successor | 同一 TWSE 新聞稿明載 `3714` 普通股 685,952,710 股於 2021-01-06 上市；TWSE 新上市公司表註記 `2448`、`3698` 同日下市轉投控，當期市場報表仍列 `3714`。[TWSE 股份轉換新聞稿](https://investoredu.twse.com.tw/Mobile_pages/..%2FFileSystem%2FFileUpload%2F3b0709ec-90bf-4051-975b-8f28b6c035aa.pdf)；[TWSE 新上市公司表](https://www.twse.com.tw/company/newlisting?response=html)；[TWSE 當期市場報表](https://www.twse.com.tw/exchangeReport/MI_INDEX?response=html&type=ALLBUT0999) | `3714` 是 2021-01-06 新掛牌的普通股 listing，不得把 `2448` 的歷史 raw prices 改碼後直接接續。換股比例是 versioned company action。 |

## Acceptance coverage

| 要求案例 | 可用 listing／event | 判斷 |
|---|---|---|
| 普通股 | 8 檔由 2026 Fact Book 的排除特別股股票表支持；`2448`／`3714` 由 TWSE 股份轉換新聞稿明載普通股 | **有足夠官方證據** |
| external code transition 與 name history | `2448` → `3714` 是兩個 listing 的 predecessor／successor code transition；`2887` 是同代號下台新金 → 台新新光金的 name history | **有足夠官方證據**；沒有同一 listing ticker value mutation 的證據 |
| 公司行動 | `2330` 於 2025-12-11 除息；`2448` 每 1 股換 `3714` 0.5 股；`2887` 於 2025-07-24 合併及更名 | **有足夠官方證據** |
| 暫停與恢復 | `2317` 於 2025-07-30 暫停、2025-07-31 恢復 | **有足夠官方證據** |
| 訓練歷史中的下市 | `2448` 於 2021-01-06 終止上市 | **有足夠官方證據**；manifest 仍須讓 training window 實際包含該日以前的資料 |
| 台灣 half-day／縮短 session | 2001 年以前的星期六縮短交易制度 | **有制度層官方證據**；近代排定 half-day 沒有證據 |

## Half-day／session 的官方邊界

TWSE 60 週年特刊記載：

- 1973-04-16 起，星期一至五為 09:00–12:00（中場休息 15 分鐘），星期六為 09:00–11:00；
- 1998-04-04 起，星期六交易延長至 12:00；
- 2001 年 1 月配合週休二日，改成星期一至五交易至 13:30。[TWSE 60 週年特刊：市場集會時間](https://www.twse.com.tw/staticFiles/product/publication/twse60/html/178/)

TWSE 2000-11-20 正式函釋鎖定生效日：自 2001-01-01 起，集中市場改為每週一至五 09:00–13:30，週六不交易及交割。[（89）台證交字第 033650 號](https://twse-regulation.twse.com.tw/TW/int/DAT01_print.aspx?FLCODE=FE063588)

`2330` 自 1994-09-05 已上市，因此在制度上跨越 2001 年以前的星期六 shortened-session regime。不過，本研究沒有選定並保存某一個實際星期六的官方日曆／成交快照；正式 manifest 若要主張一個特定 half-day session，仍須加入該日不可變 calendar evidence。

目前 TWSE 一般交易為週一至週五 09:00–13:30；春節前非交易日被明確列為「市場無交易，僅辦理結算交割」或完整休市，沒有近代排定 shortened session。[TWSE 集中市場交易制度](https://www.twse.com.tw/zh/products/system/trading.html)；[TWSE 市場開休市日期](https://www.twse.com.tw/zh/trading/holiday.html)

因此：

1. 不得把完整休市、僅交割日或個股暫停日改標成 half-day；
2. 若 manifest training history 延伸至 2001 年以前，可建立歷史星期六 shortened-session calendar version；
3. 若 training history 僅涵蓋近七年，應將 `scheduled_half_day` 標為 `not_applicable`，不能製造近代 TWSE half-day；
4. 若 acceptance criterion 嚴格要求近七年內真正排定的 half-day，現有官方證據不足，必須先澄清規格。

## Manifest 落地要求

研究證據轉入 versioned manifest 時，至少應保存：

- `selection_evidence_version = twse-10-selection-evidence-v1`
- `selection_as_of = 2026-08-15`
- 不可重用的 `issuer_id`、`security_id`、`listing_id`
- `venue = XTAI`、security type、currency 及具有效期的外部代號／名稱 assertions
- 每個 source URL、來源擁有者、事件日、生效區間、查得時間及 content hash
- `2448` 與 `3714` 分立 listing IDs，以及帶換股比例的 predecessor／successor edge
- dividend、suspension、resume、delisting 分立事件，不把它們折成一般 OHLC 缺值
- calendar version 及 session type；`scheduled_half_day` 不適用時要有明確 reason

TWSE 的當期市場報表會改變；正式 acceptance bundle 必須保存查得回應的不可變副本，不能只保留 mutable URL。保存回應的技術行為與使用權仍須由 `DEP-MKT-TW-01` 或明確開放資料條款另行核准。

## 未由本研究證明的事項

- 同一 listing identity 的 security code／ticker 值曾變更；本文件證明的是 `2887` name history，以及 `2448`／`3714` 兩個 listing 間的 external-code transition；
- 任一網站、OpenAPI、報表或 PDF 的自動擷取、七年保存、模型訓練、內部多人顯示或再散布權；
- `DEP-MKT-TW-01` 的契約、entitlement、用途、保存期、離約後留存或正式 source-steward approval；
- 10 檔在實際歷史價格來源中的完整 253-session／七年 coverage；
- 一個已保存日曆與成交證據的特定歷史星期六 half-day；
- 近代 TWSE 排定 half-day 的存在；
- 外部股票代號永久不重用，或代號本身足以代表 issuer／security／listing identity。

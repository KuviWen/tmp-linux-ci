# 盤點可合法使用的美國與全球資料來源

Type: research
Status: resolved

## Question

哪些官方或第一方來源可供生產導向 MVP 取得美國股票行情、公司行動、SEC 申報、公司公告、總體經濟、機構預測與新聞索引？逐一記錄涵蓋範圍、歷史深度、更新時點、存取方式、授權／再散布限制、速率限制、可否保存原始內容，以及需要部署者自備的憑證。

## Comments

- 已於繪圖階段指派研究代理；研究只能引用來源擁有者的一手文件。

## Answer

研究結果見 [美國與全球市場資料來源盤點](../../../docs/research/us-global-market-data-sources.md)。

核心結論：SEC EDGAR、BLS、BEA 及逐資料集核准的 World Bank／OECD／BIS 可支撐申報與多數總體資料；FRED／ALFRED 因第三方 series 與終止後刪除義務必須採 allowlist，IMF WEO 在公開使用條款補齊前保持停用。未查得任何第一方免費來源同時授權本系統取得全美調整後行情、完整公司行動、新聞原文與法人共識，並作七年原始保存、內部多人使用及 non-display 模型訓練；這些能力必須透過部署者自備商業授權的 adapter 提供，沒有書面權利時不可抓取或以非官方來源替代。

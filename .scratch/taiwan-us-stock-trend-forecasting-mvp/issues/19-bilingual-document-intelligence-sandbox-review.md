# 19 — 雙語文件情報、sandbox 與 review 路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 讓台灣及美國一手文件經無網路、無 secret 的安全解析，形成可回到原始座標的標準文本、雙語結構、去重、confirmed 標的連結、taxonomy、事件提及、凍結 embedding、市場影響及版本化 review 決策，並以 golden corpus、研究頁與營運證據驗證品質及 abstention。

**Blocked by:** 12 — 台灣官方文件到可追溯研究證據, 13 — SEC 文件到可追溯研究證據

**Trace IDs:** `P3-TRACE-NLP-01`, `GATE-DATA-01`, `GATE-SEC-01`

Status: ready-for-agent

- [ ] DocumentPipeline.process 固定 input datasets、source policies、cutoff、target stage、ProcessingBundleVersion 與 trace，呼叫端不組裝 parser、linker、classifier、embedder 或 event extractor。
- [ ] PDF、Office、HTML、XBRL、文字及 archive 在無網路、無 secret、唯讀且具 CPU／memory／time／output／expansion 限制的 sandbox 執行，禁止巨集、腳本、外部資源與不安全反序列化。
- [ ] 原始 rendition、StandardText／表格／座標映射與 matching fingerprint 分開保存；低 OCR／reading-order／table 品質可 abstain 或選可靠 rendition，不強制產生正式 NLP。
- [ ] Exact duplicate 保留每個來源文件與 policy；near-duplicate DocumentCluster 是版本化投影，merge／split 只建立新裁決及群組版本。
- [ ] Confirmed 標的連結 precision 優先，保存 evidence、角色、方法、candidate set、有效期、confidence 及狀態；錯公司或 ambiguous identity 不能進正式特徵。
- [ ] Taxonomy、EventMention、MarketEvent、embedding 及 MarketImpactAssessment 均有 evidence Segment、完整 probability／confidence、abstention、training cutoff、prospective／retrospective mode 與 bundle lineage。
- [ ] Review queue 只建立 confirm／reject、merge／split、event adjudication 與 extraction-failure 決策，保存 actor、guideline、理由及 evidence，並發布新衍生資料集而不修改來源或舊預測。
- [ ] 中英文 golden corpus 達到 structured-fact exact match `>=99.5%`、confirmed-link precision `>=99%`、taxonomy macro-F1 `>=0.80` 且 ECE `<=0.10`、event macro-F1 `>=0.75`；錯公司、錯證據或政策繞過為 hard failure。
- [ ] 惡意文件、archive bomb、parser crash、逾時、低品質 OCR 與不完整附件只隔離受影響物件並形成 health／incident／audit，其他文件仍可產生完整 outcome。
- [ ] 研究頁能由 annotation／影響評估回到允許展示的 Segment、DocumentVersion、SourcePolicyVersion 與 ProcessingBundleVersion，且不把市場影響宣稱為因果或交易理由。

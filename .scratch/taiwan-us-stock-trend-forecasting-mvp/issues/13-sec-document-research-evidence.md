# 13 — SEC 文件到可追溯研究證據

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 將 SEC 8-K、6-K、10-Q、10-K 與 company facts 經共同 DocumentPipeline 建立版本化美國文件證據、FinancialFact、confirmed 標的連結及 annotation，並一路呈現在特徵支援、繁中研究介面、來源健康與 audit 中。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Trace IDs:** `P3-ENTRY-01`, `P3-TRACE-DOC-US-01`

Status: ready-for-agent

- [ ] SEC adapter 遵守核准的識別、速率、User-Agent、存取、保存及模型用途政策，產生完整擷取收據與 CoverageReport。
- [ ] Filing、附件、HTML、inline XBRL、XBRL facts 及更正／撤回以 Document／Version／Rendition／Segment 共同模型保存，不建立美國專用文件 schema。
- [ ] CIK 與來源明示識別碼只能透過時間點有效的 IdentityAssertion 連到發行人、證券或掛牌；share class／ADR 不被錯誤合併。
- [ ] Company facts 與 filing XBRL 保存 concept、period、unit、dimensions、filing version、availability、revision 及 evidence context，衝突並列並產生品質結果。
- [ ] 只有 confirmed 標的連結、合格 first-observed time 與 feature-freeze 前完成的 annotation 可進正式 FeatureSnapshot；late 或 retrospective 結果只進後續／研究。
- [ ] 研究頁能在美股掛牌下顯示允許的 filing evidence、FinancialFact、來源政策、first-observed time、處理版本與 lineage，不跳到來源最新內容取代歷史版本。
- [ ] 來源健康、schema／integrity、必要附件、修訂、隔離與處理延遲能由 OperationsControl 外部查詢，受限讀取與 deny 具 audit evidence。
- [ ] 與台灣票 12 的 provider／module／REST contract tests 產生相同外部資料與錯誤語意，只有 adapter、識別與文件格式差異。

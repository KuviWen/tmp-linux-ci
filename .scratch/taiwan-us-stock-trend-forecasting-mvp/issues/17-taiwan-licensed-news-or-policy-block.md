# 17 — 台灣官方文件完整範圍與商業新聞排除路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 將 `official-documents-only` 固定為台灣文字模態的完整產品範圍，明確停用商業新聞 adapter，讓來源、FeatureSnapshot、研究頁、健康與 audit 一致展示 excluded／not-applicable；不得抓取公開新聞網頁、申請試用或建立付費新聞待辦。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Excluded modality:** commercial news；不存在 external gate

**Trace IDs:** `P3-ENTRY-02`, `P3-TRACE-NEWS-01`

Status: ready-for-agent

- [ ] 版本化產品範圍政策將商業新聞標為 `excluded`，不建立 collector、credential、content mode 或 source entitlement。
- [ ] FeatureSnapshot 與 prediction support 將新聞模態標為 `not_applicable`，不把它當成缺值、policy failure 或降級原因。
- [ ] 研究頁與 acceptance bundle 明示 `official-documents-only` 是完整範圍，不顯示「等待新聞權利」或 full-product blocker。
- [ ] 免費新聞網站、中央社公開頁、測試 key、人工複製或任意 provider 不得被選為 fallback，嘗試會產生 audit／health evidence。
- [ ] 既有官方公告、申報及 OGDL 文件仍依各自 SourcePolicyVersion 處理、顯名、修訂與政策刪除，商業新聞排除不擴張其使用範圍。
- [ ] 端到端測試驗證 REST／UI、FeatureSnapshot、health、audit 與 bundle 對 excluded-news 語意一致，且 P3／P5 不因新聞不存在而 blocked。

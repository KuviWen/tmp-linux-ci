# 18 — 美國官方文件完整範圍與商業新聞排除路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 將 `official-documents-only` 固定為美國文字模態的完整產品範圍，以 SEC 官方文件為正式來源並排除商業新聞；來源、FeatureSnapshot、研究頁、健康與 audit 使用與台灣相同的 excluded／not-applicable 語意。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Excluded modality:** commercial news；不存在 external gate

**Trace IDs:** `P3-ENTRY-02`, `P3-TRACE-NEWS-01`

Status: ready-for-agent

- [ ] 版本化產品範圍政策將商業新聞標為 `excluded`，不建立 collector、credential、content mode 或 source entitlement。
- [ ] FeatureSnapshot、prediction support、研究頁與 bundle 將新聞模態標為 `not_applicable`，不建立 full-product blocker。
- [ ] SEC 官方文件保持完整 source-policy lineage、內容模式、修訂與刪除語意；商業新聞排除不改變 SEC 文件身分或使用依據。
- [ ] 免費新聞網站、測試帳號、人工複製或任意 provider 不得 fallback，嘗試會產生 audit／health evidence。
- [ ] 與票 17 的 module／REST／policy interface contract tests 證明兩市場具有相同 excluded-news、支援、顯示與 audit 語意。

# 17 — 台灣授權新聞或明確阻斷路徑

**What to build:** 為台灣新聞建立 contract-required adapter，使具保存、NLP／embedding、模型、內部展示、顯名與刪除權的來源能從擷取走到文件情報、特徵與研究證據；若外部權利未完成，整條路徑仍須可展示 `official-documents-only`／`policy_blocked`，不得抓取公開網頁替代。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**External gate:** `DEP-NEWS-TW-01`

**Trace IDs:** `P3-ENTRY-02`, `P3-TRACE-NEWS-01`

Status: ready-for-agent

- [ ] SourcePolicyVersion 明確記錄 full-content／summary-only／metadata-link-only／disabled 模式及 ingest、retain、NLP、embedding、train、infer、display、export、attribute、delete 權利。
- [ ] 權利合格時，adapter 產生不可變文件版本、擷取收據、first-observed time、涵蓋與來源 evidence；無全文權時不以 memory-only、cache 或臨時下載規避。
- [ ] 合格新聞經文件處理、confirmed 標的連結、去重、annotation 及特徵後，研究頁只顯示來源政策允許的標題、摘要／片段與衍生結果。
- [ ] `DEP-NEWS-TW-01` 缺失、到期或用途不足時，來源保持 disabled／policy-blocked，FeatureSnapshot 顯示新聞模態受阻，產品明示 `official-documents-only`。
- [ ] 免費網站、中央社公開頁、測試 key、人工複製或未核准 provider 不得被自動選為 fallback，嘗試會產生 audit／health evidence。
- [ ] 政策撤回或刪除要求能沿原文、segment、embedding、annotation、feature 及受影響模型產生 impact preview、阻斷與刪除證據。
- [ ] 端到端測試同時涵蓋四種內容模式、entitlement 到期、允許展示與禁止展示，並驗證 REST／UI、FeatureSnapshot、health 與 audit 一致。

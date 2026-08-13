# 18 — 美國授權新聞或明確阻斷路徑

**What to build:** 為美國新聞建立與台灣相同的 contract-required adapter，使合格授權內容能走過文件情報、特徵及研究證據，或在權利不足時端到端維持 `official-documents-only`／`policy_blocked`，而不使用免費網站行情或新聞替代。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**External gate:** `DEP-NEWS-US-01`

**Trace IDs:** `P3-ENTRY-02`, `P3-TRACE-NEWS-01`

Status: ready-for-agent

- [ ] 美國新聞資料集的來源政策逐項記錄內容模式、保存、NLP／embedding、模型、展示、顯名、匯出及刪除權利。
- [ ] 合格來源從擷取、文件版本、first-observed time、CoverageReport、去重、confirmed 標的連結到 FeatureSnapshot 保持完整 source-policy lineage。
- [ ] 研究頁只顯示允許內容及衍生物，metadata-link-only 不產生全文 embedding／事件，summary-only 只使用實際授權摘要。
- [ ] 缺失或失效的 `DEP-NEWS-US-01` 讓 source、feature、prediction support 與產品範圍一致 policy blocked，不以其他網站、測試帳號或任意 provider fallback。
- [ ] 美國新聞與 SEC 官方文件的角色、權威與 Document identity 分開保存，近似轉載不合併來源時間、授權、更正或撤回鏈。
- [ ] Entitlement 撤回與 policy deletion 能阻止新用途、影響下游 artifact／assignment 並產生刪除證明，不改寫既有授權決策。
- [ ] 與票 17 的 provider／module／REST／policy contract tests 證明兩市場具有相同 allow／deny、支援、顯示與刪除語意。

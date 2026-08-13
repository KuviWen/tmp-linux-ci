# 12 — 台灣官方文件到可追溯研究證據

**What to build:** 將合格 MOPS／OGDL 重大訊息、月營收與財報摘要從來源政策、文件版本與安全抽取，經 FinancialFact、confirmed 標的連結及不可變 annotation，形成可供特徵使用且能在繁中研究頁、來源健康與 audit 追溯的台灣官方文件路徑。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Trace IDs:** `P3-ENTRY-01`, `P3-TRACE-DOC-TW-01`

Status: ready-for-agent

- [ ] 台灣來源 adapter 只使用明確允許的官方介面／檔案，綁定有效 SourcePolicyVersion、SourceEntitlement、內容保存模式與顯名要求。
- [ ] Document、DocumentVersion、Rendition、Segment 及附件關係以不可變身分建立，URL／ticker／內容雜湊不被當作永久文件或掛牌主鍵。
- [ ] 初始、更新、更正、撤回、late attachment 及不同 rendition 只建立新版本與關係，不覆寫舊文件或既有正式預測輸入。
- [ ] 月營收與財務資料優先形成可回到官方 context、period、unit、currency、dimension 及首次取得時間的 FinancialFact；OCR／文字推測不得覆寫正式 fact。
- [ ] 只有 confirmed 且符合發布時外部識別碼有效期的標的連結可進個股特徵；歧義 ticker／名稱比對維持 candidate／unresolved 或隔離。
- [ ] 發布的文件情報資料集保存 ProcessingBundleVersion、涵蓋、品質、來源政策、first-observed time、處理完成時間及 evidence pointers。
- [ ] 研究頁只能顯示來源政策允許的標題、摘要、片段、FinancialFact 與 annotation，並能追溯到文件版本及原始證據；禁止內容不以空字串假裝存在。
- [ ] OperationsControl 能查詢來源六維健康、文件資格、隔離、processing lag、修訂與政策狀態，且所有受限處理／展示決策留下安全稽核。

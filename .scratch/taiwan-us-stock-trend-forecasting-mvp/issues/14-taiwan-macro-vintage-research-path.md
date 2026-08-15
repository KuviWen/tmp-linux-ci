# 14 — 台灣總體 vintage 研究路徑

**Zero-cost boundary:** 只接受官方明示的免帳號、免申請、免另行書面契約、免付費公開來源與本機開源運行；缺少資料時縮小支援或 fail closed，不建立採購／entitlement 待辦。

**What to build:** 將中央銀行與主計總處的核准總體 release／vintage 從合法擷取、時間點版本與資料集資格，轉為不可變總體特徵並進入台股 shadow 預測支援；研究頁能顯示實際 vintage，後續修訂不能改寫舊 FeatureSnapshot 或 PredictionRecord。

**Blocked by:** 11 — 發布 P2 正式價量 baseline acceptance bundle

**Trace IDs:** `P3-TRACE-MACRO-01`

Status: ready-for-agent

- [ ] 每個資料集逐一核實來源政策、頻率、release calendar、revision 語意、保存及模型用途，不因同一機關其他資料開放便推定資格。
- [ ] 擷取保存原始 release、first-observed time、業務有效期、vintage／revision、單位、頻率、季調狀態、來源 policy 與 CoverageReport。
- [ ] NormalizedRecordVersion 與資料集 manifest 區分 initial、update／correction，查詢 cutoff 時只能解析當時已可得且有效的 vintage。
- [ ] FeatureFactory 以版本化規則產生利率、通膨、成長、就業或其他核准總體特徵，保存 age、availability、quality、actual dataset version 及 vintage ID。
- [ ] 台股 shadow 預測在總體可用時顯示完整／degraded 支援；來源 late、缺漏或 policy blocked 時明示原因，不以最後值或零值偷偷替代。
- [ ] 繁中標的研究頁能展示實際總體 vintage、發布／首次取得時間、來源政策及相關 FeatureSnapshot lineage，且後續修訂只影響新的預測。
- [ ] 端到端測試以同一歷史 cutoff 重播初始與修訂 vintage，證明舊預測、歷史正式紀錄與當時研究畫面保持不變。

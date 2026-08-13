# 34 — 2,000 掛牌 production baseline 容量路徑

**What to build:** 在台灣 600＋美國 1,400、每日最多 5,000 文件版本、七年時間點資料、50 名研究使用者及 REST 10 sustained／50 burst RPS 的代表性負載下，重訓並核准適用 2,000 掛牌的最佳合格模型，連續三次完成 production-shaped EOD、研究查詢與營運驗收，發布不可變容量報告。

**Blocked by:** 31 — 台灣 600 掛牌完整產品資料資格路徑, 32 — 美國 1,400 掛牌完整產品資料資格路徑, 33 — 國際預測與公司級法人 consensus 路徑

**Trace IDs:** `P5-TRACE-CAPACITY-01`, `GATE-MODEL-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] 2,000-listing support profile 使用合格資料重新建立 TrainingIntent、calibrators、八季回測、gates、approval、rollback target 及五次 shadow，不沿用 100＋100 artifact 的服務資格。
- [ ] 版本化 benchmark manifest 涵蓋台美一般日、半日市、DST、臨時休市、文件尖峰、排程重疊、API sustained／burst、資料複寫與低優先 backfill。
- [ ] Baseline 在 production-shaped staging 連續完整成功三次，以最差一次判定正確性與 deadline，另報 median／p95／p99／變異。
- [ ] 最差 EOD 在 T+105 完成、CPU inference＋attribution `<=10 分鐘`、REST p95 `<=500 ms`、p99 `<=1.5 秒`、錯誤率 `<0.1%`。
- [ ] 關鍵資源 p95 `<=70%` 核准容量，無 OOM、swap thrash、持續 throttling；daily-critical 保留容量在 backfill／training／API／document concurrent load 下仍成立。
- [ ] 每掛牌三期間結果或機器原因、manifests、checksums、probabilities、assignment、policies、projection 與 audit 100% 正確；任何錯誤發布即整次 benchmark 失敗。
- [ ] 四倍 stretch workload 驗證零資料遺失、錯誤發布、政策繞過及 OOM crash loop，baseline 同時保持 SLO，其他 backlog 在 24 小時內排空。
- [ ] 不可變 CapacityReport 綁定 deployment artifact、capacity profile、benchmark manifest、policy、hardware／nodes／storage、autoscaling、measurements、failures、cost、approval 及重現命令，保存七年。

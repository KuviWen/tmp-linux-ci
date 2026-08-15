# 34 — 零成本內部 baseline 容量路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 以台美實際零成本支援池、已觀測時間點資料、實測官方文件量及本機內部使用負載，重訓並核准適用該精確 support profile 的最佳合格模型，連續三次完成 Compose EOD、研究查詢與營運驗收，發布不可變容量報告；不預先承諾 2,000 掛牌或外部硬體。

**Blocked by:** 31 — 台灣零成本完整支援池資料資格路徑, 32 — 美國零成本完整支援池資料資格路徑, 33 — 官方機構預測 optional 路徑與 consensus 排除

**Trace IDs:** `P5-TRACE-CAPACITY-01`, `GATE-MODEL-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [ ] 精確支援池 profile 使用合格資料重新建立 TrainingIntent、calibrators、實際可形成的 folds、gates、approval、rollback target 及五次 shadow，不沿用較小池 artifact 的服務資格。
- [ ] 版本化 benchmark manifest 涵蓋台美適用的一般／shortened session、DST、臨時休市、文件尖峰、排程重疊、內部 API load、備份與低優先 backfill。
- [ ] Baseline 在本機 Compose profile 連續完整成功三次，以最差一次判定正確性與 deadline，另報 median／p95／p99／變異。
- [ ] 最差 EOD 在 T+105 完成、CPU inference＋attribution `<=10 分鐘`、REST p95 `<=500 ms`、p99 `<=1.5 秒`、錯誤率 `<0.1%`。
- [ ] 關鍵資源 p95 `<=70%` 核准容量，無 OOM、swap thrash、持續 throttling；daily-critical 保留容量在 backfill／training／API／document concurrent load 下仍成立。
- [ ] 每掛牌三期間結果或機器原因、manifests、checksums、probabilities、assignment、policies、projection 與 audit 100% 正確；任何錯誤發布即整次 benchmark 失敗。
- [ ] 可由本機資源承受的 stretch workload 驗證零資料遺失、錯誤發布、政策繞過及 OOM crash loop；結果不得擴張 baseline SLO 或股票池聲稱。
- [ ] 不可變 CapacityReport 綁定 deployment artifact、capacity profile、benchmark manifest、policy、實際 hardware／storage、measurements、failures、零外部服務成本、approval 及重現命令，依引用生命週期保存。

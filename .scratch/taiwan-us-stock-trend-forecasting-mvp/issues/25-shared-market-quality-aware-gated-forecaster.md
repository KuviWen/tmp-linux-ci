# 25 — 共享台美 quality-aware gated forecaster

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 將 price anchor、基本面、總體及文件表示整合為共享台美、三期間 quality-aware gated neural candidate，使用市場別正規化／小型 adapter、期間別 gate／head 與六個 calibrators，完整走過 FeatureSnapshot、shadow EOD、research projection 及六個 market × horizon cell 的治理證據。

**Blocked by:** 23 — 基本面與總體 neural 增量路徑, 24 — 凍結多語文件表示 neural 路徑

**Trace IDs:** `P4-TRACE-NEURAL-01`, `P4-TRACE-MARKETS-01`

Status: ready-for-agent

- [ ] 完整 candidate 保持一個共享台美模型，以 market embedding／normalization／小型 adapter 表達差異，不建立分市場 production model 或 listing embedding。
- [ ] 每個 horizon 使用獨立 quality-aware gate、classification head 與價量 residual anchor；optional modalities 只能形成受 mask 的增量。
- [ ] 六個 market × horizon cells 各自計算 bounded class-weight mean loss 再等權平均，掛牌或時間樣本不因市場大小重複。
- [ ] 訓練使用核准 optimizer、early stopping、modality dropout、auxiliary losses 與最多 15M trainable parameters；gate 使用率被監控但不被強迫均勻。
- [ ] 六個 market × horizon calibrators 只使用各 fold calibration 時段；資料不足的 identity fallback 只能研究，不能通過 promotion。
- [ ] ForecastBatch 對每掛牌 × horizon 回傳結果或結構化不可用原因，含校準機率、信心、support、calibration status、OOD／distance、gate reliance 及完整版本引用。
- [ ] 台灣與美國 shadow EOD 分別穿過資料選擇、特徵、推論、權威 ledger、REST／UI、health 與 audit；共享平均不得掩蓋任一市場／期間 cell 失敗。
- [ ] Full candidate 與 class-prior、logistic、neural price-only、fundamental、macro、text 及逐模態移除 ablations 使用相同 manifests 形成可比較 EvaluationReports。
- [ ] 10 分鐘 CPU 推論邊界、request ordering／batch composition invariance、probability schema、安全 artifact 及 offline load 通過 contract tests；未勝出不改 production assignment。

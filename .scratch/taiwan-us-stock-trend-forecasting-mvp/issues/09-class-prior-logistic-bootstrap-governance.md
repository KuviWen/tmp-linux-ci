# 09 — Class-prior 與 logistic bootstrap 治理路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 以合格台美歷史資料建立 class-prior 與 regularized multinomial logistic 兩個 TrendForecaster adapter，完整走過不可變訓練意圖、防洩漏 walk-forward、校準、評估、BootstrapGatePolicy、人工核准及五次 shadow，並在研究治理介面呈現候選證據；未勝出時不得建立正式服務指派。

**Blocked by:** 08 — 雙市場歷史證據與回填資格路徑

**Trace IDs:** `P2-TRACE-MODEL-01`, `GATE-MODEL-01`

Status: ready-for-agent

- [x] ForecastLab 透過同一 TrendForecaster 契約訓練 class-prior 與 regularized multinomial logistic，二者使用相同 immutable FeatureBatch、label、fold、cost 及 source-policy manifests。
- [x] 每個市場按季度使用全部合格歷史建立明確 training／validation spans、固定 20-session purge、固定 20-session embargo 及一季一次性 test；fold manifest 記錄實際深度與 fold 數，統計／類別／校準支援不足時不得形成正式候選。
- [x] 所有 preprocessing、normalizer、class weights 與 model selection 只以允許的 training／validation 資料擬合，測試季度不影響特徵、停止、校準或模型選擇。
- [x] 每個候選使用三個預先登錄 seeds、六個 market × horizon calibrators、版本化交易成本情境及 immutable ModelArtifact／EvaluationReport，artifact 可離線載入且無 latest lookup。
- [ ] BootstrapGatePolicy 只接受 logistic 相對 class-prior 至少一個 macro-F1 percentage point 改善，並要求所有絕對校準、穩定、涵蓋、重現、安全及營運 hard gates 通過。
- [ ] Model approver 與 TrainingIntent 發起／執行者職責分離；ApprovalDecision 綁定 exact artifact、evaluation、gate policy、理由及 expected assignment，且依既有期限／失效規則處理。
- [ ] 通過 gate 的候選完成兩市場各五次 eligible EOD shadow，shadow 結果不進 production history，研究治理介面可比較候選、baseline、calibration、support 與 gate evidence。
- [x] Logistic 未達改善、calibrator 樣本不足、任何 hard gate 或核准失敗時，正式 serving 保持 blocked 並保存不可變 GateDecision，不以 class-prior 自動冒充 production。
- [x] 首個 production assignment 建立後，BootstrapGatePolicy 永久停用，後續候選不能以 bootstrap 規則繞過 incumbent comparison。

## Implementation notes

- 公共 seams：`TrendForecaster.train/predict`、`ForecastLab.develop`、`ModelLifecycle.execute`、`POST /api/v1/governance/approval-decisions`、`GET /api/v1/research/model-families/{model_family_id}/backtests` 與對應 research UI。
- SQL authority 使用 append-only `model_lifecycle_events`，並與 lifecycle outbox 在同一 transaction 提交；memory／SQL adapters 共用 command replay／conflict 契約。
- 六個 market × horizon calibrators 由各 `TrendForecaster.train` adapter 以自身 validation probabilities 擬合 temperature scaling；每個模型／seed 的 calibrator 內容寫入自己的 safe JSON artifact，離線載入後依 market／horizon 套用。`ForecastLab` 只經注入的 `TrendForecaster` seam 訓練／預測，不再依 model family 組裝 calibrator。
- Class-prior 依 `market × horizon` cell 擬合 empirical prior；logistic 的市場別 median／IQR／winsorization normalizer、每 cell bounded class weights 及六-cell 等權 loss 都只讀 training rows，並封裝於各自離線可載入 artifact。
- 正式候選只接受 Ticket 08 雙市場 claim IDs；共享 contracts 中的 `HistoricalTrainingLineage` 必須把 source-policy、label、fold、dataset／adjustment／mature-label／FeatureSnapshot artifacts，以及該市場 exact feature rows digest 綁到 verifier 解析出的同一 claim chain。`FeatureBatch` 本身亦須通過內容定址重算；任意或改寫 rows 即使沿用 claim IDs 仍 fail closed。Ticket 08 現有 snapshot 未宣告 model feature-row digest，因此不被本票默認升格為正式訓練資料。
- `BootstrapGatePolicyVersion` payload 不可變、內容定址並由專用 object repository 解析；hard-gate ref 必須解析成 checksum 合格、schema 合格且綁定 exact policy／evaluation／measurements 的 `HardGateReportArtifact`，lifecycle 只評估解析出的 measurements，並分別保存 submitted／verified 值。Approval 另驗證 current assignment CAS，從實際核准時間起七日到期，且同一 exact evidence 的有效拒絕不可翻轉；shadow 證據需 checksum、cold-load、schema、機率、source policy、比較、CPU SLA、唯一日期及前一 run 鏈結。
- 工程 tracer 明確標記 `engineering_acceptance`／`engineering_example`，因此 GateDecision 失敗於 `qualification` 與 `hard_gate_evidence`，不嘗試人工核准，shadow 保持 `0 / 5`，serving blocked，且沒有 production assignment/history；`formal_model_qualification=not_claimed`。
- 未勾選 criteria：目前沒有正式來源資格／全套 hard-gate reports，故不能聲稱 hard gates 真正通過、人工核准完成或五次 eligible EOD shadow 完成。相應 policy、REST approval、CAS、期限及 shadow state-machine 行為只有 contract-test evidence，不冒充正式簽核／shadow。
- 驗證：focused／affected tests `32 passed` 及 baseline／ForecastLab `12 passed`；完整非 PostgreSQL suite `397 passed, 1 deselected`，PostgreSQL opt-in `1 passed, 397 deselected`；`mypy .`（108 source files）、`ruff check .`、`ruff format --check .`、wheel build 與 Compose acceptance 均通過。Wheel SHA-256 為 `7616ECBFD3AD6CCA22ACF065BC3612E5F6E0AD7D635EC3E3F6B391F58F4689AC`。
- 部署驗收：`docker compose -p stock-forecasting-ticket-09-review4 -f compose.yaml --profile ticket-09-acceptance run --build --rm ticket-09-acceptance` 輸出 `status=passed`，八個 deployed fail-closed checks 全為 `true`；專用 containers／volumes 已在驗收後移除。

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
- [x] BootstrapGatePolicy 只接受 logistic 相對 class-prior 至少一個 macro-F1 percentage point 改善，並要求所有絕對校準、穩定、涵蓋、重現、安全及營運 hard gates 通過。
- [x] Model approver 與 TrainingIntent 發起／執行者職責分離；ApprovalDecision 綁定 exact artifact、evaluation、gate policy、理由及 expected assignment，且依既有期限／失效規則處理。
- [x] 通過 gate 的候選完成兩市場各五次 eligible EOD shadow，shadow 結果不進 production history，研究治理介面可比較候選、baseline、calibration、support 與 gate evidence。
- [x] Logistic 未達改善、calibrator 樣本不足、任何 hard gate 或核准失敗時，正式 serving 保持 blocked 並保存不可變 GateDecision，不以 class-prior 自動冒充 production。
- [x] 首個 production assignment 建立後，BootstrapGatePolicy 永久停用，後續候選不能以 bootstrap 規則繞過 incumbent comparison。

## Implementation notes

- 公共 seams：`TrendForecaster.train/predict`、`ForecastLab.develop`、`ModelLifecycle.execute`、`POST /api/v1/governance/approval-decisions`、`GET /api/v1/research/model-families/{model_family_id}/backtests` 與對應 research UI。
- SQL authority 使用 append-only `model_lifecycle_events`；candidate、gate、approval 與五次 joint-market shadow 皆以 aggregate version／idempotency precondition 寫入。
- 工程 tracer 明確標記 `engineering_acceptance`，完成 gate、分離核准與五次 shadow，但 serving 維持 blocked、沒有 production assignment 或 production history。未驗證的正式來源基礎產生 immutable `unverified_source_basis` GateDecision；`formal_model_qualification=not_claimed`。
- 驗證：focused ticket tests `39 passed`；完整非 PostgreSQL suite `385 passed, 1 deselected`；PostgreSQL opt-in `1 passed, 385 deselected`；`mypy src tests`、`ruff check .`、`ruff format --check .` 與 wheel build 通過。
- 部署驗收：`docker compose -p stock-forecasting-ticket-09-acceptance -f compose.yaml --profile ticket-09-acceptance run --rm ticket-09-acceptance` 輸出 `status=passed`，九個 deployed checks 全為 `true`；專用 containers／volumes 已在驗收後移除。

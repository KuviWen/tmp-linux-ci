# 09 — Class-prior 與 logistic bootstrap 治理路徑

**What to build:** 以合格台美歷史資料建立 class-prior 與 regularized multinomial logistic 兩個 TrendForecaster adapter，完整走過不可變訓練意圖、防洩漏 walk-forward、校準、評估、BootstrapGatePolicy、人工核准及五次 shadow，並在研究治理介面呈現候選證據；未勝出時不得建立正式服務指派。

**Blocked by:** 08 — 雙市場歷史證據與回填資格路徑

**Trace IDs:** `P2-TRACE-MODEL-01`, `GATE-MODEL-01`

Status: ready-for-agent

- [ ] ForecastLab 透過同一 TrendForecaster 契約訓練 class-prior 與 regularized multinomial logistic，二者使用相同 immutable FeatureBatch、label、fold、cost 及 source-policy manifests。
- [ ] 每個市場按季度建立七年 training、一年 validation／calibration、固定 20-session purge、固定 20-session embargo 及一季一次性 test，正式報告涵蓋最新八個完整季度。
- [ ] 所有 preprocessing、normalizer、class weights 與 model selection 只以允許的 training／validation 資料擬合，測試季度不影響特徵、停止、校準或模型選擇。
- [ ] 每個候選使用三個預先登錄 seeds、六個 market × horizon calibrators、版本化交易成本情境及 immutable ModelArtifact／EvaluationReport，artifact 可離線載入且無 latest lookup。
- [ ] BootstrapGatePolicy 只接受 logistic 相對 class-prior 至少一個 macro-F1 percentage point 改善，並要求所有絕對校準、穩定、涵蓋、重現、安全及營運 hard gates 通過。
- [ ] Model approver 與 TrainingIntent 發起／執行者職責分離；ApprovalDecision 綁定 exact artifact、evaluation、gate policy、理由及 expected assignment，且依既有期限／失效規則處理。
- [ ] 通過 gate 的候選完成兩市場各五次 eligible EOD shadow，shadow 結果不進 production history，研究治理介面可比較候選、baseline、calibration、support 與 gate evidence。
- [ ] Logistic 未達改善、calibrator 樣本不足、任何 hard gate 或核准失敗時，正式 serving 保持 blocked 並保存不可變 GateDecision，不以 class-prior 自動冒充 production。
- [ ] 首個 production assignment 建立後，BootstrapGatePolicy 永久停用，後續候選不能以 bootstrap 規則繞過 incumbent comparison。

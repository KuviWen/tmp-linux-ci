# 09 — Class-prior 與 logistic bootstrap 治理路徑

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 以合格台美歷史資料建立 class-prior 與 regularized multinomial logistic 兩個 TrendForecaster adapter，完整走過不可變訓練意圖、防洩漏 walk-forward、校準、評估、BootstrapGatePolicy、人工核准及五次 shadow，並在研究治理介面呈現候選證據；未勝出時不得建立正式服務指派。

**Blocked by:** 08 — 雙市場歷史證據與回填資格路徑

**Trace IDs:** `P2-TRACE-MODEL-01`, `GATE-MODEL-01`

Status: ready-for-agent

- [x] ForecastLab 透過同一 TrendForecaster 契約訓練 class-prior 與 regularized multinomial logistic，二者使用相同 immutable FeatureBatch、label、fold、cost 及 source-policy manifests。
- [x] 每個市場按季度使用全部合格歷史建立明確 training／validation spans、固定 20-session purge、固定 20-session embargo 及一季一次性 test；fold manifest 記錄實際深度與 fold 數，統計／類別／校準支援不足時不得形成正式候選。
- [x] 所有 preprocessing、normalizer、class weights 與 model selection 只以允許的 training／validation 資料擬合，測試季度不影響特徵、停止、校準或模型選擇。
- [ ] 每個候選使用三個預先登錄 seeds、六個 market × horizon calibrators、版本化交易成本情境及 immutable ModelArtifact／EvaluationReport，artifact 可離線載入且無 latest lookup。
- [ ] BootstrapGatePolicy 只接受 logistic 相對 class-prior 至少一個 macro-F1 percentage point 改善，並要求所有絕對校準、穩定、涵蓋、重現、安全及營運 hard gates 通過。
- [ ] ApprovalDecision 依不可變 `ModelApprovalPolicyVersion` 執行：`separated_duties` 要求 approver 與 TrainingIntent 發起／執行者分離；`owner_operated` 只允許政策指定 owner principal 自行核准並明記 `independent_review=false`。兩者皆綁定 exact artifact、evaluation、gate policy、approval policy、理由及 expected assignment，且依既有期限／失效規則處理，不能覆寫 hard gate。
- [ ] 通過 gate 的候選在五個不同且遞增的 eligible EOD 日期完成兩市場 shadow；日期間可有每日排定停機，停機不算 cycle，重新上線後從 committed checkpoint 補抓且不回填 `first_observed_at`。Shadow 結果不進 production history，研究治理介面可比較候選、baseline、calibration、support、approval-review mode 與 gate evidence。
- [x] Logistic 未達改善、calibrator 樣本不足、任何 hard gate 或核准失敗時，正式 serving 保持 blocked 並保存不可變 GateDecision，不以 class-prior 自動冒充 production。
- [x] 首個 production assignment 建立後，BootstrapGatePolicy 永久停用，後續候選不能以 bootstrap 規則繞過 incumbent comparison。

## Implementation notes

- 公共 seams：`TrendForecaster.train/predict`、`ForecastLab.develop`、`ModelLifecycle.execute`、`POST /api/v1/governance/approval-decisions`、`GET /api/v1/research/model-families/{model_family_id}/backtests` 與對應 research UI。
- SQL authority 使用 append-only `model_lifecycle_events`，並與 lifecycle outbox 在同一 transaction 提交；`stock` runtime role 只有 `SELECT/INSERT`，明確撤銷 `UPDATE/DELETE`。Memory／SQL adapters 以 canonical JSON 共用 command replay／conflict 契約，tuple 寫入 JSON array 後仍可正確 idempotent replay。
- 六個 market × horizon calibrators 由各 `TrendForecaster.train` adapter 以自身 validation probabilities 擬合 temperature scaling；每個模型／seed 的 calibrator 內容寫入自己的 safe JSON artifact，離線載入後依 market／horizon 套用。`ForecastLab` 只經注入的 `TrendForecaster` seam 訓練／預測，不再依 model family 組裝 calibrator。
- Class-prior 依 `market × horizon` cell 擬合 empirical prior；logistic 的市場別 median／IQR／winsorization normalizer、每 cell bounded class weights 及六-cell 等權 loss 都只讀 training rows。Cell loss 以實際 bounded sample-weight 總和正規化；兩個 adapters 的 offline artifact loaders 都驗證版本、exact schema、manifest shape、finite probabilities／weights、normalizer 與 calibrator binding，逐一重算 calibrator content ID，並要求 calibrator cells 與模型 cells 完全一致；未知 schema、stale ID、缺漏或孤立 cell 都 fail closed。
- 正式候選只接受 Ticket 08 雙市場 claim IDs；共享 contracts 中的 `HistoricalTrainingLineage` 必須把 source-policy、label、fold、dataset／adjustment／mature-label／FeatureSnapshot artifacts，以及該市場 exact feature rows digest 綁到 verifier 解析出的同一 claim chain。`ForecastLab` 先建立 actual walk-forward fold，再以該 fold 更新 lineage、重算 final `FeatureBatch` content ID，最後才驗證並訓練；任意或改寫 rows 即使沿用 claim IDs 仍 fail closed。Ticket 08 現有 snapshot 未宣告 model feature-row digest，因此不被本票默認升格為正式訓練資料。
- `BootstrapGatePolicyVersion` payload 不可變、內容定址並由專用 object repository 以 exact schema 解析；未知／缺漏 category、comparison、limit、重複 metric 或非完整 gate categories 都視為 policy unavailable。Hard-gate ref 必須解析成 checksum 合格、schema 合格且綁定 exact policy／evaluation／measurements 的 `HardGateReportArtifact`，lifecycle 只評估解析出的 measurements，並分別保存 submitted／verified 值。Approval 另驗證 current assignment CAS，從實際核准時間起七日到期，且同一 exact evidence 的有效拒絕不可翻轉；shadow 證據需 checksum、cold-load、schema、機率、source policy、比較、CPU SLA、唯一日期及前一 run 鏈結。
- `EvaluationReport` 是 checksum 驗證、exact-schema 且由 object repository 解析的 immutable artifact；`RecordCandidate` 只接受完整 report，不再接受獨立的 report ID／分數／改善幅度，gate 從解析出的 class-prior／logistic equal-cell macro-F1 驗證至少一個 percentage point。Model artifacts 的 calibrator bundle 欄位必填；未提供 validation rows 時，每個模型 cell 會寫入內容定址的 `insufficient_data` identity evidence，缺失、空缺 formal cells 或竄改仍 fail closed。
- 正式候選另經 `FormalCostScenarioVerifier` 驗證 cost manifest；預設 verifier 不可用且 fail closed。Repo 尚無符合 ADR 0018（費用、稅、spread、slippage、turnover 與生效日）的正式非零成本情境 artifact／adapter，因此 AC 4 保持未勾選，不能把任意字串或工程用零成本標籤當作正式證據。
- `ModelApprovalPolicyVersion` 內容定址並在每筆 Decision 綁定 mode 與指定 owner。Lifecycle 預設維持 `separated_duties`；實際單一 local-runtime identity 使用 `owner_operated`，且只允許政策 owner 對其本人發起或執行訓練的候選作成決定；指定 principal 以外或 owner 未參與訓練時 fail closed。缺少不可變 approval-policy binding 的 legacy Decision 仍可誠實查詢，但不能開始 shadow。REST、研究 read model 與 UI 在核准及拒絕狀態都公開 `approval_policy_version_id`、mode、owner principal 及是否有獨立審查，不提供 caller 可變的 `allow_self_approval` 旗標。
- 五次 shadow 仍必須是不同且遞增的 eligible EOD 日期，不是把同一天重播五次。單機只需完成一個最長 24 小時的每日運作窗；窗間可排定停機。重新上線後以既有 checkpoint 補抓停機期間已公開資料，並保留實際 post-restart `first_observed_at`，不讓 late data 進入停機前 cutoff。
- Shadow evidence 內容定址並綁定 exact candidate、artifact、evaluation、approval decision、approval policy 與 expected assignment；cycle 同時通過 `ShadowEligibilityVerifier` 的雙市場共同合格 EOD 與獨立 `ShadowRunVerifier`，任一預設不可用或 provider 例外都 fail closed。CPU latency 必須 finite、非負且不超過 600 秒；contract evidence 使用真實遞增工作日，不再把週末自我宣告成合格交易日。
- AC 5–7 的本機準備採用 `ticket-09-operator` Compose profile：loopback-only API、獨立持久化 PostgreSQL／object／encrypted source-secret volumes、30 日 owner 與 source-adapter identities，以及只透過 hidden prompt 呼叫既有 write-only REST 的 operator CLI。Pending-rights policy 允許 owner 管理 credential／model governance metadata，但不授予 source-adapter 任何 live dataset policy；因此 credential 可安全保存為 `configured`，書面 rights 尚未核定時 validation 必須在 provider network 前回傳 HTTP 403 `authorization_denied`。
- Docker Desktop 實機 smoke 使用純 synthetic canaries 驗證 profile isolation、加密寫入、log 無 canary、停機／復機後 credential metadata 與同一 owner principal 持久化；測試 project 與全部 volumes 已刪除。這只是 runtime prerequisite evidence，不是正式來源資格、hard-gate、核准或 shadow 證據，故 AC 5–7 仍保持未勾選。逐步操作與外部回覆證據格式記錄於 `docs/operations/ticket-09-ac5-7-runbook.md`。
- 工程 tracer 明確標記 `engineering_acceptance`／`engineering_example`，因此 GateDecision 失敗於 `qualification` 與 `hard_gate_evidence`，不嘗試人工核准，shadow 保持 `0 / 5`，serving blocked，且沒有 production assignment/history；`formal_model_qualification=not_claimed`。
- 未勾選 criteria：目前沒有已驗證的正式交易成本情境、正式來源資格或全套 hard-gate reports，故不能聲稱正式候選 artifact 完整、hard gates 真正通過、實際 owner 核准完成或五個不同 eligible EOD 日期的 shadow 完成。相應 policy、REST approval、CAS、期限、停機補抓語意及 shadow state-machine 行為只有 contract-test／既有 checkpoint evidence，不冒充正式簽核／shadow。
- 驗證：完整非 PostgreSQL suite `444 passed, 1 deselected`，PostgreSQL opt-in `1 passed, 444 deselected`；`mypy src tests`（103 source files）、`ruff check .`、`ruff format --check .`（219 files）、wheel build、Compose config 與 Ticket 09 Compose acceptance 均通過。Wheel SHA-256 為 `04C6B05B5F0ECF6F3CBDCB0FC829FB456C621CD9A6925B201BF634B194EC7A29`。
- 部署驗收：`docker compose -p stock-forecasting-ticket-09-review3 -f compose.yaml --profile ticket-09-acceptance run --build --rm ticket-09-acceptance` 輸出 `status=passed`，九個 deployed fail-closed checks 全為 `true`，包括使用實際 `stock` role 的 `lifecycle_ledger_append_only=true`；專用 containers／network／volumes 與 PostgreSQL integration-test resources 已在驗收後移除。

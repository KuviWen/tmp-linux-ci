# 設計模型登錄、升版與復現流程

Type: grilling
Status: resolved
Blocked by: 03, 05, 07, 08

## Question

資料清單、來源快照、特徵綱要、程式版本、環境、實驗、模型 artifact、校準器、回測、漂移報告與人工核准如何形成不可變譜系；週增量、月重訓、季調參及提前候選流程如何共享同一狀態機並安全回退？

## Comments

### Round 1 frontier

1. 哪裡是模型生命週期的權威紀錄？建議由應用擁有的 PostgreSQL `ml` schema 保存 append-only lifecycle events、核准與 production assignment，物件儲存保存內容定址成品／報告；MLflow 只作 experiment tracking／registry projection，不直接讀寫其內部表，也不能用 alias／stage 取代權威核准。
2. 哪些不可變身分必須分開？建議使用穩定 ModelFamily，以及不可變 TrainingIntent、TrainingRun attempt、ModelArtifact、EvaluationReport、GateDecision、ApprovalDecision、PromotionEvent；ServingAssignment 是目前投影而非 artifact 狀態。每一層使用內部 UUID、內容雜湊及單向引用。
3. 狀態機採可變 stage 還是 append-only event projection？建議事件序列投影 `planned -> running -> trained -> evaluated -> gate_passed／gate_failed -> awaiting_approval -> approved／rejected -> promoted`，另有 `failed／superseded／retired`；任何更正新增事件，不改寫歷史。Rollback 只建立新 assignment 指回先前 approved artifact。
4. 哪些 trigger 共用同一流程？建議 `scheduled_incremental`（每週）、`scheduled_full`（每月）、`scheduled_hpo`（每季）、`drift_early` 與 `manual_rebuild` 全部建立 TrainingIntent 並走相同評估／人工核准狀態機，絕不因緊急或排程而自動升版。排程碰撞時季度取代月度、月度取代週度，避免同一資料截止點重複候選。
5. TrainingIntent 何時才算完整？建議啟動前固定 source／dataset／FeatureSnapshot／label／fold manifests、股票池、日曆、調整、處理組合、來源政策、model／HPO config、seeds、程式 SHA、套件鎖定、交易成本情境、parent artifact（若 warm-start）及 trigger evidence；canonical manifest hash 同時作 idempotency key，任何 `latest` 或未通過涵蓋／授權者都不得啟動。
6. 正式推論可以有幾個 current？建議每個 ModelFamily 只有一個原子 `production` ServingAssignment，另允許一個不影響正式結果的 `shadow` assignment；共享台美 ModelArtifact 同時涵蓋兩市場與三 horizons，MVP 不混搭不同 market／horizon artifact。切換使用 compare-and-swap，舊 assignment 永久保留事件證據。

### Round 1 decision

使用者確認六項全部採建議。應用 PostgreSQL 事件帳本與內容定址物件是權威來源，MLflow 只作 projection；生命週期身分與 ServingAssignment 分離；所有狀態由 append-only events 投影；五類 trigger 共用同一流程；TrainingIntent canonical manifest 在啟動前完全固定；每個 ModelFamily 僅有一個原子 production assignment 與一個 optional shadow assignment。

### Round 2 frontier

7. 五類 trigger 的訓練行為各是什麼？建議週增量 warm-start 現行模型、固定 architecture／feature schema／normalizer，對完整 rolling 7-year training window 最多 5 epochs，並重新擬合當期六個 calibrators；月重訓以相同已核准 config 從頭重建 normalizer 與三 seeds、最多 50 epochs；季調參跑最多 30 trials 後用選定 config 從頭訓練三 seeds；drift_early 使用現行 config 從頭重訓，不臨時擴大 search space；manual_rebuild 必須帶原因。任何可升版候選仍跑相同八季回測與閘門。
8. HPO trial 是否本身就是候選模型？建議不是。Trials 是單一 scheduled_hpo TrainingIntent 下的 child TrainingRuns，只保存參數、指標、資源與 trial artifact；只依 validation 規則選出的 config 能建立新的 TrainingIntent，從頭以三 seeds 重訓、校準及回測後才產生可升版 ModelArtifact。Trial checkpoint 永遠不可直接 promoted。
9. 排程到點但資料尚未就緒時怎麼辦？建議週訓練在每週第一個共同 readiness window、月重訓在月末後第一個 readiness window、季調參在季末後第一個 readiness window建立 intent。Barrier 要求兩市場所需 CoverageReport、成熟標籤、FeatureDataset、來源政策與交易日曆完整；最多等待 24 小時，逾時轉 `blocked` 並告警，不以舊快照或部分資料偷偷訓練。兩市場可保存不同 latest-mature session，但都寫入 manifest。
10. 升版採加權總分還是所有閘門 veto？建議 eligibility／reproducibility、統計、校準、經濟、穩定性／公平涵蓋、效能／安全及人工核准都是 hard gates；任一失敗即不可 promoted，不允許經濟報酬抵銷資料洩漏、平均分抵銷單一市場崩壞或漂亮 accuracy 抵銷失準機率。統計閘門同時要求勝過 baselines 並對現行模型達非劣／改善條件，下一輪再固定數值。
11. 重試、排程碰撞與併發如何保持冪等？建議 canonical manifest hash 加 ModelFamily／cutoff／trigger priority 形成 idempotency key；同一 intent 同時只允許一個 active TrainingRun attempt，暫時性失敗最多新增三個 attempts，絕不覆寫前次。輸入或 config 改變必須新建 intent；季度／月度／週度碰撞依優先級 coalesce，取消與 supersede 都寫事件。Production compare-and-swap 防止兩個核准流程互相覆蓋。
12. MLflow outage 是否阻止權威流程？建議 canonical ledger、物件成品、評估及人工核准可繼續；ExperimentTracker／ModelRegistry adapter 以 transactional outbox 標記 `projection_pending`，恢復後冪等補投並做 checksum／run-link reconciliation。MLflow 不可用會告警但不改變權威狀態，也不成為繞過或阻擋 canonical promotion 的隱性單點。

### Round 2 decision

使用者確認六項全部採建議。五類 trigger 固定各自的 warm-start／from-scratch／HPO 行為但共享八季評估；trial checkpoint 不可升版；排程受 24 小時 readiness barrier 保護；所有閘門都是 veto；intent／attempt／assignment 使用 manifest idempotency 與 compare-and-swap；MLflow outage 只造成可補投 projection，不改變 canonical lifecycle。

### Round 3 frontier

13. Eligibility／reproducibility gate 的數字如何定？建議 required manifests、CoverageReport、來源政策資格、checksum、schema、20-session 防洩漏契約與契約測試 100% 通過，三 seeds 及全部 baselines／ablations 完成；同一核准 runtime 重播時 sample membership／labels／metrics 必須一致，CPU 同環境逐筆機率最大差 `<=1e-6`，核准的 mixed-precision GPU 路徑容差 `<=1e-4`。任何缺件、`latest`、retrospective contamination 或 policy block 直接否決。
14. Statistical gate 的非劣與改善門檻如何定？建議在相同最新 8 季測試摺上，以 session 為 cluster、20-session block、2,000 次 paired bootstrap：candidate 的六 cell 等權 macro-F1 至少比最佳 prior／logistic baseline 高 1.0 percentage point；相對現行模型的 95% CI lower bound 必須大於 -0.5 point，且任一 market × horizon cell 的 point decline 不得超過 1.5 points。沒有現行模型時只使用 baseline 與其餘 absolute gates。
15. Calibration gate 如何定？建議六 cell 等權 ECE `<=0.05`，每個 full-support cell `<=0.08`、degraded-support cell `<=0.10`；六個 calibrators 都必須 `sufficient_data`。Equal-cell NLL 與 multiclass Brier 各自不得比現行模型惡化超過 1%；沒有現行模型時不得比最佳校準 baseline 差。Identity fallback 可供研究，但不能 promoted。
16. Economic gate 如何定？建議成本後 rank IC 在台灣及美國 aggregate 都大於 0，六 cells 至少四個大於 0，equal-cell IC information ratio `>=0.30`；top-20% long 相對市場基準的成本後 excess return 在兩市場 aggregate 都非負且至少四 cells 非負。最大回撤相對現行模型的惡化不得超過 `max(2 percentage points, incumbent drawdown 的 10%)`；換手增加超過 25% 時必須仍有更高成本後報酬。美股 market-neutral 保留 diagnostic，不作 hard gate。
17. Stability／coverage gate 如何定？建議 8 季中至少 6 季的 equal-cell macro-F1 delta `>=-0.5 point`，不得連續三季落後；三 seeds 的 equal-cell macro-F1 標準差 `<=1.0 point`，worst seed 仍須勝過最佳 baseline。對樣本數 `>=500` 的市場／horizon／產業／規模／流動性主要切片，任一 macro-F1 decline 不得超過 2 points；degraded coverage 不得比現行模型減少超過 5 points，且其 macro-F1 不得下降超過 2 points。
18. Operational／safety gate 如何定？建議 trainable parameters `<=15M`、核准 MVP 股票池 CPU EOD predict＋attribution `<=10 分鐘`、完整每日管線 `<=2 小時`；ModelArtifact 必須離線載入、不連網、不含不安全 callable／未授權原文，checksum／runtime／source-policy 驗證通過，ForecastBatch schema 與機率不變量 100% 通過。任何 critical security finding、artifact corruption 或不可重現載入直接否決。
19. 通過所有非劣 hard gates 就一定值得換版嗎？建議還須至少一項 material improvement：equal-cell macro-F1 `+0.5 point`、NLL 或 Brier 相對改善 `>=2%`、equal-cell rank IC `+0.005`、成本後 annualized excess return `+1.0 percentage point`，或在其他結果非劣時 CPU latency／artifact size 改善 `>=20%`。首個現行模型免除此條，但仍須勝過 baselines 並通過全部 absolute gates。所有數值屬不可變 GatePolicyVersion，變更只影響新決策。

### Round 3 decision

使用者確認七項全部採建議。資格／復現、統計、校準、經濟、穩定性／涵蓋及營運／安全均有不可互相抵銷的 hard thresholds；候選另須至少達成一項 material improvement，首個現行模型僅免除相對現行模型的改善條件。每次 GateDecision 綁定不可變 GatePolicyVersion，政策變更不重算舊決策。

### Round 4 frontier

20. 人工核准需要哪些約束？建議核准者必須具 `model_approver` 角色且不能是該 TrainingIntent 的發起者或執行者；ApprovalDecision 明確綁定 artifact checksum、EvaluationReport、GatePolicyVersion、預期 ModelFamily 與 expected current assignment。核准不得空白理由，7 個曆日未升版即失效；其間若 artifact、gate report、來源政策資格或 critical security 狀態改變也立即失效。拒絕不可翻轉，同一 artifact 需重新評估並建立新核准請求。
21. 升版前需要 shadow／canary 嗎？建議 hard gates 通過後先建立 shadow assignment，連續 5 個市場共同 EOD runs 完成載入、FeatureBatch compatibility、10 分鐘 SLA、ForecastBatch 不變量及與現行流程的差異報告；shadow 預測不可成為使用者正式結果。研究系統首版不做按使用者 canary，以免同一資訊截止點顯示不同現行模型；核准後在下一個未開始的日終批次邊界原子切換 production assignment。
22. 哪些情況自動回退，哪些只告警待人？建議每個 production assignment 預先綁定一個仍符合目前 schema／來源政策的 approved rollback target。Artifact checksum／載入失敗、整批 schema incompatibility、無效機率、不可用率較 shadow baseline 增加超過 5 percentage points，或 CPU SLA 連續兩次違反時自動 compare-and-swap 回退；資料／預測漂移與成熟標籤品質下降因延遲與雜訊只告警並允許人工回退。回退不刪除或重算任何既有預測。
23. 何時建立 `drift_early` TrainingIntent？建議三類訊號任一連續兩個週檢查窗成立：至少 10% critical features 的 PSI `>=0.25`，或 OOD／degraded rate 相對訓練基線增加 `>=10 percentage points`；有 60-session 成熟標籤時 equal-cell macro-F1 下降 `>=3 points` 且 bootstrap CI 排除 0，或 ECE `>0.10`，或 aggregate rank IC `<=0`。來源事故／policy block 先隔離資料而非用重訓掩蓋。Trigger 只建立候選，不自動升版。
24. 來源授權撤回、刪除或安全事件如何影響模型？建議依 lineage 建立 PolicyImpactAssessment；受影響 artifact 轉 `policy_quarantined`，禁止新訓練、核准及 assignment。若現行模型受影響，立即停用並原子切到仍 eligible 的 approved target；沒有 target 則停止正式預測而非使用未核准模型。需實體刪除的物件依政策刪除，canonical ledger 保留不可逆 tombstone、checksum、影響範圍與 deletion certificate。
25. 各類成品保存多久？建議 promoted／approved／rejected ModelArtifacts、evaluation／gate／approval／assignment／prediction lineage 至少保存 7 年；所有 trial 與 failed-run 的 manifest、params、metrics、logs、checksum 也保存 7 年，但未入選 trial checkpoint 只保存 90 日、未完成暫存物 30 日後清理。現行模型與 rollback target 永不在任職期間到期，退役後再計 7 年；若來源政策要求更短或刪除，以政策處置與 tombstone 優先。
26. 若沒有候選通過或現行模型逐漸過時怎麼辦？建議候選失敗時維持現行 assignment 並保存 GateDecision，不因排程到了就換版；現行 artifact 超過 45 日沒有新的成功 monthly-full 評估，或最近一次完整 gate report 超過 90 日，標記 `stale` 並告警。Stale 不自動改變預測機率，但研究介面必須顯示；若超過 120 日、來源政策失效或目前 FeatureSnapshot schema 不再相容，停止正式預測直到合格模型升版。

### Round 4 decision

使用者確認七項全部採建議。核准具角色分離、內容綁定與七日效期；升版前需五次 EOD shadow 並在批次邊界原子切換；只有可立即驗證的 serving failures 自動回退，品質漂移由人處理；漂移採連續週窗與 60-session 成熟標籤觸發候選；授權撤回可 quarantine／刪除並 fail closed；canonical 譜系保存七年、非入選 checkpoints 短期保存；現行模型具 45／90／120 日過時階梯。

### Round 5 frontier

27. Promotion transaction 如何避免「資料庫說已升版、serving 卻載不到」？建議先把 artifact staged 到 serving 可讀位置，驗證 checksum、runtime、FeatureSchema、來源政策及 cold-load smoke；再於單一 PostgreSQL transaction 鎖定 ModelFamily、驗證 approval／shadow／expected current assignment，append PromotionEvent、建立新 ServingAssignment 並寫 outbox。Serving 每個 EOD batch 開始時解析一次 assignment 並 pin 到整批結束；transaction commit 後 projection／cache 可重試，不能以可變 symlink 或 MLflow alias 決定正式模型。
28. 升版或回退後要不要重算舊預測？建議永不改寫。新 assignment 只從下一個未開始的 EOD batch 生效；既有 production 與 shadow PredictionRecords 永久引用原 artifact／assignment。研究需要的歷史重算另建 `retrospective_replay` run／dataset，明確標記非當時 production，不能混入正式績效或取代舊結果。
29. Rollback target 如何保持真的可用？建議每次 promotion 前重新驗證前一個 production 或指定 target 的 checksum、runtime、current FeatureSchema／source-policy eligibility 及 cold-load，成功才記為 rollback target；之後每日輕量驗證。除首次部署外，沒有合格 target 不得 promotion；首次部署失敗則停止正式預測。Target 失格立即移除資格並告警，不等事故發生才發現。
30. 是否允許 break-glass 把未通過候選直接升版？建議永遠不允許。Break-glass 只能由雙人核准執行 `stop_serving`、切回既有 eligible／approved artifact 或撤銷 assignment；不能略過 gate、shadow、來源政策或安全檢查，也不能修改 PredictionRecords。每次緊急操作需事件、理由、incident ID、操作者與事後檢討。
31. 如何證明從 manifest 可真正復現？建議 gate 前在隔離、無網路、以 container image digest／套件鎖定建立的核准 runtime 執行 `ReproductionRun`：重新解析 manifests、驗證所有 inputs／artifact checksums、重建 sample membership、標籤、normalizer、primary-seed artifact 與評估，產生差異報告並套用 Q13 容差。失敗即 veto；成功報告與 runtime／硬體 profile 保存七年。
32. Canonical ledger、MLflow projection 或 serving cache 不一致時以誰為準？建議 ledger＋內容定址物件永遠勝出。週期 reconciler 驗證 event sequence、assignment、artifact checksum、MLflow run／model link 與 serving cache；缺失 projection 由 outbox 補建，多餘／錯誤 projection 標記 orphan 且不影響 production。Serving cache 若無法證明等於 pinned assignment 就 fail closed／使用已驗證 rollback target，不能自行選「最新」。

### Round 5 decision

使用者確認六項全部採建議。Promotion 使用 staged cold-load 與單一 PostgreSQL transaction 建立事件、assignment 與 outbox，EOD batch pin 固定 assignment；升版／回退永不重算歷史 prediction；rollback target 在升版前及每日驗證；break-glass 永不允許未通過候選升版；gate 前必須完成隔離無網路 ReproductionRun；任何投影／cache 衝突皆以 canonical ledger 與內容定址物件為準並 fail closed。

### Shared-understanding checkpoint

五輪共 32 項決策已回答，設計樹 frontier 為空。使用者已明確確認共有理解成立。

## Answer

採應用 PostgreSQL append-only ledger 與內容定址物件作權威生命週期；MLflow、registry 與 serving cache 只作可補投 projection。週增量、月重訓、季調參、漂移提前候選及人工重建共用同一 readiness／訓練／八季回測／hard-gate／人工核准／shadow／原子 assignment 狀態機；trial checkpoint、未通過候選及 break-glass 都不能繞過升版。

GatePolicyVersion 固定資格／復現、統計、校準、經濟、穩定性／涵蓋、營運／安全及 material-improvement 數值；核准採職責分離與七日效期。Serving 每批 pin 單一 assignment，升版與回退不改寫舊預測；可立即驗證的 serving failures 才自動回退，漂移只觸發候選。Artifact 需在隔離無網路環境真正重建，授權撤回可 quarantine／刪除並 fail closed，canonical 譜系保存七年。

- Design: [`docs/design/model-lifecycle-and-promotion.md`](../../../docs/design/model-lifecycle-and-promotion.md)
- ADR: [`docs/adr/0009-canonical-event-ledger-for-model-lifecycle.md`](../../../docs/adr/0009-canonical-event-ledger-for-model-lifecycle.md)

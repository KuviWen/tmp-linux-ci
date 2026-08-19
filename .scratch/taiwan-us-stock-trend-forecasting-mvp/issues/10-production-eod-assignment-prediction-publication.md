# 10 — 正式 EOD 服務指派與預測發布

**Zero-cost boundary:** 遵循主 spec `COST-0-01` 與 ADR 0018；允許用途資格合格的零付費 authenticated provider 及程式管理的來源憑證，禁止付費／採購／sales approval／協商契約；憑證未就緒是可觀察狀態，不是 ticket 交付 blocker。

**What to build:** 讓一個經 bootstrap 治理核准的 logistic ModelArtifact 透過原子 production 服務指派進入正式日終 workflow；每個市場在 T+90 固定資料選擇、FeatureSnapshot 與 assignment，於 T+120 前交易發布 10＋10 每掛牌三期間結果或機器原因，並在研究、營運與 audit 中完整可追溯。

**Blocked by:** 09 — Class-prior 與 logistic bootstrap 治理路徑

**Trace IDs:** `P2-TRACE-EOD-01`, `GATE-OPS-01`, `GATE-UX-01`

Status: ready-for-agent

- [x] Promotion 先 staged cold-load 並驗 checksum、runtime、FeatureSchema、來源政策與 rollback target，再以單一交易建立 PromotionEvent、ServingAssignment 與 outbox。
- [x] 每市場正式 EOD 在資訊截止點解析一次 DataSelection 與 production assignment，整批 pin 相同版本，不在掛牌間重新查詢 current／latest。
- [x] FeatureSnapshot 只包含 cutoff 前 `platform_observed` 且 feature-freeze 前處理完成的版本；late 內容只能進下一次正式批次或明示 retrospective replay。
- [x] 每個 10＋10 掛牌與 1／5／20 期間交易發布一筆具名機率、信心、支援、主要版本引用的結果，或無機率且具穩定原因的 unavailable 結果。
- [x] 單一掛牌或市場的部分失敗不會使成功結果消失；正式批次只有在完整 result-or-reason、schema、probability、checksum、lineage 及核心 projection 驗證成立時完成。
- [x] REST／繁中矩陣與標的頁顯示 formal cutoff、prediction history、ModelArtifact、FeatureSnapshot、dataset、assignment、calibration、support 及政策允許的證據，且不混入 shadow／fixture／replay。
- [x] OperationsControl 保存 T+90 readiness、T+105 feature、T+115 forecast validation、T+120 breach、source health、incident、notification delivery 與 audit，遲到正式批次仍計 SLO breach。
- [x] Promotion／rollback 不重算或覆寫既有 PredictionRecord；下一個未開始批次才使用新 assignment，歷史查詢保持原 artifact 與 assignment。
- [x] 端到端測試涵蓋 assignment compare-and-swap race、rollback-target 失格、late data、schema mismatch、invalid probability、公開來源條款撤回／更換與 projection lag。

## Implementation notes

- Public seams: `ModelLifecycle.execute(...)` for promotion／rollback, `ForecastExecution.run(...)` for one market EOD batch, and `ResearchQuery`／REST／繁中 UI／`OperationsControl` for publication and operations evidence.
- Production publication authorizes the catalog before replay／selection access, re-evaluates the selected manifest after resolution, and re-evaluates current scoped action／source-use rights at the persistence boundary; denied commit-time decisions roll back prediction, research, outbox, and operations writes. Replay, research display, operations reads, and notifications also re-evaluate their scoped rights.
- Readiness is observed after data selection／assignment pin. T+120 completion and SLO state are finalized inside the publication transaction, and production selection rejects empty immutable lineage identifiers or unavailable reasons outside the published contract enum.
- Deterministic positive scenarios are isolated engineering acceptance only. The deployed application has no default formal `ProductionDataSelection` provider and fails closed; `formal_model_qualification=not_claimed` remains unchanged.
- Verified with focused acceptance／contract tests, all acceptance＋contract tests (535 passed), full repo tests without the opt-in PostgreSQL URL (535 passed, 1 PostgreSQL skip), strict mypy (109 files), Ruff lint／format (109 Python files), Alembic `20260819_09 (head)`, and wheel SHA-256 `9840a2c5398cd7f8a07db9e73b01ab9088d0d0d0a0f960607c2d73a0988e73f0`.
- Docker CLI is unavailable on this host. The externally configured PostgreSQL URL timed out, so Compose smoke and real-PostgreSQL integration could not be verified; no passing operational or formal-model evidence is claimed for them.

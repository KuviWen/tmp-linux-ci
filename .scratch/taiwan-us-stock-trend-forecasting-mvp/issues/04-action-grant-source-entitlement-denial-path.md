# 04 — 行動權限與來源使用資格交集拒絕路徑

**What to build:** 使用相同本機 SecurityContext 與行動權限，分別搭配有效及無效來源使用資格，讓 fixture 擷取、日終 workflow、預測發布、研究 projection、REST／UI 與安全稽核都由同一 AuthorizationPolicy 得到一致 allow／deny 結果；任何單一工具或管理角色都不能繞過來源政策。

**Blocked by:** 01 — 台股 fixture 完整日終研究路徑

**Trace IDs:** `P1-TRACE-AUTH-01`

Status: ready-for-agent

- [x] Loopback local／development API key 只能建立具 owner、expiry、scope 與 environment 的可信 SecurityContext；非 loopback 或正式設定會啟動失敗。
- [x] 兩個 fixture 來源具版本化來源政策、資料保護類別與來源使用資格，且 allow 決策同時要求有效身分、行動權限、來源使用資格、用途、環境與資源狀態。
- [x] 相同行動權限配合 active entitlement 時，擷取、FeatureSnapshot、fixture 預測、projection 與授權允許的研究查詢能完整成功。
- [x] 將 entitlement 改為 suspended／expired／revoked 或移除指定用途後，新的擷取、推論、projection 及展示均在一致位置 fail closed，且不回傳受限 payload。
- [x] REST 對 caller 只回穩定 problem code 與 correlation ID；受控 audit 保存真實 decision、reason、policy／grant／entitlement version 與 trace。
- [ ] Dagster、CLI、REST handler、資料庫角色或 platform-admin 身分都不能產生與 AuthorizationPolicy 相反的 allow 結果。
- [x] 端到端 decision-matrix 測試同時涵蓋有權限無資格、有資格無權限、未知政策、到期資格與正常 allow，並驗證 workflow、REST、projection 與 audit 一致。

## Implementation notes

- 公共 seam：`stock-forecasting acceptance ticket-04`；深層 seam 為
  `LocalApiKeyVerifier.authenticate(...) -> SecurityContext` 與
  `AuthorizationPolicy.evaluate(...) -> AuthorizationDecision`。Workflow、ResearchQuery、
  REST／UI、Dagster 與 CLI 都使用同一 policy，受控 audit 保存完整 decision evidence。
- Compose 使用不進 Git 的 named-volume ephemeral key file；active API 與 revoked-entitlement
  API／Dagster code location 使用同一身分與 action grant；金鑰在 clean startup 時建立 24
  小時效期，不依賴固定日期。部署驗證步驟記錄於
  `docs/operations/ticket-04-authorization.md`。
- Policy 拒絕以穩定 `policy_denied` outcome 回傳；每次評估有獨立 `evaluation_id`，同一
  semantic decision 可保留共同 `decision_id`，且完整 credential、dataset、時間與版本證據
  逐次 append，不因重試去重。
- 授權 policy set 由 PostgreSQL 中的 immutable repository 提供，runtime 僅選擇明確版本；
  allow audit 與研究／預測／outbox publication 在同一資料庫交易提交，deny audit 則獨立保存。
- Compose 將 migration 與 policy bootstrap 留給 `postgres` 管理角色；執行中服務使用
  non-superuser `stock`，且對 `authorization_policy_sets` 僅有讀取權。部署 acceptance 會查詢
  PostgreSQL 的實際 role privilege。
- 驗證：`python -m pytest -q`（108 passed、1 個需外部 PostgreSQL 的 integration test
  skipped）、`python -m mypy src tests`、`python -m ruff check .`、
  `python -m ruff format --check .`、ticket-04 acceptance runner，以及 Alembic upgrade 均通過。
- 本機沒有 Docker executable，因此本次未執行 Compose clean-container／PostgreSQL acceptance；
  Compose 契約測試已通過，但不得將其描述為實際容器執行證據。上方包含資料庫角色的
  acceptance criterion 因缺少這項外部部署證據而維持未勾選。

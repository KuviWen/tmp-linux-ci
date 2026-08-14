# 05 — 發布 P1 雙市場工程脊柱 acceptance bundle

**What to build:** 從乾淨環境以單一 Compose 命令與單一 acceptance runner 展示台灣及美國 fixture EOD、繁中 REST／UI、outbox crash 恢復、授權交集拒絕、失敗矩陣及重啟，最後發布內容定址、不可變且明示「只證明工程脊柱」的 P1 acceptance bundle。

**Blocked by:** 02 — 美股 fixture 共用契約研究路徑, 03 — Outbox crash 後完整恢復預測投影, 04 — 行動權限與來源使用資格交集拒絕路徑

**Trace IDs:** `P1-EXIT-01`, `GATE-POLICY-01`, `GATE-PIT-01`, `GATE-DATA-01`, `GATE-MODEL-01`, `GATE-SEC-01`, `GATE-OPS-01`, `GATE-DEPLOY-01`, `GATE-UX-01`

Status: ready-for-agent

- [x] Acceptance runner 從無既有 state 的環境啟動 Compose，完成兩市場 fixture EOD、REST／UI 查詢、事故注入、重啟及結果驗證，不依賴手動步驟。
- [x] 驗收涵蓋 duplicate collection、late data、必要／可選模態缺失、日曆／公司行動缺件、checksum、stale fencing、單市場失敗、fixture 升版企圖與 outbox 重送。
- [ ] Windows Docker Desktop 與 Linux CI 使用相同 container 路徑及命令通過 PostgreSQL、filesystem ObjectRepository、Dagster wrapper、REST／event contract 與端到端驗收。
- [x] 繁中比較矩陣／標的頁、URL 重載、鍵盤操作、文字狀態、三期間機率、信心、支援、cutoff、fixture 標章與譜系全部可由外部觀察驗證。
- [x] Bundle 綁定 trace IDs、Git／image／deployment／migration／fixture digests、來源政策、manifests、contracts、E2E IDs、failure evidence、UI／REST goldens、restart 與 resource smoke。
- [x] Bundle 記錄每個未通過、degraded、policy-blocked 或例外的穩定原因、owner、前一 bundle reference 及完整重現命令。
- [x] Bundle 明示 fixture 模型不可 promotion、fixture 結果不可成為 production PredictionRecord，且 P1 不宣稱來源授權、預測力或正式容量。
- [x] 只有全部 P1 hard gates 通過時才發布 passing bundle；失敗重新執行會建立新的 failed／blocked 證據而不修改既有 bundle。

## Implementation notes

- 公共 seam：`docker compose --profile acceptance run --build --rm acceptance`，由
  `stock-forecasting acceptance ticket-05` 聚合既有 ticket 02／03／04 公共 runners，再驗證
  checksum、stale fencing、optional-modality phase boundary 與 fixture promotion denial。
- Bundle seam：`P1AcceptanceBundlePublisher.publish(evaluation)` 使用真實 filesystem
  ObjectRepository 發布 canonical、content-addressed JSON；invalid／duplicate／missing gate 與矛盾
  evidence fail closed，rerun 以新 attempt／reference 建立新物件。
- 每個平台 run 發布獨立的 content-addressed evidence，綁定實際 OCI image ID、Git、application
  payload、deployment、migration、contract、scenario、restart 與 resource 結果；只有同一
  provenance 的 Windows Docker Desktop 與 Linux CI evidence 都通過，P1-EXIT 才能 passing。
- Windows Docker Desktop deployed path 已從不存在的 ticket-05 state 通過；目前 bundle 正確保持
  `blocked`（`dual_platform_evidence_required`），等待同 commit 的 hosted Linux artifact。
- Linux workflow 會執行真實 PostgreSQL opt-in suite，並在清理前 checksum 驗證及上傳匯出的
  bundle；repo 無 remote／hosted run 證據，因此對應 criterion 保持未勾選。
- Failure matrix 由實際觀察建立，correction／withdrawal 分列；optional modalities 由 REST／UI
  公開 phase boundary 觀察；fixture digest 綁 raw artifact 而非 dataset manifest。
- 驗證：`python -m pytest tests/acceptance/test_acceptance_runner.py
  tests/contracts/test_acceptance_bundle.py tests/contracts/test_compose_contract.py
  tests/contracts/test_object_repository.py -q`；`python -m mypy src tests`；
  `python -m ruff check .`；`python -m ruff format --check .`；上述 Docker Compose acceptance command。
- PostgreSQL 17 provider suite：依 `docs/development/postgresql-test-environment.md` 啟動隔離 project 後，
  `python -m pytest -m postgresql -q`。完整 suite：`python -m pytest -q`（PostgreSQL opt-in 另行
  通過）。確切 counts 與最終 bundle reference 以本次實作最終回報為準。

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
  ObjectRepository 發布 canonical、content-addressed JSON；missing gate fail closed，rerun 以新
  attempt／reference 建立新物件。
- Windows Docker Desktop deployed command 已從不存在的 ticket-05 project state 通過；每次驗收輸出
  當次 content-addressed bundle reference，最終提交後需再執行以對齊 Git commit 與 image payload。
- 驗證：`python -m pytest tests/acceptance/test_acceptance_runner.py
  tests/contracts/test_acceptance_bundle.py tests/contracts/test_compose_contract.py
  tests/contracts/test_object_repository.py -q`（21 passed）；`python -m mypy src tests`；
  `python -m ruff check .`；`python -m ruff format --check .`；上述 Docker Compose acceptance command。
- PostgreSQL 17 provider suite：依 `docs/development/postgresql-test-environment.md` 啟動隔離 project 後，
  `python -m pytest -m postgresql -q`（1 passed，116 deselected）。完整 suite：
  `python -m pytest -q`（116 passed，1 skipped；PostgreSQL opt-in 另行通過）。
- Linux CI workflow 與相同 command 已建立並由 contract test 驗證，但 repo 無 remote／hosted run
  證據；對應 criterion 保持未勾選，不宣稱 Linux CI 已通過。

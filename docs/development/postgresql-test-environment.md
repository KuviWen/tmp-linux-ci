# PostgreSQL 測試環境

`compose.test.yaml` 提供一個可丟棄、與完整 `compose.yaml` 驗收堆疊隔離的 PostgreSQL 17 環境。它使用獨立的 Compose project、`55432` host port 與 named volume，採 SCRAM 驗證，並讓應用程式以非 superuser 的 `stock_test` 角色執行 migration 與測試。

此環境只供本機與 CI 測試，不承載正式或共享資料。`.env.test.example` 內的密碼是公開且刻意弱化的測試值，不得在其他環境重用。

## 前置需求

- Docker Desktop（Windows）或 Docker Engine（Linux）
- Docker Compose v2（`docker compose`）
- 專案的 Python 3.12 virtual environment（執行 host-side pytest 時使用）

Windows／Codex 環境若 `docker` 不在 `PATH`，不得據此判定 Docker 未安裝，也不要改走 WSL。先依
[`docs/agents/docker-acceptance.md`](../agents/docker-acceptance.md) 解析 Docker Desktop CLI；預設候選為
`$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe`。以下命令中的 `docker` 可改為
`& $dockerExe`，並在 workspace sandbox 要求時使用核准的 shell escalation。

確認工具：

```powershell
docker version
docker compose version
```

## 啟動與驗證

先建立本機設定；`.env.test` 已被 Git 忽略：

```powershell
Copy-Item .env.test.example .env.test
```

啟動 PostgreSQL、等待健康檢查，並套用 Alembic migration：

```powershell
docker compose --env-file .env.test -f compose.test.yaml build migration
docker compose --env-file .env.test -f compose.test.yaml up -d --wait postgres
docker compose --env-file .env.test -f compose.test.yaml run --rm migration
docker compose --env-file .env.test -f compose.test.yaml run --rm db-check
```

第三個命令會以非 superuser 測試角色連線並輸出 database、role 與 PostgreSQL 版本。資料庫只綁定 `127.0.0.1:55432`，不對區域網路公開。

## 執行真實 PostgreSQL 整合測試

一般 `pytest` 不要求 Docker；PostgreSQL 測試只有在明確提供 `TEST_DATABASE_URL` 時才執行。PowerShell：

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://stock_test:stock_test_local_only@127.0.0.1:55432/stock_forecasting_test'
.\.venv\Scripts\python.exe -m pytest -m postgresql -q
Remove-Item Env:TEST_DATABASE_URL
```

Linux／macOS shell：

```bash
TEST_DATABASE_URL='postgresql+psycopg://stock_test:stock_test_local_only@127.0.0.1:55432/stock_forecasting_test' \
  python -m pytest -m postgresql -q
```

整合測試具兩層防護：只接受 PostgreSQL URL，且 database 名稱必須以 `_test` 結尾。它驗證 migration、實際連線角色、table 集合與 PostgreSQL transaction rollback。

## 停止、重啟與清空

保留資料停止：

```powershell
docker compose --env-file .env.test -f compose.test.yaml stop
```

再次啟動：

```powershell
docker compose --env-file .env.test -f compose.test.yaml start
```

刪除整個測試資料庫與 volume，回到乾淨狀態：

```powershell
docker compose --env-file .env.test -f compose.test.yaml down --volumes --remove-orphans
```

`down --volumes` 只會刪除 `stock-forecasting-postgres-test` project 的測試 volume；執行前仍應確認命令使用的是 `compose.test.yaml`。

## 自訂連線設定

可修改 `.env.test` 的 port、database、role 與密碼。若密碼含 URL 保留字，`TEST_DATABASE_URL` 與 Compose 內的 SQLAlchemy URL 必須使用百分比編碼。修改初始化角色後，既有 volume 不會重跑 init script；請先執行上述 `down --volumes` 再重建。

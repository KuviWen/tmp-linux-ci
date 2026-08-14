# Ticket 05 acceptance bundle

Ticket 05 的最高公共 seam 是以下唯一命令：

```text
docker compose --profile acceptance run --build --rm acceptance
```

命令會在 `stock-forecasting-ticket-05` Compose project 中啟動 PostgreSQL、migration、
filesystem ObjectRepository、Dagster code location／daemon／webserver、REST／繁中 UI、授權拒絕
端點及單一 acceptance runner。Runner 依序驗證 outbox crash/restart、stale fencing、雙市場 fixture
EOD、資料與介面情境、授權交集，最後將 canonical JSON bundle 寫入內容定址
ObjectRepository。成功輸出的 `bundle.object_id` 與 `bundle.checksum` 可用來定位不可變證據。
每次 deployed run 另發布內容定址的 platform evidence。單一 Windows 或 Linux run 可成功完成驗收，
但 `GATE-DEPLOY-01` 與 P1-EXIT bundle 會保持 `blocked`，直到同一 Git commit、application payload、
deployment 與 migration digests 下的 `windows_docker_desktop` 和 `linux_ci` evidence 都存在。

Ticket 05 使用獨立的 loopback host ports，避免覆蓋或停止先前 ticket 的 Compose project：

- PostgreSQL：`127.0.0.1:15435`
- REST／UI：`127.0.0.1:18005`
- Dagster：`127.0.0.1:13005`

Linux workflow 位於 `.github/workflows/p1-acceptance.yml`，固定使用 `ubuntu-24.04`，並執行相同
container command。Workflow 另執行非 PostgreSQL suite、真實 PostgreSQL 17 opt-in provider contract、
mypy、Ruff lint／format check 與 Compose config validation。Workflow 會先取得實際 OCI image ID，
並在清理 volume 前驗證匯出 bundle 的 SHA-256、上傳 `.artifacts/`。未有 GitHub hosted run URL 或
artifact 時，只能證明 workflow contract 與 Windows Docker Desktop 執行結果，不得宣稱 Linux CI
已通過。

Bundle 明示 P1 只證明工程脊柱：fixture model 不可 promotion、fixture 結果不可成為 production
PredictionRecord，且不宣稱正式來源授權、預測力、簽章或容量。若要連結上一份證據，可在直接 CLI
執行時提供 `--previous-bundle-reference sha256:<digest>`；publisher 會建立新的 content-addressed
bundle，不修改既有物件。

跨平台彙整時，將另一平台保存的 `p1-acceptance-bundle.json` 放入 acceptance export mount，並以
`P1_COUNTERPART_BUNDLE` 指向 container 內路徑。Runner 會重新計算其中 platform evidence 的內容
位址，且只接受與本次 provenance 相同的通過證據。OCI provenance 的預備 build 與驗收 build 均
使用 `BUILDX_NO_DEFAULT_ATTESTATIONS=1`；Docker image ID 必須在驗收前由 engine 讀取並透過
`P1_OCI_IMAGE_DIGEST` 傳入，workflow 已自動完成此步驟。

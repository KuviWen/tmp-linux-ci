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

Ticket 05 使用獨立的 loopback host ports，避免覆蓋或停止先前 ticket 的 Compose project：

- PostgreSQL：`127.0.0.1:15435`
- REST／UI：`127.0.0.1:18005`
- Dagster：`127.0.0.1:13005`

Linux workflow 位於 `.github/workflows/p1-acceptance.yml`，固定使用 `ubuntu-24.04`，並執行相同
container command。Workflow 另執行完整 pytest、mypy、Ruff lint／format check 與 Compose config
validation。未有 GitHub hosted run URL 或結果時，只能證明 workflow contract 與 Windows Docker
Desktop 上的 Linux container path，不得宣稱 Linux CI 已通過。

Bundle 明示 P1 只證明工程脊柱：fixture model 不可 promotion、fixture 結果不可成為 production
PredictionRecord，且不宣稱正式來源授權、預測力、簽章或容量。若要連結上一份證據，可在直接 CLI
執行時提供 `--previous-bundle-reference sha256:<digest>`；publisher 會建立新的 content-addressed
bundle，不修改既有物件。

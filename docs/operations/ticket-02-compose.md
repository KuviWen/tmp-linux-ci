# Ticket 02 Compose tracer-bullet runbook

This runbook starts the shared XTAI and XNAS fixture research path from ticket 02. The data
and predictions are synthetic fixture evidence. They are not licensed production data, formal
research predictions, or eligible model-promotion evidence.

## Start the runtime

Prerequisites are Docker Engine with the Compose plugin and free loopback ports 3000, 5432,
and 8000. From the repository root:

```console
docker compose config --quiet
docker compose up --build --wait postgres api dagster-code dagster-webserver dagster-daemon
```

The one-shot `migration` service must complete before the API and Dagster code location start.
The one-shot `dagster-init` service initializes the shared Dagster instance before the code
server, webserver, and daemon start. Compose readiness requires the code server's gRPC health
check, a GraphQL workspace containing both `xtai_fixture_eod` and `xnas_fixture_eod`, and healthy
heartbeats from every required Dagster daemon. The PostgreSQL trust configuration is local-only
and safe here solely because port 5432 is bound to `127.0.0.1`.

Public surfaces:

- REST and Traditional Chinese UI: `http://127.0.0.1:8000/research`
- versioned OpenAPI source: `http://127.0.0.1:8000/openapi/openapi.yaml`
- Dagster UI: `http://127.0.0.1:3000`
- readiness: `http://127.0.0.1:8000/readyz`

## Run the external acceptance seam

```console
docker compose --profile acceptance run --build --rm acceptance
```

Success is exit code zero with one JSON document whose `status` is `passed`. The ticket-02
runner uses the same `2026-08-12T22:00:00Z` information cutoff for XTAI and XNAS. Its checks cover
the shared workflow and Dagster adapter, market-specific calendars and company actions, REST,
the Traditional Chinese matrix and detail page, correction and unavailable-result behavior,
one-market failure isolation, canonical lineage, source health, security audit, and the absence
of production prediction records. In deployed mode it uses the migrated PostgreSQL schema and
calls the separately running API and Dagster GraphQL endpoints over HTTP.

The durable engineering evidence is split deliberately:

- `fixture-objects` stores content-addressed raw fixture bytes;
- `postgres-data` stores immutable research projections, lineage, fixture results, work attempts,
  source health, and security audit events;
- `dagster-home` stores Dagster runtime state.

Stop services without deleting the named evidence volumes:

```console
docker compose down
```

## Repository verification

```console
python -m pytest tests/acceptance tests/contracts -q
python -m pytest -q
python -m mypy src tests
python -m ruff check .
python -m ruff format --check .
python -m alembic -c alembic.ini upgrade head
python -m stock_forecasting.cli acceptance ticket-02 \
  --database-url sqlite+pysqlite:///ticket-02.db \
  --object-root .ticket-02-objects \
  --information-cutoff 2026-08-12T22:00:00Z \
  --observed-at 2026-08-12T21:55:00Z
```

The last command is a fast SQLite/filesystem developer check. It does not replace the Compose
acceptance command when claiming PostgreSQL or clean-container startup evidence.

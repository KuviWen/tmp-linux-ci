# Ticket 01 Compose tracer-bullet runbook

This runbook starts only the XTAI fixture engineering path from ticket 01. The data and
predictions are synthetic fixture evidence. They are not licensed production data, formal
research predictions, or eligible model-promotion evidence.

## Start the runtime

Prerequisites are Docker Engine with the Compose plugin and free loopback ports 3000, 5432,
and 8000. From the repository root:

```console
docker compose config --quiet
docker compose up --build --wait postgres api dagster-code dagster-webserver dagster-daemon
```

The one-shot `migration` service must complete before the API and Dagster code location start.
The one-shot `dagster-init` service then initializes the shared Dagster instance before the code
server, webserver, and daemon start, preventing concurrent first-run storage migrations. All
application services consume the same versioned image; `api` is the single owner of its build
context, so Compose sends one build session even when the repository path contains non-ASCII
characters.
The local-only PostgreSQL trust configuration is acceptable solely because port 5432 is bound
to `127.0.0.1`; it is not a production credential pattern.

Public surfaces:

- REST and Traditional Chinese UI: `http://127.0.0.1:8000/research`
- versioned OpenAPI source: `http://127.0.0.1:8000/openapi/openapi.yaml`
- Dagster UI: `http://127.0.0.1:3000`
- readiness: `http://127.0.0.1:8000/readyz`

## Run the external acceptance seam

```console
docker compose --profile acceptance run --build --rm acceptance
```

Success is exit code zero with one JSON document whose `status` is `passed`. Its checks cover
the direct workflow and Dagster adapter, REST, Traditional Chinese matrix/detail reload,
filesystem raw-object checksum, canonical lineage, source health, security audit, fixture-use
denials, and the absence of production prediction records. The runner uses the migrated
PostgreSQL schema and calls the separately running API over HTTP.

The durable engineering evidence is split deliberately:

- `fixture-objects` stores content-addressed raw fixture bytes;
- `postgres-data` stores immutable research projections, canonical lineage references,
  fixture prediction results, work attempts, source health, and security audit events;
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
python -m stock_forecasting.cli acceptance ticket-01 \
  --database-url sqlite+pysqlite:///ticket-01.db \
  --object-root .ticket-01-objects \
  --information-cutoff 2026-08-12T07:00:00Z \
  --observed-at 2026-08-12T06:55:00Z
```

The last command is a fast SQLite/filesystem developer check. It does not replace the Compose
acceptance command when claiming PostgreSQL or clean-container startup evidence.

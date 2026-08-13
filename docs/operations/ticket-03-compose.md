# Ticket 03 outbox recovery runbook

This runbook verifies the ticket 03 transactional-outbox tracer bullet. All inputs and
predictions are synthetic fixture evidence. They are not licensed production data, formal
research predictions, or production-readiness evidence.

## Start the runtime

Prerequisites are Docker Engine with the Compose plugin and free loopback ports 3000, 5432,
and 8000. From the repository root:

The specification defines this acceptance seam from a clean environment. For a later rerun,
first archive any evidence that must be retained. If the disposable ticket-03 named volumes are
from an earlier run and no longer need to be kept, remove only this Compose project's state:

```console
docker compose down --volumes --remove-orphans
```

Then start the clean runtime:

```console
docker compose config --quiet
docker compose up --build --wait postgres api dagster-code dagster-webserver dagster-daemon
```

The migration service creates the immutable outbox envelope, dispatch state, consumer markers,
projection cursors, delivery attempts, and correlated incidents before the API starts. Public
surfaces are:

- REST and Traditional Chinese UI: `http://127.0.0.1:8000/research`
- versioned OpenAPI source: `http://127.0.0.1:8000/openapi/openapi.yaml`
- Dagster UI: `http://127.0.0.1:3000`
- readiness: `http://127.0.0.1:8000/readyz`

## Run the crash-recovery acceptance seam

```console
docker compose --profile acceptance run --build --rm acceptance
```

Success is exit code zero with one JSON document whose `status` is `passed`. The runner creates
the authoritative prediction, core research projection, and pending outbox event in one
PostgreSQL transaction. It then kills a separate relay process before consumer work, restarts
the application from PostgreSQL truth, and verifies one-time research and operations effects.
It also exercises a consumer-transaction crash, duplicate delivery, repeated out-of-order
delivery, version catch-up, REST/UI stale-to-fresh status, audit evidence, work attempts, and
incident correlation.

The acceptance process intentionally uses a hard process termination only for its disposable
relay child. The parent runner remains alive to validate the durable evidence.

## Run one relay pass

```console
docker compose --profile relay run --rm outbox-relay
```

The command relays the oldest pending event once. It returns exit code zero for delivered,
already-delivered, or empty work; deferred or failed work returns nonzero and persists diagnostic
evidence for OperationsControl.

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
```

The crash-recovery acceptance command is the highest public verification seam. SQLite tests
remain fast developer feedback, but only the Compose command demonstrates clean-container
startup and PostgreSQL-backed process recovery.

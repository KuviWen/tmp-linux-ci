# Ticket 04 authorization denial runbook

This runbook verifies the local/development authorization tracer bullet. All market records are
synthetic fixture evidence and are not formal predictions or licensed production data.

## Runtime contract

The Compose project publishes PostgreSQL, REST/UI, and Dagster only on host loopback addresses.
Uvicorn itself binds only to container loopback. A pinned Nginx ingress shares the API network
namespace, publishes the host-loopback port, and forwards to Uvicorn over `127.0.0.1`; therefore
the verifier receives the real direct peer at its trust boundary instead of a configurable host
value. The revoked acceptance API uses the same arrangement on its private port.
`local-key-init` creates one ephemeral API-key file in the `local-api-key` named volume before
the API, Dagster code location, relay, or acceptance runner starts. The key is owned by
`local-researcher`, limited to the fixture pipeline and research-read scopes, valid only in the
development environment, and expires within 30 days. The command prints only an initialization
status; it never prints the credential. No key value is stored in Git, Compose YAML, or `.env`.
Clean startup issues the key for 24 hours from initialization time, so the verification does not
depend on a date embedded in Compose. An existing unexpired named-volume key is reused; an expired
or conflicting file fails closed and the disposable volume must be recreated deliberately.

The normal API and `dagster-code` location load active XTAI and XNAS source entitlements. The
acceptance-only `denied-api` and `stock_forecasting_denied` Dagster code location load the same key
and action grant but a revoked XTAI entitlement. All adapters call the same `AuthorizationPolicy`;
the denied services cannot substitute an admin or database role for an allow decision.

`authorization-init` installs immutable, principal-bound policy sets after Alembic migration and
before application processes start. Runtime environment variables select an explicit policy-set
identifier; they do not define grants, policies, or entitlements. The application connects as the
non-superuser `stock` role. `database-grants` grants application DML after initialization and then
revokes insert, update, and delete privileges on `authorization_policy_sets`; deployed acceptance
queries PostgreSQL privileges to verify that boundary.

## Verify the deployed seam

Prerequisites are Docker Engine with the Compose plugin and free host-loopback ports 3000, 5432,
and 8000. From the repository root:

```console
docker compose config --quiet
docker compose --profile acceptance up --build --wait postgres api api-ingress denied-api denied-api-ingress dagster-code denied-dagster-code dagster-webserver dagster-daemon
docker compose --profile acceptance run --rm acceptance
```

Success is exit code zero and one JSON document with `status` equal to `passed`. The runner
verifies active allow for both fixture markets, suspended/expired/revoked/purpose-removal
denials, missing action grant, unknown source policy, no raw/prediction persistence on denial,
existing projection blocking without deletion, redacted REST/UI problem details, an actual
GraphQL-launched run in the revoked Dagster code location, platform-admin denial, and full
controlled audit evidence for every evaluation.

The API exposes only `authentication_required` or `authorization_denied` plus the request trace
ID. The underlying reason and policy/grant/entitlement version identifiers remain in the
security audit. Readiness and the versioned OpenAPI document remain public.

Stop services without deleting evidence volumes:

```console
docker compose --profile acceptance down
```

Delete the named volumes only when the disposable evidence and ephemeral key are no longer
needed:

```console
docker compose --profile acceptance down --volumes --remove-orphans
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

The Compose acceptance command is the highest public seam. SQLite acceptance and contract tests
are fast developer evidence but do not replace clean-container PostgreSQL verification.

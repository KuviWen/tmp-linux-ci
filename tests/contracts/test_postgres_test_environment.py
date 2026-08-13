from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_postgres_test_compose_is_isolated_authenticated_and_migrated() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.test.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    postgres = services["postgres"]

    assert compose["name"] == "stock-forecasting-postgres-test"
    assert set(services) == {"postgres", "migration", "db-check"}
    assert postgres["image"] == "postgres:17-alpine"
    assert postgres["environment"]["POSTGRES_HOST_AUTH_METHOD"] == "scram-sha-256"
    assert "POSTGRES_TEST_USER" in postgres["environment"]
    assert postgres["ports"] == ["127.0.0.1:${POSTGRES_TEST_PORT:-55432}:5432"]
    assert postgres["healthcheck"]["test"][0] == "CMD-SHELL"
    assert postgres["security_opt"] == ["no-new-privileges:true"]
    assert "postgres-test-data:/var/lib/postgresql/data" in postgres["volumes"]

    init_mount = next(mount for mount in postgres["volumes"] if isinstance(mount, dict))
    assert init_mount == {
        "type": "bind",
        "source": "./docker/postgres/init-test-database.sh",
        "target": "/docker-entrypoint-initdb.d/10-test-database.sh",
        "read_only": True,
    }

    migration = services["migration"]
    assert migration["command"][-2:] == ["upgrade", "head"]
    assert migration["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert migration["environment"]["DATABASE_URL"].startswith(
        "postgresql+psycopg://${POSTGRES_TEST_USER:-stock_test}:"
    )

    db_check = services["db-check"]
    assert db_check["profiles"] == ["verify"]
    assert db_check["depends_on"]["migration"]["condition"] == ("service_completed_successfully")


def test_postgres_initializer_creates_a_least_privilege_test_role() -> None:
    initializer = (REPOSITORY_ROOT / "docker" / "postgres" / "init-test-database.sh").read_text(
        encoding="utf-8"
    )

    assert "NOSUPERUSER" in initializer
    assert "NOCREATEDB" in initializer
    assert "NOCREATEROLE" in initializer
    assert "NOINHERIT" in initializer
    assert "NOREPLICATION" in initializer
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in initializer
    assert "GRANT USAGE, CREATE ON SCHEMA public" in initializer

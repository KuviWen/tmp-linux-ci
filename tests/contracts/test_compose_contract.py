from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_compose_declares_the_deployable_ticket_04_runtime() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert compose["name"] == "stock-forecasting-ticket-04"
    assert set(services) == {
        "postgres",
        "migration",
        "local-key-init",
        "authorization-init",
        "database-grants",
        "denied-api",
        "denied-api-ingress",
        "api",
        "api-ingress",
        "dagster-init",
        "dagster-code",
        "denied-dagster-code",
        "dagster-webserver",
        "dagster-daemon",
        "outbox-relay",
        "acceptance",
    }
    assert services["postgres"]["image"] == "postgres:17-alpine"
    assert services["postgres"]["environment"] == {
        "POSTGRES_DB": "stock_forecasting",
        "POSTGRES_USER": "postgres",
        "POSTGRES_HOST_AUTH_METHOD": "trust",
    }
    assert services["migration"]["command"][-2:] == ["upgrade", "head"]
    assert services["acceptance"]["profiles"] == ["acceptance"]
    assert "ticket-04" in services["acceptance"]["command"]
    assert "--base-url" in services["acceptance"]["command"]
    assert "--observed-at" in services["acceptance"]["command"]
    application_services = {
        "migration",
        "local-key-init",
        "authorization-init",
        "denied-api",
        "api",
        "dagster-init",
        "dagster-code",
        "denied-dagster-code",
        "dagster-webserver",
        "dagster-daemon",
        "outbox-relay",
        "acceptance",
    }
    assert {services[name]["image"] for name in application_services} == {
        "stock-forecasting-ticket-04-app:0.1.0"
    }
    assert services["api"]["build"] == {"context": "."}
    assert all("build" not in services[name] for name in application_services if name != "api")
    assert services["outbox-relay"]["profiles"] == ["relay"]
    assert services["outbox-relay"]["command"] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "relay",
        "--once",
    ]
    assert services["outbox-relay"]["depends_on"]["migration"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["local-key-init"]["command"][:5] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "local-key",
        "init",
    ]
    assert "--owner" in services["local-key-init"]["command"]
    assert services["local-key-init"]["command"].count("--scope") == 2
    assert "--issued-at" not in services["local-key-init"]["command"]
    assert "--expires-at" not in services["local-key-init"]["command"]
    assert services["migration"]["environment"]["DATABASE_URL"].startswith(
        "postgresql+psycopg://postgres@"
    )
    assert services["authorization-init"]["command"][:5] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "authorization",
        "init-fixtures",
    ]
    assert (
        "postgresql+psycopg://postgres@postgres:5432/stock_forecasting"
        in services["authorization-init"]["command"]
    )
    assert services["database-grants"]["image"] == "postgres:17-alpine"
    assert services["database-grants"]["command"][-2:] == [
        "-f",
        "/opt/stock-forecasting/grant-application-role.sql",
    ]
    assert services["database-grants"]["volumes"] == [
        "./docker/postgres/grant-application-role.sql:/opt/stock-forecasting/grant-application-role.sql:ro"
    ]
    assert services["denied-api"]["environment"]["AUTHORIZATION_POLICY_SET_ID"] == (
        "fixture-revoked-v1"
    )
    assert services["denied-api"]["profiles"] == ["acceptance"]
    assert services["api"]["command"][-4:] == [
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    assert services["denied-api"]["command"][-4:] == [
        "--host",
        "127.0.0.1",
        "--port",
        "8001",
    ]
    assert services["api"]["ports"] == ["127.0.0.1:8000:8080"]
    assert services["api-ingress"]["image"] == "nginx:1.29.1-alpine"
    assert services["api-ingress"]["network_mode"] == "service:api"
    assert services["denied-api-ingress"]["network_mode"] == "service:denied-api"
    assert (
        services["acceptance"]["command"][services["acceptance"]["command"].index("--base-url") + 1]
        == "http://api:8080"
    )
    assert (
        services["acceptance"]["command"][
            services["acceptance"]["command"].index("--denied-base-url") + 1
        ]
        == "http://denied-api:8081"
    )
    assert "--denied-base-url" in services["acceptance"]["command"]
    assert services["acceptance"]["depends_on"]["denied-api"]["condition"] == ("service_healthy")
    assert services["acceptance"]["depends_on"]["api-ingress"]["condition"] == ("service_healthy")
    assert services["acceptance"]["depends_on"]["denied-api-ingress"]["condition"] == (
        "service_healthy"
    )
    for name in (
        "api",
        "dagster-code",
        "denied-dagster-code",
        "outbox-relay",
        "acceptance",
    ):
        assert services[name]["depends_on"]["local-key-init"]["condition"] == (
            "service_completed_successfully"
        )
        assert services[name]["depends_on"]["database-grants"]["condition"] == (
            "service_completed_successfully"
        )
    assert services["dagster-init"]["command"] == ["dagster", "instance", "migrate"]
    assert services["dagster-code"]["healthcheck"]["test"] == [
        "CMD",
        "dagster",
        "api",
        "grpc-health-check",
        "-h",
        "127.0.0.1",
        "-p",
        "4000",
    ]
    assert services["denied-dagster-code"]["environment"] == services["dagster-code"][
        "environment"
    ] | {
        "AUTHORIZATION_POLICY_SET_ID": "fixture-revoked-v1",
        "AUTHORIZATION_ACCEPTANCE_MODE": "denied",
    }
    for name in (
        "dagster-code",
        "denied-dagster-code",
        "dagster-webserver",
        "dagster-daemon",
    ):
        assert services[name]["depends_on"]["dagster-init"]["condition"] == (
            "service_completed_successfully"
        )
    assert services["dagster-webserver"]["depends_on"]["dagster-code"]["condition"] == (
        "service_healthy"
    )
    assert services["dagster-webserver"]["depends_on"]["denied-dagster-code"]["condition"] == (
        "service_healthy"
    )
    assert (
        "stock_forecasting.dagster_deployment"
        in services["dagster-webserver"]["healthcheck"]["test"]
    )
    assert "workspace" in services["dagster-webserver"]["healthcheck"]["test"]
    assert (
        "stock_forecasting.dagster_deployment" in services["dagster-daemon"]["healthcheck"]["test"]
    )
    assert "daemons" in services["dagster-daemon"]["healthcheck"]["test"]
    assert "--dagster-url" in services["acceptance"]["command"]
    for name in (
        "dagster-code",
        "denied-dagster-code",
        "dagster-webserver",
        "dagster-daemon",
    ):
        assert services["acceptance"]["depends_on"][name]["condition"] == "service_healthy"
    assert compose["x-application-environment"]["FIXTURE_COLLECTION_OBSERVED_AT"] == (
        "2026-08-12T21:55:00Z"
    )
    assert compose["x-application-environment"]["FIXTURE_INFORMATION_CUTOFF"] == (
        "2026-08-12T22:00:00Z"
    )
    assert compose["x-application-environment"] | {
        "DATABASE_URL": None,
        "OBJECT_ROOT": None,
        "FIXTURE_INFORMATION_CUTOFF": None,
        "FIXTURE_COLLECTION_OBSERVED_AT": None,
        "DAGSTER_HOME": None,
    } == {
        "DATABASE_URL": None,
        "OBJECT_ROOT": None,
        "FIXTURE_INFORMATION_CUTOFF": None,
        "FIXTURE_COLLECTION_OBSERVED_AT": None,
        "DAGSTER_HOME": None,
        "RUNTIME_ENVIRONMENT": "development",
        "PUBLIC_BIND_HOST": "127.0.0.1",
        "LOCAL_API_KEY_MODE": "enabled",
        "LOCAL_API_KEY_FILE": "/run/stock-forecasting/local-api-key.json",
        "PLATFORM_ADMIN_API_KEY_FILE": "/run/stock-forecasting/platform-admin-api-key.json",
        "AUTHORIZATION_POLICY_SET_ID": "fixture-active-v1",
    }
    for name in application_services:
        assert "local-api-key:/run/stock-forecasting" in services[name]["volumes"]
    assert "local-api-key" in compose["volumes"]
    role_grant_sql = (
        REPOSITORY_ROOT / "docker" / "postgres" / "grant-application-role.sql"
    ).read_text(encoding="utf-8")
    assert "NOSUPERUSER" in role_grant_sql
    assert "REVOKE INSERT, UPDATE, DELETE ON authorization_policy_sets FROM stock" in (
        role_grant_sql
    )
    assert "proxy_pass http://127.0.0.1:8000" in (
        REPOSITORY_ROOT / "docker" / "nginx" / "api-loopback.conf"
    ).read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:8001" in (
        REPOSITORY_ROOT / "docker" / "nginx" / "denied-api-loopback.conf"
    ).read_text(encoding="utf-8")

    for name in ("postgres", "api", "dagster-webserver"):
        assert all(str(port).startswith("127.0.0.1:") for port in services[name]["ports"])

    serialized = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8").lower()
    assert "password" not in serialized
    assert ":latest" not in serialized


def test_container_build_is_pinned_non_root_and_uses_a_lock_file() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith("FROM python:3.12.12-slim\n")
    assert "COPY requirements.lock pyproject.toml ./" in dockerfile
    assert "/run/stock-forecasting" in dockerfile
    assert "USER app" in dockerfile
    assert '"--host", "127.0.0.1"' in dockerfile
    assert (REPOSITORY_ROOT / "requirements.lock").is_file()


def test_dagster_workspace_exposes_a_separate_revoked_entitlement_location() -> None:
    workspace = yaml.safe_load(
        (REPOSITORY_ROOT / "dagster-workspace.yaml").read_text(encoding="utf-8")
    )

    locations = {
        entry["grpc_server"]["location_name"]: entry["grpc_server"]["host"]
        for entry in workspace["load_from"]
    }
    assert locations == {
        "stock_forecasting": "dagster-code",
        "stock_forecasting_denied": "denied-dagster-code",
    }

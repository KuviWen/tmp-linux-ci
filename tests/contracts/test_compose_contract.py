from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_compose_declares_the_deployable_ticket_02_runtime() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert compose["name"] == "stock-forecasting-ticket-02"
    assert set(services) == {
        "postgres",
        "migration",
        "api",
        "dagster-init",
        "dagster-code",
        "dagster-webserver",
        "dagster-daemon",
        "acceptance",
    }
    assert services["postgres"]["image"] == "postgres:17-alpine"
    assert services["postgres"]["environment"] == {
        "POSTGRES_DB": "stock_forecasting",
        "POSTGRES_USER": "stock",
        "POSTGRES_HOST_AUTH_METHOD": "trust",
    }
    assert services["migration"]["command"][-2:] == ["upgrade", "head"]
    assert services["acceptance"]["profiles"] == ["acceptance"]
    assert "ticket-02" in services["acceptance"]["command"]
    assert "--base-url" in services["acceptance"]["command"]
    assert "--observed-at" in services["acceptance"]["command"]
    application_services = {
        "migration",
        "api",
        "dagster-init",
        "dagster-code",
        "dagster-webserver",
        "dagster-daemon",
        "acceptance",
    }
    assert {services[name]["image"] for name in application_services} == {
        "stock-forecasting-ticket-02-app:0.1.0"
    }
    assert services["api"]["build"] == {"context": "."}
    assert all("build" not in services[name] for name in application_services if name != "api")
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
    for name in ("dagster-code", "dagster-webserver", "dagster-daemon"):
        assert services[name]["depends_on"]["dagster-init"]["condition"] == (
            "service_completed_successfully"
        )
    assert services["dagster-webserver"]["depends_on"]["dagster-code"]["condition"] == (
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
    for name in ("dagster-code", "dagster-webserver", "dagster-daemon"):
        assert services["acceptance"]["depends_on"][name]["condition"] == "service_healthy"
    assert compose["x-application-environment"]["FIXTURE_COLLECTION_OBSERVED_AT"] == (
        "2026-08-12T21:55:00Z"
    )
    assert compose["x-application-environment"]["FIXTURE_INFORMATION_CUTOFF"] == (
        "2026-08-12T22:00:00Z"
    )

    for name in ("postgres", "api", "dagster-webserver"):
        assert all(str(port).startswith("127.0.0.1:") for port in services[name]["ports"])

    serialized = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8").lower()
    assert "password" not in serialized
    assert ":latest" not in serialized


def test_container_build_is_pinned_non_root_and_uses_a_lock_file() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.startswith("FROM python:3.12.12-slim\n")
    assert "COPY requirements.lock pyproject.toml ./" in dockerfile
    assert "USER app" in dockerfile
    assert (REPOSITORY_ROOT / "requirements.lock").is_file()

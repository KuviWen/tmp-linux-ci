from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_compose_declares_the_deployable_ticket_01_runtime() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(services) == {
        "postgres",
        "migration",
        "api",
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
    assert "--base-url" in services["acceptance"]["command"]

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

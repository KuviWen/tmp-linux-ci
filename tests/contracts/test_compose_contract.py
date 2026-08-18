from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_compose_declares_the_deployable_ticket_05_runtime() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert compose["name"] == "stock-forecasting-ticket-05"
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
        "evidence-init",
        "image-provenance",
        "acceptance",
        "ticket-06-local-key-init",
        "ticket-06-source-adapter-key-init",
        "ticket-06-authorization-init",
        "ticket-06-source-probe",
        "ticket-06-api",
        "ticket-06-api-ingress",
        "ticket-06-acceptance",
        "ticket-07-local-key-init",
        "ticket-07-source-adapter-key-init",
        "ticket-07-authorization-init",
        "ticket-07-api",
        "ticket-07-api-ingress",
        "ticket-07-acceptance",
        "ticket-08-local-key-init",
        "ticket-08-authorization-init",
        "ticket-08-api",
        "ticket-08-api-ingress",
        "ticket-08-acceptance",
        "ticket-09-local-key-init",
        "ticket-09-authorization-init",
        "ticket-09-api",
        "ticket-09-api-ingress",
        "ticket-09-acceptance",
        "ticket-09-operator-postgres",
        "ticket-09-operator-migration",
        "ticket-09-operator-local-key-init",
        "ticket-09-operator-source-adapter-key-init",
        "ticket-09-operator-authorization-init",
        "ticket-09-operator-database-grants",
        "ticket-09-operator-api",
        "ticket-09-operator-api-ingress",
        "ticket-09-operator-cli",
    }
    assert services["postgres"]["image"] == "postgres:17-alpine"
    assert services["postgres"]["environment"] == {
        "POSTGRES_DB": "stock_forecasting",
        "POSTGRES_USER": "postgres",
        "POSTGRES_HOST_AUTH_METHOD": "trust",
    }
    assert services["migration"]["command"][-2:] == ["upgrade", "head"]
    assert services["acceptance"]["profiles"] == ["acceptance"]
    assert "ticket-05" in services["acceptance"]["command"]
    assert "--base-url" in services["acceptance"]["command"]
    assert "--observed-at" in services["acceptance"]["command"]
    assert services["acceptance"]["command"][-4:] == [
        "--project-root",
        "/app",
        "--git-dir",
        "/workspace/.git",
    ]
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
        "stock-forecasting-ticket-05-app:0.1.0"
    }
    assert services["api"]["build"] == {
        "context": ".",
        "args": {"SOURCE_DATE_EPOCH": "0"},
    }
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
    assert services["postgres"]["ports"] == ["127.0.0.1:15435:5432"]
    assert services["api"]["ports"] == ["127.0.0.1:18005:8080"]
    assert services["dagster-webserver"]["ports"] == ["127.0.0.1:13005:3000"]
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
    assert services["image-provenance"]["profiles"] == ["acceptance"]
    assert services["image-provenance"]["image"] == "docker:28.5.2-cli"
    assert services["image-provenance"]["entrypoint"] == ["/bin/sh", "-ec"]
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in services["image-provenance"]["volumes"]
    assert services["image-provenance"]["depends_on"]["api"]["condition"] == ("service_healthy")
    assert "docker ps" in services["image-provenance"]["command"][0]
    assert "docker inspect" in services["image-provenance"]["command"][0]
    assert "oci-image-id.tmp" in services["image-provenance"]["command"][0]
    assert (
        "mv /evidence/oci-image-id.tmp /evidence/oci-image-id"
        in services["image-provenance"]["command"][0]
    )
    assert "while true" not in services["image-provenance"]["command"][0]
    assert services["acceptance"]["depends_on"]["image-provenance"]["condition"] == (
        "service_completed_successfully"
    )
    assert (
        "image-provenance:/run/stock-forecasting/image-provenance:ro"
        in services["acceptance"]["volumes"]
    )
    assert services["evidence-init"]["profiles"] == ["acceptance"]
    assert services["evidence-init"]["image"] == "alpine:3.22.1"
    assert "chmod 0777 /evidence" in services["evidence-init"]["command"][0]
    assert "./.artifacts:/evidence" in services["evidence-init"]["volumes"]
    assert services["acceptance"]["depends_on"]["evidence-init"]["condition"] == (
        "service_completed_successfully"
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
        "P1_ACCEPTANCE_PLATFORM": "${P1_ACCEPTANCE_PLATFORM:-windows_docker_desktop}",
        "P1_OCI_IMAGE_DIGEST": "${P1_OCI_IMAGE_DIGEST:-}",
        "P1_OCI_IMAGE_DIGEST_FILE": ("/run/stock-forecasting/image-provenance/oci-image-id"),
        "P1_ACCEPTANCE_EXPORT_DIR": "/var/lib/stock-forecasting/exports",
        "P1_COUNTERPART_BUNDLE": "${P1_COUNTERPART_BUNDLE:-}",
    }
    for name in application_services:
        assert "local-api-key:/run/stock-forecasting" in services[name]["volumes"]
    assert "./.git:/workspace/.git:ro" in services["acceptance"]["volumes"]
    assert "./.artifacts:/var/lib/stock-forecasting/exports" in services["acceptance"]["volumes"]
    assert compose["x-application-environment"]["P1_ACCEPTANCE_PLATFORM"] == (
        "${P1_ACCEPTANCE_PLATFORM:-windows_docker_desktop}"
    )
    assert compose["x-application-environment"]["P1_ACCEPTANCE_EXPORT_DIR"] == (
        "/var/lib/stock-forecasting/exports"
    )
    assert compose["x-application-environment"]["P1_OCI_IMAGE_DIGEST_FILE"] == (
        "/run/stock-forecasting/image-provenance/oci-image-id"
    )
    assert "local-api-key" in compose["volumes"]
    assert "image-provenance" in compose["volumes"]
    role_grant_sql = (
        REPOSITORY_ROOT / "docker" / "postgres" / "grant-application-role.sql"
    ).read_text(encoding="utf-8")
    assert "NOSUPERUSER" in role_grant_sql
    assert "REVOKE INSERT, UPDATE, DELETE ON authorization_policy_sets FROM stock" in (
        role_grant_sql
    )
    assert "REVOKE UPDATE, DELETE ON model_lifecycle_events FROM stock" in role_grant_sql
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


def test_compose_declares_ticket_06_finmind_credential_lifecycle_acceptance() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    profile = ["ticket-06-acceptance"]

    assert services["ticket-06-local-key-init"]["profiles"] == profile
    key_command = services["ticket-06-local-key-init"]["command"]
    assert key_command.count("--scope") == 3
    assert "market_data.collect" not in key_command
    assert "price_research_eligibility.read" in key_command
    assert "source_credential.read" in key_command
    assert "source_credential.manage" in key_command
    assert key_command.count("--data-protection-class") == 3
    assert {"licensed", "restricted", "secret"} <= set(key_command)
    adapter_key = services["ticket-06-source-adapter-key-init"]
    assert adapter_key["profiles"] == profile
    assert adapter_key["command"].count("--scope") == 1
    assert "market_data.collect" in adapter_key["command"]
    assert services["ticket-06-authorization-init"]["profiles"] == profile
    assert "init-ticket-06" in services["ticket-06-authorization-init"]["command"]
    assert "--source-adapter-key-file" in services["ticket-06-authorization-init"]["command"]
    assert services["ticket-06-api"]["profiles"] == profile
    assert services["ticket-06-api"]["environment"]["AUTHORIZATION_POLICY_SET_ID"] == (
        "ticket-06-finmind-zero-fee-engineering-v1"
    )
    assert services["ticket-06-api"]["environment"]["SOURCE_ADAPTER_API_KEY_FILE"] == (
        "/run/stock-forecasting-source-adapter/local-api-key.json"
    )
    assert services["ticket-06-api"]["environment"]["SOURCE_SECRET_ROOT"] == (
        "/var/lib/stock-forecasting/source-secrets"
    )
    assert services["ticket-06-api-ingress"]["network_mode"] == "service:ticket-06-api"
    acceptance = services["ticket-06-acceptance"]
    assert acceptance["profiles"] == profile
    assert acceptance["command"][:5] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "acceptance",
        "ticket-06",
    ]
    assert "--base-url" in acceptance["command"]
    assert "--key-file" in acceptance["command"]
    assert "--source-adapter-key-file" not in acceptance["command"]
    assert "--source-secret-root" not in acceptance["command"]
    assert all("source-adapter" not in volume for volume in acceptance["volumes"])
    assert all("source-secrets" not in volume for volume in acceptance["volumes"])
    source_probe = services["ticket-06-source-probe"]
    assert source_probe["profiles"] == profile
    assert "ticket-06-source-probe" in source_probe["command"]
    assert "ticket-06-local-key" not in " ".join(source_probe["volumes"])
    assert "ticket-06-source-adapter-key" in " ".join(source_probe["volumes"])
    assert (
        "ticket-06-source-secrets:/var/lib/stock-forecasting/source-secrets:ro"
        in source_probe["volumes"]
    )
    assert "--source-secret-root" in source_probe["command"]
    assert source_probe["depends_on"]["ticket-06-api"]["condition"] == "service_healthy"
    assert acceptance["depends_on"]["ticket-06-source-probe"]["condition"] == (
        "service_completed_successfully"
    )
    assert acceptance["depends_on"]["ticket-06-api"]["condition"] == "service_healthy"
    assert acceptance["depends_on"]["ticket-06-api-ingress"]["condition"] == ("service_healthy")


def test_compose_declares_ticket_07_missing_credential_deployed_acceptance() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    profile = ["ticket-07-acceptance"]

    key_init = services["ticket-07-local-key-init"]
    assert key_init["profiles"] == profile
    assert key_init["command"].count("--scope") == 3
    assert {
        "price_research_eligibility.read",
        "source_credential.read",
        "source_credential.manage",
    } <= set(key_init["command"])
    assert "market_data.collect" not in key_init["command"]
    assert key_init["command"].count("--data-protection-class") == 3
    assert {"licensed", "restricted", "secret"} <= set(key_init["command"])

    adapter_key_init = services["ticket-07-source-adapter-key-init"]
    assert adapter_key_init["profiles"] == profile
    assert adapter_key_init["command"].count("--scope") == 1
    assert "market_data.collect" in adapter_key_init["command"]
    assert "source_credential.manage" not in adapter_key_init["command"]

    authorization_init = services["ticket-07-authorization-init"]
    assert authorization_init["profiles"] == profile
    assert "init-ticket-07" in authorization_init["command"]
    assert "--source-adapter-key-file" in authorization_init["command"]

    api = services["ticket-07-api"]
    assert api["profiles"] == profile
    assert api["environment"]["AUTHORIZATION_POLICY_SET_ID"] == (
        "ticket-07-us-zero-fee-engineering-v1"
    )
    assert api["environment"]["SOURCE_SECRET_ROOT"] == ("/var/lib/stock-forecasting/source-secrets")
    assert api["environment"]["SOURCE_ADAPTER_API_KEY_FILE"] == (
        "/run/stock-forecasting-source-adapter/local-api-key.json"
    )
    assert "ticket-07-source-secrets:/var/lib/stock-forecasting/source-secrets" in api["volumes"]
    assert "ticket-07-source-adapter-key:/run/stock-forecasting-source-adapter:ro" in api["volumes"]
    assert services["ticket-07-api-ingress"]["network_mode"] == "service:ticket-07-api"

    acceptance = services["ticket-07-acceptance"]
    assert acceptance["profiles"] == profile
    assert acceptance["command"][:5] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "acceptance",
        "ticket-07",
    ]
    assert "--base-url" in acceptance["command"]
    assert "--key-file" in acceptance["command"]
    assert "--source-adapter-key-file" in acceptance["command"]
    assert acceptance["depends_on"]["ticket-07-api"]["condition"] == "service_healthy"
    assert acceptance["depends_on"]["ticket-07-api-ingress"]["condition"] == ("service_healthy")
    assert "ticket-07-source-secrets" in compose["volumes"]
    assert "ticket-07-source-adapter-key" in compose["volumes"]


def test_compose_declares_ticket_08_historical_reconstruction_acceptance() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    profile = ["ticket-08-acceptance"]

    assert services["ticket-08-local-key-init"]["profiles"] == profile
    assert "price_research_eligibility.read" in services["ticket-08-local-key-init"]["command"]
    assert services["ticket-08-authorization-init"]["profiles"] == profile
    assert "init-ticket-08" in services["ticket-08-authorization-init"]["command"]
    api = services["ticket-08-api"]
    assert api["profiles"] == profile
    assert api["environment"]["AUTHORIZATION_POLICY_SET_ID"] == (
        "ticket-08-historical-reconstruction-engineering-v1"
    )
    assert services["ticket-08-api-ingress"]["network_mode"] == ("service:ticket-08-api")
    acceptance = services["ticket-08-acceptance"]
    assert acceptance["profiles"] == profile
    assert acceptance["command"][:5] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "acceptance",
        "ticket-08",
    ]
    assert "--base-url" in acceptance["command"]
    assert "--key-file" in acceptance["command"]
    assert acceptance["depends_on"]["ticket-08-api"]["condition"] == "service_healthy"
    assert acceptance["depends_on"]["ticket-08-api-ingress"]["condition"] == ("service_healthy")
    assert "ticket-08-local-key" in compose["volumes"]


def test_compose_declares_ticket_09_bootstrap_governance_acceptance() -> None:
    compose = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    profile = ["ticket-09-acceptance"]

    key_init = services["ticket-09-local-key-init"]
    assert key_init["profiles"] == profile
    assert "model_governance.read" in key_init["command"]
    assert "model_governance.approve" in key_init["command"]
    assert services["ticket-09-authorization-init"]["profiles"] == profile
    assert "init-ticket-09" in services["ticket-09-authorization-init"]["command"]
    api = services["ticket-09-api"]
    assert api["profiles"] == profile
    assert api["environment"]["AUTHORIZATION_POLICY_SET_ID"] == (
        "ticket-09-bootstrap-governance-engineering-v1"
    )
    assert services["ticket-09-api-ingress"]["network_mode"] == ("service:ticket-09-api")
    acceptance = services["ticket-09-acceptance"]
    assert acceptance["profiles"] == profile
    assert acceptance["command"][:5] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "acceptance",
        "ticket-09",
    ]
    assert "--base-url" in acceptance["command"]
    assert "--key-file" in acceptance["command"]
    assert acceptance["depends_on"]["ticket-09-api"]["condition"] == "service_healthy"
    assert acceptance["depends_on"]["ticket-09-api-ingress"]["condition"] == ("service_healthy")
    assert "ticket-09-local-key" in compose["volumes"]


def test_compose_declares_the_persistent_owner_operator_runtime() -> None:
    compose_path = REPOSITORY_ROOT / "compose.yaml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]
    profile = ["ticket-09-operator"]

    operator_services = {
        "ticket-09-operator-postgres",
        "ticket-09-operator-migration",
        "ticket-09-operator-local-key-init",
        "ticket-09-operator-source-adapter-key-init",
        "ticket-09-operator-authorization-init",
        "ticket-09-operator-database-grants",
        "ticket-09-operator-api",
        "ticket-09-operator-api-ingress",
        "ticket-09-operator-cli",
    }
    assert all(services[name]["profiles"] == profile for name in operator_services)

    postgres = services["ticket-09-operator-postgres"]
    assert postgres["volumes"] == ["ticket-09-operator-postgres-data:/var/lib/postgresql/data"]
    assert "ports" not in postgres

    owner_key = services["ticket-09-operator-local-key-init"]
    assert owner_key["command"][:5] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "local-key",
        "init",
    ]
    assert "${OPERATOR_OWNER_PRINCIPAL:-owner-local}" in owner_key["command"]
    assert {
        "price_research_eligibility.read",
        "source_credential.read",
        "source_credential.manage",
        "model_governance.read",
        "model_governance.approve",
    } <= set(owner_key["command"])
    assert "market_data.collect" not in owner_key["command"]
    assert owner_key["command"][owner_key["command"].index("--lifetime-hours") + 1] == "720"

    adapter_key = services["ticket-09-operator-source-adapter-key-init"]
    assert "market_data.collect" in adapter_key["command"]
    assert "source_credential.manage" not in adapter_key["command"]
    assert adapter_key["command"][adapter_key["command"].index("--lifetime-hours") + 1] == ("720")

    authorization = services["ticket-09-operator-authorization-init"]
    assert "init-operator" in authorization["command"]
    assert "--source-adapter-key-file" in authorization["command"]

    api = services["ticket-09-operator-api"]
    assert api["environment"]["RUNTIME_ENVIRONMENT"] == "local"
    assert api["environment"]["AUTHORIZATION_POLICY_SET_ID"] == (
        "${OPERATOR_AUTHORIZATION_POLICY_SET_ID:-ticket-09-owner-operator-v1}"
    )
    assert api["environment"]["SOURCE_SECRET_ROOT"] == ("/var/lib/stock-forecasting/source-secrets")
    assert "FIXTURE_INFORMATION_CUTOFF" not in api["environment"]
    assert "FIXTURE_COLLECTION_OBSERVED_AT" not in api["environment"]
    assert api["ports"] == ["127.0.0.1:18009:8080"]
    assert {
        "ticket-09-operator-objects:/var/lib/stock-forecasting/objects",
        "ticket-09-operator-source-secrets:/var/lib/stock-forecasting/source-secrets",
        "ticket-09-operator-local-key:/run/stock-forecasting:ro",
        ("ticket-09-operator-source-adapter-key:/run/stock-forecasting-source-adapter:ro"),
    } <= set(api["volumes"])
    assert services["ticket-09-operator-api-ingress"]["network_mode"] == (
        "service:ticket-09-operator-api"
    )

    operator_cli = services["ticket-09-operator-cli"]
    assert operator_cli["command"] == [
        "python",
        "-m",
        "stock_forecasting.cli",
        "operator",
        "--help",
    ]
    assert operator_cli["environment"] == {
        "OPERATOR_BASE_URL": "http://ticket-09-operator-api:8080",
        "LOCAL_API_KEY_FILE": "/run/stock-forecasting/local-api-key.json",
    }
    assert operator_cli["volumes"] == ["ticket-09-operator-local-key:/run/stock-forecasting:ro"]

    assert {
        "ticket-09-operator-postgres-data",
        "ticket-09-operator-objects",
        "ticket-09-operator-local-key",
        "ticket-09-operator-source-adapter-key",
        "ticket-09-operator-source-secrets",
    } <= set(compose["volumes"])
    serialized = compose_path.read_text(encoding="utf-8")
    assert "FINMIND_TOKEN" not in serialized
    assert "ALPACA_API_KEY" not in serialized
    assert "ALPACA_API_SECRET" not in serialized


def test_operator_runbook_targets_only_the_operator_dependency_tree() -> None:
    runbook = (REPOSITORY_ROOT / "docs" / "operations" / "ticket-09-ac5-7-runbook.md").read_text(
        encoding="utf-8"
    )
    project = "docker compose -p stock-forecasting-ticket-09-operator"

    assert (
        f"{project} --profile ticket-09-operator up -d --build --wait ticket-09-operator-cli"
    ) in runbook
    assert (
        f"{project} --profile ticket-09-operator up -d --wait ticket-09-operator-cli"
    ) in runbook
    assert "HTTP 403 `authorization_denied`" in runbook
    assert "Compose wrapper 應回傳非零" in runbook


def test_container_build_is_pinned_non_root_and_uses_a_lock_file() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert dockerfile.startswith("FROM python:3.12.12-slim\n")
    assert "COPY requirements.lock pyproject.toml ./" in dockerfile
    assert "COPY Dockerfile compose.yaml .dockerignore ./" in dockerfile
    assert "COPY docker ./docker" in dockerfile
    assert "/run/stock-forecasting-source-adapter" in dockerfile
    assert "COPY .github ./.github" in dockerfile
    assert "/run/stock-forecasting" in dockerfile
    assert "/var/lib/stock-forecasting/source-secrets" in dockerfile
    assert "USER app" in dockerfile
    assert '"--host", "127.0.0.1"' in dockerfile
    assert (REPOSITORY_ROOT / "requirements.lock").is_file()
    assert "*.egg-info" in dockerignore


def test_linux_ci_uses_the_same_compose_acceptance_command() -> None:
    workflow = yaml.safe_load(
        (REPOSITORY_ROOT / ".github" / "workflows" / "p1-acceptance.yml").read_text(
            encoding="utf-8"
        )
    )
    job = workflow["jobs"]["p1-acceptance"]

    assert job["runs-on"] == "ubuntu-24.04"
    assert job["env"] == {
        "BUILDX_NO_DEFAULT_ATTESTATIONS": "1",
        "P1_ACCEPTANCE_PLATFORM": "linux_ci",
    }
    assert workflow["permissions"] == {"contents": "read"}
    commands = [step.get("run") for step in job["steps"]]
    assert "docker compose --profile acceptance run --build --rm acceptance" in commands
    assert (
        'python -m pytest -m "not postgresql" --junitxml=.artifacts/non-postgresql.xml' in commands
    )
    postgres_step = next(
        step for step in job["steps"] if step.get("name") == "PostgreSQL provider contract"
    )
    assert postgres_step["env"]["TEST_DATABASE_URL"].startswith("postgresql+psycopg://stock_test:")
    assert postgres_step["run"] == (
        "python -m pytest -m postgresql -q --junitxml=.artifacts/postgresql.xml"
    )
    assert (
        "cd .artifacts && sha256sum non-postgresql.xml postgresql.xml > contract-reports.sha256"
        in commands
    )
    assert "python -m mypy src tests" in commands
    assert "python -m ruff check ." in commands
    assert "python -m ruff format --check ." in commands
    assert all("docker compose build api" not in str(command) for command in commands)
    assert all("docker image inspect" not in str(command) for command in commands)
    assert all("chmod 0777 .artifacts" not in str(command) for command in commands)
    assert all(step.get("name") != "Prepare acceptance evidence directory" for step in job["steps"])
    acceptance_index = next(
        index
        for index, step in enumerate(job["steps"])
        if step.get("run") == "docker compose --profile acceptance run --build --rm acceptance"
    )
    verify_index = next(
        index
        for index, step in enumerate(job["steps"])
        if step.get("name") == "Verify exported acceptance bundle"
    )
    upload_index = next(
        index
        for index, step in enumerate(job["steps"])
        if step.get("uses") == "actions/upload-artifact@v4"
    )
    cleanup_index = next(
        index
        for index, step in enumerate(job["steps"])
        if step.get("name") == "Clean acceptance state"
    )
    assert acceptance_index < verify_index < upload_index < cleanup_index
    assert job["steps"][verify_index]["run"] == (
        "cd .artifacts && sha256sum --check contract-reports.sha256 && "
        "sha256sum --check p1-acceptance-bundle.json.sha256 && "
        "sha256sum --check p1-evidence-objects.sha256"
    )
    assert job["steps"][upload_index]["with"]["path"] == ".artifacts/"
    assert job["steps"][upload_index]["with"]["include-hidden-files"] is True
    cleanup = job["steps"][cleanup_index]
    assert cleanup["if"] == "always()"
    assert cleanup["run"] == "docker compose --profile acceptance down --volumes --remove-orphans"


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

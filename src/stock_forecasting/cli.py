from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from getpass import getpass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from stock_forecasting.acceptance import (
    run_ticket_01,
    run_ticket_02,
    run_ticket_03,
    run_ticket_04,
    run_ticket_05,
)
from stock_forecasting.acceptance_bundle import is_sha256_reference
from stock_forecasting.authorization import (
    LocalApiKeyIdentity,
    build_fixture_authorization_policy,
    build_historical_reconstruction_engineering_authorization_policy,
    build_pending_rights_operator_authorization_policy,
    build_taiwan_finmind_engineering_authorization_policy,
    build_us_zero_fee_engineering_authorization_policy,
)
from stock_forecasting.authorization_repository import (
    FIXTURE_REVOKED_POLICY_SET,
    TICKET_06_FINMIND_ENGINEERING_POLICY_SET,
    TICKET_07_ENGINEERING_POLICY_SET,
    TICKET_08_ENGINEERING_POLICY_SET,
    TICKET_09_ENGINEERING_POLICY_SET,
    TICKET_09_OWNER_OPERATOR_POLICY_SET,
    AuthorizationPolicyRepository,
    fixture_authorization_policy_catalog,
)
from stock_forecasting.platform.state_store import StateStore
from stock_forecasting.runtime import RuntimeSettings
from stock_forecasting.ticket_06_acceptance import (
    run_ticket_06_acceptance,
    run_ticket_06_source_probe,
)
from stock_forecasting.ticket_07_acceptance import run_ticket_07_acceptance
from stock_forecasting.ticket_08_acceptance import run_ticket_08_acceptance
from stock_forecasting.ticket_09_acceptance import run_ticket_09_acceptance


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("information cutoff must include a timezone")
    return parsed


def _local_key_lifetime_hours(value: str) -> int:
    try:
        hours = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("lifetime hours must be an integer") from error
    if not 1 <= hours <= 720:
        raise argparse.ArgumentTypeError("lifetime hours must be between 1 and 720")
    return hours


def _container_image_digest_from_environment() -> str | None:
    direct_digest = os.environ.get("P1_OCI_IMAGE_DIGEST")
    if direct_digest:
        return direct_digest
    digest_file = os.environ.get("P1_OCI_IMAGE_DIGEST_FILE")
    if digest_file:
        path = Path(digest_file)
        if path.is_file():
            digest = path.read_text(encoding="utf-8").strip()
            return digest or None
    return None


def _preserve_previous_bundle_input(export_directory: Path) -> Path | None:
    stable_path = export_directory / "p1-acceptance-bundle.json"
    if not stable_path.is_file():
        return None
    content = stable_path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    preserved_path = export_directory / "previous" / "sha256" / checksum[:2] / checksum
    preserved_path.parent.mkdir(parents=True, exist_ok=True)
    preserved_path.write_bytes(content)
    return stable_path


def _export_failure_evidence_objects(
    *,
    bundle: dict[str, object],
    object_root: Path,
    export_directory: Path,
) -> None:
    preserved: dict[str, str] = {}
    preserved_scenarios: set[str] = set()
    required_probe_scenarios: set[str] = set()
    failure_evidence = bundle.get("failure_evidence")
    if isinstance(failure_evidence, list):
        for result in failure_evidence:
            if not isinstance(result, dict):
                continue
            scenario = result.get("scenario")
            evidence_ids = result.get("evidence_ids")
            if scenario in {"checksum_failure", "stale_fencing"}:
                required_probe_scenarios.add(str(scenario))
            if not isinstance(scenario, str) or not isinstance(evidence_ids, list):
                continue
            for evidence_id in evidence_ids:
                if not is_sha256_reference(evidence_id):
                    continue
                checksum = evidence_id.removeprefix("sha256:")
                source_path = object_root / "sha256" / checksum[:2] / checksum
                if not source_path.is_file():
                    continue
                content = source_path.read_bytes()
                if hashlib.sha256(content).hexdigest() != checksum:
                    raise RuntimeError("acceptance_evidence_object_checksum_mismatch")
                relative_path = Path("objects") / "sha256" / checksum[:2] / checksum
                destination = export_directory / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                preserved[relative_path.as_posix()] = checksum
                preserved_scenarios.add(scenario)

    previous_root = export_directory / "previous" / "sha256"
    if previous_root.is_dir():
        for preserved_input in previous_root.glob("*/*"):
            if not preserved_input.is_file():
                continue
            checksum = preserved_input.name
            if hashlib.sha256(preserved_input.read_bytes()).hexdigest() != checksum:
                raise RuntimeError("previous_acceptance_bundle_checksum_mismatch")
            preserved[preserved_input.relative_to(export_directory).as_posix()] = checksum

    if not required_probe_scenarios <= preserved_scenarios:
        raise RuntimeError("acceptance_probe_export_missing")
    manifest = "".join(
        f"{checksum}  {relative_path}\n" for relative_path, checksum in sorted(preserved.items())
    )
    (export_directory / "p1-evidence-objects.sha256").write_text(
        manifest,
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-forecasting")
    commands = parser.add_subparsers(dest="command", required=True)
    acceptance = commands.add_parser("acceptance")
    acceptance.add_argument(
        "ticket",
        choices=[
            "ticket-01",
            "ticket-02",
            "ticket-03",
            "ticket-04",
            "ticket-05",
            "ticket-06",
            "ticket-06-source-probe",
            "ticket-07",
            "ticket-08",
            "ticket-09",
        ],
    )
    acceptance.add_argument("--database-url", required=True)
    acceptance.add_argument("--object-root", type=Path, required=True)
    acceptance.add_argument("--information-cutoff", type=_instant, required=True)
    acceptance.add_argument("--observed-at", type=_instant, required=True)
    acceptance.add_argument("--base-url")
    acceptance.add_argument("--dagster-url")
    acceptance.add_argument("--denied-base-url")
    acceptance.add_argument("--key-file", type=Path)
    acceptance.add_argument("--source-adapter-key-file", type=Path)
    acceptance.add_argument("--source-secret-root", type=Path)
    acceptance.add_argument("--project-root", type=Path, default=Path.cwd())
    acceptance.add_argument("--git-dir", type=Path, default=Path.cwd() / ".git")
    acceptance.add_argument("--previous-bundle-reference")
    acceptance.add_argument(
        "--platform-name",
        choices=["windows_docker_desktop", "linux_ci"],
        default=os.environ.get("P1_ACCEPTANCE_PLATFORM"),
    )
    acceptance.add_argument(
        "--container-image-digest",
        default=_container_image_digest_from_environment(),
    )
    acceptance.add_argument(
        "--counterpart-bundle",
        type=Path,
        default=(Path(value) if (value := os.environ.get("P1_COUNTERPART_BUNDLE")) else None),
    )
    acceptance.add_argument(
        "--evidence-export-dir",
        type=Path,
        default=(Path(value) if (value := os.environ.get("P1_ACCEPTANCE_EXPORT_DIR")) else None),
    )
    relay = commands.add_parser("relay")
    relay.add_argument("--once", action="store_true")
    local_key = commands.add_parser("local-key")
    local_key_commands = local_key.add_subparsers(dest="local_key_command", required=True)
    local_key_init = local_key_commands.add_parser("init")
    local_key_init.add_argument("--path", type=Path, required=True)
    local_key_init.add_argument("--owner", required=True)
    local_key_init.add_argument(
        "--environment",
        choices=["local", "development"],
        required=True,
    )
    local_key_init.add_argument(
        "--scope",
        action="append",
        choices=[
            "fixture_pipeline.execute",
            "research_prediction.read",
            "market_data.collect",
            "price_research_eligibility.read",
            "source_credential.read",
            "source_credential.manage",
            "model_governance.read",
            "model_governance.approve",
        ],
        required=True,
    )
    local_key_init.add_argument(
        "--data-protection-class",
        action="append",
        choices=["public_source", "internal", "licensed", "restricted", "secret"],
    )
    local_key_init.add_argument(
        "--principal-classification",
        choices=[
            "individual_non_commercial",
            "individual_or_internal_group",
            "commercial",
        ],
    )
    local_key_init.add_argument("--issued-at", type=_instant)
    local_key_init.add_argument("--expires-at", type=_instant)
    local_key_init.add_argument("--lifetime-hours", type=_local_key_lifetime_hours)
    authorization = commands.add_parser("authorization")
    authorization_commands = authorization.add_subparsers(
        dest="authorization_command", required=True
    )
    authorization_init = authorization_commands.add_parser("init-fixtures")
    authorization_init.add_argument("--database-url", required=True)
    authorization_init.add_argument("--key-file", type=Path, required=True)
    authorization_init.add_argument("--platform-admin-key-file", type=Path, required=True)
    ticket_06_authorization = authorization_commands.add_parser("init-ticket-06")
    ticket_06_authorization.add_argument("--database-url", required=True)
    ticket_06_authorization.add_argument("--key-file", type=Path, required=True)
    ticket_06_authorization.add_argument(
        "--source-adapter-key-file",
        type=Path,
        required=True,
    )
    ticket_07_authorization = authorization_commands.add_parser("init-ticket-07")
    ticket_07_authorization.add_argument("--database-url", required=True)
    ticket_07_authorization.add_argument("--key-file", type=Path, required=True)
    ticket_07_authorization.add_argument(
        "--source-adapter-key-file",
        type=Path,
        required=True,
    )
    ticket_08_authorization = authorization_commands.add_parser("init-ticket-08")
    ticket_08_authorization.add_argument("--database-url", required=True)
    ticket_08_authorization.add_argument("--key-file", type=Path, required=True)
    ticket_09_authorization = authorization_commands.add_parser("init-ticket-09")
    ticket_09_authorization.add_argument("--database-url", required=True)
    ticket_09_authorization.add_argument("--key-file", type=Path, required=True)
    operator_authorization = authorization_commands.add_parser("init-operator")
    operator_authorization.add_argument("--database-url", required=True)
    operator_authorization.add_argument("--key-file", type=Path, required=True)
    operator_authorization.add_argument(
        "--source-adapter-key-file",
        type=Path,
        required=True,
    )
    operator = commands.add_parser("operator")
    operator_commands = operator.add_subparsers(dest="operator_command", required=True)
    source_credentials = operator_commands.add_parser("source-credentials")
    source_credential_commands = source_credentials.add_subparsers(
        dest="source_credential_command",
        required=True,
    )
    configure_source_credential = source_credential_commands.add_parser("configure")
    configure_source_credential.add_argument("--provider", required=True)
    source_credential_commands.add_parser("status")
    validate_source_credential = source_credential_commands.add_parser("validate")
    validate_source_credential.add_argument("--provider", required=True)
    return parser


def _operator_request(
    *,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    base_url = os.environ.get("OPERATOR_BASE_URL")
    key_file_text = os.environ.get("LOCAL_API_KEY_FILE")
    if not base_url:
        raise RuntimeError("OPERATOR_BASE_URL is required")
    if not key_file_text:
        raise RuntimeError("LOCAL_API_KEY_FILE is required")
    identity = LocalApiKeyIdentity.load(Path(key_file_text))
    body = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if payload is not None
        else None
    )
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": identity.credential.authorization_header(),
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    with urlopen(request, timeout=10.0) as response:
        decoded = json.loads(response.read())
    if not isinstance(decoded, dict):
        raise RuntimeError("operator_response_invalid")
    return decoded


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if (
        arguments.command == "operator"
        and arguments.operator_command == "source-credentials"
        and arguments.source_credential_command == "validate"
    ):
        try:
            result = _operator_request(
                method="POST",
                path=(
                    "/api/v1/operations/source-credentials/"
                    f"{quote(arguments.provider, safe='')}/validations"
                ),
            )
        except HTTPError as error:
            problem = json.loads(error.read())
            if not isinstance(problem, dict):
                raise RuntimeError("operator_response_invalid") from error
            print(json.dumps(problem, ensure_ascii=False, sort_keys=True))
            return 3
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if (
        arguments.command == "operator"
        and arguments.operator_command == "source-credentials"
        and arguments.source_credential_command == "status"
    ):
        result = _operator_request(
            method="GET",
            path="/api/v1/operations/source-credentials",
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if (
        arguments.command == "operator"
        and arguments.operator_command == "source-credentials"
        and arguments.source_credential_command == "configure"
    ):
        listing = _operator_request(
            method="GET",
            path="/api/v1/operations/source-credentials",
        )
        items = listing.get("items")
        if not isinstance(items, list):
            raise RuntimeError("operator_source_credential_listing_invalid")
        provider = next(
            (
                item
                for item in items
                if isinstance(item, dict) and item.get("provider_id") == arguments.provider
            ),
            None,
        )
        if provider is None:
            raise RuntimeError("operator_source_provider_not_found")
        required_fields = provider.get("required_fields")
        if not isinstance(required_fields, list) or not all(
            isinstance(field_name, str) and field_name for field_name in required_fields
        ):
            raise RuntimeError("operator_source_credential_contract_invalid")
        credential_fields = {
            field_name: getpass(f"{arguments.provider} {field_name}: ")
            for field_name in required_fields
        }
        try:
            result = _operator_request(
                method="PUT",
                path=(
                    f"/api/v1/operations/source-credentials/{quote(arguments.provider, safe='')}"
                ),
                payload={"credential_fields": credential_fields},
            )
        finally:
            credential_fields.clear()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if arguments.command == "acceptance" and arguments.ticket in {
        "ticket-01",
        "ticket-02",
        "ticket-03",
        "ticket-04",
        "ticket-05",
        "ticket-06",
        "ticket-06-source-probe",
        "ticket-07",
        "ticket-08",
        "ticket-09",
    }:
        if arguments.ticket not in {
            "ticket-06",
            "ticket-06-source-probe",
            "ticket-07",
            "ticket-08",
            "ticket-09",
        } and ((arguments.base_url is None) != (arguments.dagster_url is None)):
            parser.error("--base-url and --dagster-url must be provided together")
        if arguments.ticket in {"ticket-06", "ticket-07", "ticket-08", "ticket-09"}:
            if arguments.base_url is None or arguments.key_file is None:
                parser.error(f"{arguments.ticket} requires --base-url and --key-file")
            if arguments.ticket == "ticket-07" and (
                arguments.source_secret_root is None or arguments.source_adapter_key_file is None
            ):
                parser.error(
                    f"{arguments.ticket} requires --source-secret-root and "
                    "--source-adapter-key-file"
                )
            if arguments.ticket == "ticket-06":
                report = run_ticket_06_acceptance(
                    base_url=arguments.base_url,
                    key_file=arguments.key_file,
                )
            elif arguments.ticket == "ticket-07":
                report = run_ticket_07_acceptance(
                    database_url=arguments.database_url,
                    object_root=arguments.object_root,
                    base_url=arguments.base_url,
                    key_file=arguments.key_file,
                    source_adapter_key_file=arguments.source_adapter_key_file,
                    source_secret_root=arguments.source_secret_root,
                )
            elif arguments.ticket == "ticket-08":
                report = run_ticket_08_acceptance(
                    database_url=arguments.database_url,
                    object_root=arguments.object_root,
                    observed_at=arguments.observed_at,
                    base_url=arguments.base_url,
                    key_file=arguments.key_file,
                )
            else:
                report = run_ticket_09_acceptance(
                    database_url=arguments.database_url,
                    object_root=arguments.object_root,
                    observed_at=arguments.observed_at,
                    base_url=arguments.base_url,
                    key_file=arguments.key_file,
                )
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0 if report["status"] == "passed" else 1
        if arguments.ticket == "ticket-06-source-probe":
            if arguments.source_adapter_key_file is None or arguments.source_secret_root is None:
                parser.error(
                    "ticket-06-source-probe requires --source-adapter-key-file and "
                    "--source-secret-root"
                )
            report = run_ticket_06_source_probe(
                database_url=arguments.database_url,
                object_root=arguments.object_root,
                source_adapter_key_file=arguments.source_adapter_key_file,
                source_secret_root=arguments.source_secret_root,
            )
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0 if report["status"] == "passed" else 1
        runners: dict[str, Callable[..., dict[str, object]]] = {
            "ticket-01": run_ticket_01,
            "ticket-02": run_ticket_02,
            "ticket-03": run_ticket_03,
            "ticket-04": run_ticket_04,
            "ticket-05": run_ticket_05,
        }
        runner = runners[arguments.ticket]
        runner_arguments = {
            "database_url": arguments.database_url,
            "object_root": arguments.object_root,
            "information_cutoff": arguments.information_cutoff,
            "observed_at": arguments.observed_at,
            "base_url": arguments.base_url,
            "dagster_url": arguments.dagster_url,
        }
        if arguments.ticket in {"ticket-04", "ticket-05"}:
            runner_arguments["denied_base_url"] = arguments.denied_base_url
        elif arguments.denied_base_url is not None:
            parser.error("--denied-base-url is only valid for ticket-04 or ticket-05")
        if arguments.ticket == "ticket-05":
            previous_bundle_path = None
            if arguments.evidence_export_dir is not None:
                previous_bundle_path = _preserve_previous_bundle_input(
                    arguments.evidence_export_dir
                )
            runner_arguments.update(
                {
                    "project_root": arguments.project_root,
                    "git_dir": arguments.git_dir,
                    "previous_bundle_reference": arguments.previous_bundle_reference,
                    "previous_bundle_path": (
                        previous_bundle_path
                        if arguments.previous_bundle_reference is None
                        else None
                    ),
                    "platform_name": arguments.platform_name,
                    "container_image_digest": arguments.container_image_digest,
                    "counterpart_bundle": arguments.counterpart_bundle,
                }
            )
        report = runner(
            **runner_arguments,
        )
        if arguments.ticket == "ticket-05" and arguments.evidence_export_dir is not None:
            export_directory = arguments.evidence_export_dir
            export_directory.mkdir(parents=True, exist_ok=True)
            bundle_report = report["bundle"]
            if not isinstance(bundle_report, dict):
                raise RuntimeError("acceptance_bundle_report_invalid")
            bundle_content = Path(str(bundle_report["uri"])).read_bytes()
            bundle_checksum = hashlib.sha256(bundle_content).hexdigest()
            if bundle_checksum != bundle_report["checksum"]:
                raise RuntimeError("acceptance_bundle_export_checksum_mismatch")
            content_path = export_directory / f"{bundle_checksum}.p1-acceptance.json"
            stable_path = export_directory / "p1-acceptance-bundle.json"
            checksum_path = export_directory / "p1-acceptance-bundle.json.sha256"
            content_path.write_bytes(bundle_content)
            stable_path.write_bytes(bundle_content)
            checksum_path.write_text(
                f"{bundle_checksum}  p1-acceptance-bundle.json\n",
                encoding="utf-8",
            )
            bundle_payload = json.loads(bundle_content)
            if not isinstance(bundle_payload, dict):
                raise RuntimeError("acceptance_bundle_export_invalid")
            _export_failure_evidence_objects(
                bundle=bundle_payload,
                object_root=arguments.object_root,
                export_directory=export_directory,
            )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        run_status = report.get("platform_run_status", report["status"])
        return 0 if run_status == "passed" else 1
    if arguments.command == "relay":
        if not arguments.once:
            parser.error("relay currently requires --once")
        outcome = RuntimeSettings.from_environment().build_application().relay_outbox()
        print(json.dumps(asdict(outcome), ensure_ascii=False, sort_keys=True))
        return 1 if outcome.status in {"failed", "deferred", "isolated"} else 0
    if arguments.command == "local-key" and arguments.local_key_command == "init":
        if (arguments.issued_at is None) != (arguments.expires_at is None):
            parser.error("--issued-at and --expires-at must be provided together")
        if arguments.issued_at is not None and arguments.lifetime_hours is not None:
            parser.error("--lifetime-hours cannot be combined with explicit times")
        generated_at = datetime.now(UTC)
        issued_at = arguments.issued_at or generated_at
        expires_at = arguments.expires_at or generated_at + timedelta(
            hours=arguments.lifetime_hours or 24
        )
        status = "initialized"
        if arguments.path.exists():
            identity = LocalApiKeyIdentity.load(arguments.path)
            explicit_times_conflict = arguments.issued_at is not None and (
                identity.context.issued_at != issued_at or identity.context.expires_at != expires_at
            )
            explicit_classes_conflict = (
                arguments.data_protection_class is not None
                and identity.context.data_protection_classes
                != frozenset(arguments.data_protection_class)
            )
            explicit_principal_classification_conflict = (
                arguments.principal_classification is not None
                and identity.context.principal_classification != arguments.principal_classification
            )
            explicit_lifetime_conflict = arguments.lifetime_hours is not None and (
                identity.context.expires_at - identity.context.issued_at
                != timedelta(hours=arguments.lifetime_hours)
            )
            if (
                identity.context.owner != arguments.owner
                or identity.context.environment != arguments.environment
                or identity.context.scopes != frozenset(arguments.scope)
                or explicit_times_conflict
                or explicit_classes_conflict
                or explicit_principal_classification_conflict
                or explicit_lifetime_conflict
                or identity.context.expires_at <= generated_at
            ):
                raise RuntimeError("local_api_key_file_conflict")
            status = "existing"
        else:
            identity = LocalApiKeyIdentity.issue(
                owner=arguments.owner,
                environment=arguments.environment,
                scopes=set(arguments.scope),
                issued_at=issued_at,
                expires_at=expires_at,
                data_protection_classes=(
                    set(arguments.data_protection_class)
                    if arguments.data_protection_class is not None
                    else None
                ),
                principal_classification=arguments.principal_classification,
            )
            identity.save(arguments.path)
        print(json.dumps({"status": status}, sort_keys=True))
        return 0
    if arguments.command == "authorization" and arguments.authorization_command == (
        "init-fixtures"
    ):
        identity = LocalApiKeyIdentity.load(arguments.key_file)
        if arguments.platform_admin_key_file.exists():
            platform_admin = LocalApiKeyIdentity.load(arguments.platform_admin_key_file)
        else:
            platform_admin = LocalApiKeyIdentity.issue(
                owner="platform-admin",
                environment=identity.context.environment,
                scopes={"fixture_pipeline.execute", "research_prediction.read"},
                issued_at=identity.context.issued_at,
                expires_at=identity.context.expires_at,
            )
            platform_admin.save(arguments.platform_admin_key_file)
        repository = AuthorizationPolicyRepository(
            StateStore(arguments.database_url, create_schema=False)
        )
        catalog = fixture_authorization_policy_catalog(identity.context)
        for policy_set_id, policy in catalog.items():
            repository.install(policy_set_id, policy)
        repository.install(
            FIXTURE_REVOKED_POLICY_SET,
            fixture_authorization_policy_catalog(platform_admin.context)[
                FIXTURE_REVOKED_POLICY_SET
            ],
        )
        print(
            json.dumps(
                {"status": "initialized", "policy_set_count": len(catalog)},
                sort_keys=True,
            )
        )
        return 0
    if arguments.command == "authorization" and arguments.authorization_command == (
        "init-ticket-06"
    ):
        identity = LocalApiKeyIdentity.load(arguments.key_file)
        source_adapter_identity = LocalApiKeyIdentity.load(arguments.source_adapter_key_file)
        repository = AuthorizationPolicyRepository(
            StateStore(arguments.database_url, create_schema=False)
        )
        repository.install(
            TICKET_06_FINMIND_ENGINEERING_POLICY_SET,
            build_taiwan_finmind_engineering_authorization_policy(identity.context),
        )
        repository.install(
            TICKET_06_FINMIND_ENGINEERING_POLICY_SET,
            build_taiwan_finmind_engineering_authorization_policy(source_adapter_identity.context),
        )
        print(json.dumps({"status": "initialized", "policy_set_count": 2}, sort_keys=True))
        return 0
    if arguments.command == "authorization" and arguments.authorization_command == (
        "init-ticket-07"
    ):
        identity = LocalApiKeyIdentity.load(arguments.key_file)
        source_adapter_identity = LocalApiKeyIdentity.load(arguments.source_adapter_key_file)
        repository = AuthorizationPolicyRepository(
            StateStore(arguments.database_url, create_schema=False)
        )
        repository.install(
            TICKET_07_ENGINEERING_POLICY_SET,
            build_us_zero_fee_engineering_authorization_policy(identity.context),
        )
        repository.install(
            TICKET_07_ENGINEERING_POLICY_SET,
            build_us_zero_fee_engineering_authorization_policy(source_adapter_identity.context),
        )
        print(json.dumps({"status": "initialized", "policy_set_count": 2}, sort_keys=True))
        return 0
    if arguments.command == "authorization" and arguments.authorization_command == (
        "init-ticket-08"
    ):
        identity = LocalApiKeyIdentity.load(arguments.key_file)
        repository = AuthorizationPolicyRepository(
            StateStore(arguments.database_url, create_schema=False)
        )
        repository.install(
            TICKET_08_ENGINEERING_POLICY_SET,
            build_historical_reconstruction_engineering_authorization_policy(identity.context),
        )
        print(json.dumps({"status": "initialized", "policy_set_count": 1}, sort_keys=True))
        return 0
    if arguments.command == "authorization" and arguments.authorization_command == (
        "init-ticket-09"
    ):
        identity = LocalApiKeyIdentity.load(arguments.key_file)
        repository = AuthorizationPolicyRepository(
            StateStore(arguments.database_url, create_schema=False)
        )
        repository.install(
            TICKET_09_ENGINEERING_POLICY_SET,
            build_fixture_authorization_policy(identity.context),
        )
        print(json.dumps({"status": "initialized", "policy_set_count": 1}, sort_keys=True))
        return 0
    if arguments.command == "authorization" and arguments.authorization_command == (
        "init-operator"
    ):
        identity = LocalApiKeyIdentity.load(arguments.key_file)
        source_adapter_identity = LocalApiKeyIdentity.load(arguments.source_adapter_key_file)
        repository = AuthorizationPolicyRepository(
            StateStore(arguments.database_url, create_schema=False)
        )
        repository.install(
            TICKET_09_OWNER_OPERATOR_POLICY_SET,
            build_pending_rights_operator_authorization_policy(identity.context),
        )
        repository.install(
            TICKET_09_OWNER_OPERATOR_POLICY_SET,
            build_pending_rights_operator_authorization_policy(source_adapter_identity.context),
        )
        print(json.dumps({"status": "initialized", "policy_set_count": 2}, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

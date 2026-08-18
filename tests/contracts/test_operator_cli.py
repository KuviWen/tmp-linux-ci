from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from stock_forecasting.authorization import LocalApiKeyIdentity
from stock_forecasting.cli import main


class JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> JsonResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_operator_cli_configures_write_only_fields_from_hidden_prompts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="owner-local",
        environment="local",
        scopes={"source_credential.read", "source_credential.manage"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"restricted", "secret"},
    )
    key_file = tmp_path / "owner-api-key.json"
    identity.save(key_file)
    secret = "hidden-finmind-token"
    requests: list[Request] = []

    def fake_urlopen(request: Request, *, timeout: float) -> JsonResponse:
        assert timeout == 10.0
        requests.append(request)
        if request.get_method() == "GET":
            return JsonResponse(
                {
                    "items": [
                        {
                            "provider_id": "finmind-free-api",
                            "required_fields": ["token"],
                            "readiness": "missing",
                            "reason_code": "source_credential_missing",
                        }
                    ]
                }
            )
        return JsonResponse(
            {
                "provider_id": "finmind-free-api",
                "readiness": "configured",
                "reason_code": "source_credential_not_validated",
                "version": 1,
            }
        )

    monkeypatch.setenv("OPERATOR_BASE_URL", "http://ticket-09-operator-api:8080")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.setattr("stock_forecasting.cli.urlopen", fake_urlopen, raising=False)
    monkeypatch.setattr("stock_forecasting.cli.getpass", lambda prompt: secret, raising=False)

    exit_code = main(
        [
            "operator",
            "source-credentials",
            "configure",
            "--provider",
            "finmind-free-api",
        ]
    )

    assert exit_code == 0
    assert [request.get_method() for request in requests] == ["GET", "PUT"]
    configured_request = requests[1]
    assert configured_request.full_url.endswith(
        "/api/v1/operations/source-credentials/finmind-free-api"
    )
    assert configured_request.headers["Authorization"] == (
        identity.credential.authorization_header()
    )
    assert isinstance(configured_request.data, bytes)
    assert json.loads(configured_request.data) == {"credential_fields": {"token": secret}}
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "provider_id": "finmind-free-api",
        "readiness": "configured",
        "reason_code": "source_credential_not_validated",
        "version": 1,
    }
    assert secret not in output


def test_operator_cli_reports_credential_status_without_secret_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="owner-local",
        environment="local",
        scopes={"source_credential.read"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"restricted"},
    )
    key_file = tmp_path / "owner-api-key.json"
    identity.save(key_file)
    response_payload: dict[str, object] = {
        "items": [
            {
                "provider_id": "finmind-free-api",
                "readiness": "configured",
                "reason_code": "source_credential_not_validated",
                "version": 1,
            },
            {
                "provider_id": "alpaca-market-data-basic",
                "readiness": "configured",
                "reason_code": "source_credential_not_validated",
                "version": 1,
            },
        ]
    }
    requests: list[Request] = []

    def fake_urlopen(request: Request, *, timeout: float) -> JsonResponse:
        requests.append(request)
        return JsonResponse(response_payload)

    monkeypatch.setenv("OPERATOR_BASE_URL", "http://ticket-09-operator-api:8080")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.setattr("stock_forecasting.cli.urlopen", fake_urlopen)

    exit_code = main(["operator", "source-credentials", "status"])

    assert exit_code == 0
    assert len(requests) == 1
    assert requests[0].get_method() == "GET"
    assert json.loads(capsys.readouterr().out) == response_payload


def test_operator_cli_surfaces_policy_blocked_validation_as_a_nonzero_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)
    identity = LocalApiKeyIdentity.issue(
        owner="owner-local",
        environment="local",
        scopes={"source_credential.manage"},
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=23),
        data_protection_classes={"restricted"},
    )
    key_file = tmp_path / "owner-api-key.json"
    identity.save(key_file)
    problem = {
        "status": 403,
        "code": "authorization_denied",
        "detail": "The requested operation is not authorized.",
        "trace_id": "trace-operator-validation",
    }
    requests: list[Request] = []

    def fake_urlopen(request: Request, *, timeout: float) -> JsonResponse:
        requests.append(request)
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=Message(),
            fp=BytesIO(json.dumps(problem).encode("utf-8")),
        )

    monkeypatch.setenv("OPERATOR_BASE_URL", "http://ticket-09-operator-api:8080")
    monkeypatch.setenv("LOCAL_API_KEY_FILE", str(key_file))
    monkeypatch.setattr("stock_forecasting.cli.urlopen", fake_urlopen)

    exit_code = main(
        [
            "operator",
            "source-credentials",
            "validate",
            "--provider",
            "finmind-free-api",
        ]
    )

    assert exit_code == 3
    assert len(requests) == 1
    assert requests[0].get_method() == "POST"
    assert requests[0].full_url.endswith(
        "/api/v1/operations/source-credentials/finmind-free-api/validations"
    )
    assert json.loads(capsys.readouterr().out) == problem

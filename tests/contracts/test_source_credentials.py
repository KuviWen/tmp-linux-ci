from __future__ import annotations

import pickle
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_forecasting.source_credentials import (
    EncryptedFilesystemSecretProvider,
    SecretUseContext,
)


def _use_context(*, issued_at: datetime) -> SecretUseContext:
    return SecretUseContext(
        workload_principal_id="workload:alpaca-source-adapter",
        environment="development",
        source_id="alpaca-us-stock-bars",
        destination="data.alpaca.markets",
        purpose="price_research_ingest",
        request_id="request-ticket-07-secret-lease",
        work_id="work-ticket-07-secret-lease",
        credential_version=3,
        lease_duration=timedelta(minutes=5),
        lease_not_before=issued_at,
        lease_expires_at=issued_at + timedelta(minutes=5),
    )


def test_encrypted_filesystem_secret_provider_survives_restart_without_plaintext(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "source-secrets"
    credential_fields = {
        "api_key_id": "ticket-07-key-id",
        "api_secret_key": "ticket-07-secret-value",
    }

    issued_at = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
    monotonic_time = [100.0]
    provider = EncryptedFilesystemSecretProvider(
        secret_root,
        clock=lambda: issued_at,
        monotonic_clock=lambda: monotonic_time[0],
    )
    secret_ref = provider.put(
        provider_id="alpaca-market-data-basic",
        credential_fields=credential_fields,
    )

    persisted_bytes = b"".join(
        path.read_bytes() for path in secret_root.iterdir() if path.is_file()
    )
    assert b"ticket-07-key-id" not in persisted_bytes
    assert b"ticket-07-secret-value" not in persisted_bytes

    restarted_provider = EncryptedFilesystemSecretProvider(
        secret_root,
        clock=lambda: issued_at,
        monotonic_clock=lambda: monotonic_time[0],
    )
    lease = restarted_provider.checkout(secret_ref, _use_context(issued_at=issued_at))
    assert lease.secret_ref_id == secret_ref.secret_ref_id
    assert lease.credential_version == 3
    assert lease.issued_at == issued_at
    assert lease.expires_at == issued_at + timedelta(minutes=5)
    assert lease.purpose == "price_research_ingest"
    assert lease.revoked is False
    assert lease.credential_fields() == credential_fields
    assert "ticket-07-key-id" not in repr(lease)
    assert "ticket-07-secret-value" not in repr(lease)
    with pytest.raises(TypeError, match="secret_lease_not_serializable"):
        pickle.dumps(lease)

    with pytest.raises(AttributeError):
        lease.expires_at = issued_at + timedelta(days=1)  # type: ignore[misc]

    monotonic_time[0] += 300
    with pytest.raises(KeyError, match="source_credential_lease_expired"):
        lease.credential_fields()

    lease.revoke()
    assert lease.revoked is True
    with pytest.raises(KeyError, match="source_credential_lease_revoked"):
        lease.credential_fields()

    restarted_provider.revoke(secret_ref)
    with pytest.raises(KeyError, match="source_credential_secret_unavailable"):
        restarted_provider.checkout(secret_ref, _use_context(issued_at=issued_at))

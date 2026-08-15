from __future__ import annotations

from pathlib import Path

import pytest

from stock_forecasting.source_credentials import EncryptedFilesystemSecretProvider


def test_encrypted_filesystem_secret_provider_survives_restart_without_plaintext(
    tmp_path: Path,
) -> None:
    secret_root = tmp_path / "source-secrets"
    credential_fields = {
        "api_key_id": "ticket-07-key-id",
        "api_secret_key": "ticket-07-secret-value",
    }

    provider = EncryptedFilesystemSecretProvider(secret_root)
    secret_ref = provider.put(
        provider_id="alpaca-market-data-basic",
        credential_fields=credential_fields,
    )

    persisted_bytes = b"".join(
        path.read_bytes() for path in secret_root.iterdir() if path.is_file()
    )
    assert b"ticket-07-key-id" not in persisted_bytes
    assert b"ticket-07-secret-value" not in persisted_bytes

    restarted_provider = EncryptedFilesystemSecretProvider(secret_root)
    lease = restarted_provider.checkout(secret_ref.secret_ref_id)
    assert lease.credential_fields() == credential_fields
    assert "ticket-07-key-id" not in repr(lease)
    assert "ticket-07-secret-value" not in repr(lease)

    restarted_provider.revoke(secret_ref.secret_ref_id)
    with pytest.raises(KeyError, match="source_credential_secret_unavailable"):
        restarted_provider.checkout(secret_ref.secret_ref_id)

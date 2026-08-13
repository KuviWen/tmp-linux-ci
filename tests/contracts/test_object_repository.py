from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from stock_forecasting.platform.object_repository import (
    FilesystemObjectRepository,
    ObjectIntegrityError,
)


def test_filesystem_object_repository_is_content_addressed_and_detects_corruption(
    tmp_path: Path,
) -> None:
    repository = FilesystemObjectRepository(tmp_path)
    content = b'{"fixture":"XTAI","sessions":253}'
    checksum = hashlib.sha256(content).hexdigest()

    first = repository.put_verified(
        BytesIO(content),
        expected_checksum=checksum,
        metadata={"media_type": "application/json", "source": "fixture"},
    )
    duplicate = repository.put_verified(
        BytesIO(content),
        expected_checksum=checksum,
        metadata={"media_type": "application/json", "source": "fixture"},
    )

    assert duplicate == first
    assert repository.open(first).read() == content
    assert repository.stat(first) == {
        "checksum": checksum,
        "size": len(content),
        "metadata": {"media_type": "application/json", "source": "fixture"},
    }

    Path(first.uri).write_bytes(b"corrupt")
    with pytest.raises(ObjectIntegrityError, match="checksum_mismatch"):
        repository.open(first)

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO


class ObjectIntegrityError(RuntimeError):
    """Raised when immutable object bytes do not match their declared checksum."""


@dataclass(frozen=True)
class ObjectRef:
    object_id: str
    checksum: str
    uri: str


class FilesystemObjectRepository:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put_verified(
        self,
        stream: BinaryIO,
        *,
        expected_checksum: str,
        metadata: Mapping[str, str],
    ) -> ObjectRef:
        content = stream.read()
        actual_checksum = hashlib.sha256(content).hexdigest()
        if actual_checksum != expected_checksum:
            raise ObjectIntegrityError("checksum_mismatch")

        object_path = self._object_path(actual_checksum)
        metadata_path = object_path.with_suffix(".metadata.json")
        object_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_once(object_path, content)
        self._write_once(
            metadata_path,
            json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )
        return ObjectRef(
            object_id=f"sha256:{actual_checksum}",
            checksum=actual_checksum,
            uri=str(object_path),
        )

    def open(self, reference: ObjectRef) -> BytesIO:
        content = Path(reference.uri).read_bytes()
        if hashlib.sha256(content).hexdigest() != reference.checksum:
            raise ObjectIntegrityError("checksum_mismatch")
        return BytesIO(content)

    def stat(self, reference: ObjectRef) -> dict[str, object]:
        content = self.open(reference).read()
        metadata_path = Path(reference.uri).with_suffix(".metadata.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return {
            "checksum": reference.checksum,
            "size": len(content),
            "metadata": metadata,
        }

    def _object_path(self, checksum: str) -> Path:
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise ValueError("invalid_sha256")
        return self._root / "sha256" / checksum[:2] / checksum

    @staticmethod
    def _write_once(path: Path, content: bytes) -> None:
        if path.exists():
            return
        descriptor, temporary_name = tempfile.mkstemp(dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                temporary_path.unlink()
            else:
                os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

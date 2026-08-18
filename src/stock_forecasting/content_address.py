from __future__ import annotations

import hashlib
import json


def canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json_bytes(payload: object) -> bytes:
    return canonical_json(payload).encode("utf-8")


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_id(content: bytes) -> str:
    return f"sha256:{sha256_hex(content)}"


def content_id(kind: str, payload: object) -> str:
    serialized = canonical_json_bytes(payload)
    return sha256_id(kind.encode() + serialized)

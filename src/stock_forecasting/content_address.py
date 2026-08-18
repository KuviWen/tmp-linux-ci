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


def content_id(kind: str, payload: object) -> str:
    serialized = canonical_json(payload).encode("utf-8")
    return f"sha256:{hashlib.sha256(kind.encode() + serialized).hexdigest()}"

from __future__ import annotations

import hashlib
import json


def content_id(kind: str, payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(kind.encode() + serialized).hexdigest()}"

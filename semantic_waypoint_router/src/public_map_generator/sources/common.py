from __future__ import annotations

from io import BytesIO
from pathlib import Path

import requests


class PublicDataError(RuntimeError):
    """Error claro al acceder o interpretar una fuente pública."""


def request_bytes(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 180,
) -> bytes:
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    content = response.content
    prefix = content[:300].lower()
    content_type = response.headers.get("content-type", "").lower()
    if b"serviceexception" in prefix or b"exceptionreport" in prefix:
        raise PublicDataError(content[:2000].decode("utf-8", errors="replace"))
    if "xml" in content_type and (b"exception" in prefix or b"error" in prefix):
        raise PublicDataError(content[:2000].decode("utf-8", errors="replace"))
    return content


def save_bytes(path: str | Path, payload: bytes) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    return out


def as_bytes_io(payload: bytes) -> BytesIO:
    stream = BytesIO(payload)
    stream.seek(0)
    return stream

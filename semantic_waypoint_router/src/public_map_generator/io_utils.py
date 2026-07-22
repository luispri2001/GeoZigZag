from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def ensure_empty_directory(path: str | Path, overwrite: bool = False) -> Path:
    directory = Path(path)
    if directory.exists() and any(directory.iterdir()):
        if not overwrite:
            raise FileExistsError(
                f"El directorio {directory} ya contiene datos. "
                "Activa output.overwrite para sustituirlo."
            )
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_json(path: str | Path, payload: Any) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    return out


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

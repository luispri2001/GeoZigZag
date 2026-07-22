from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import AppConfig
from .pipeline import generate_map

JOB_ROOT = Path("outputs") / ".jobs"
TERMINAL_STATES = {"completed", "error", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_job(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_job(path: str | Path) -> dict[str, Any]:
    job_path = Path(path)
    try:
        return json.loads(job_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def start_job(config: AppConfig) -> Path:
    job_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    job_path = JOB_ROOT / f"{job_id}.json"
    log_path = JOB_ROOT / f"{job_id}.log"
    components = {
        "workspace": {"state": "pending", "message": "En cola"},
        "elevation": {"state": "pending", "message": "MDT05 IGN/CNIG"},
        "orthophoto": {"state": "pending", "message": "PNOA"},
        "osm": {"state": "pending", "message": "OpenStreetMap"},
        "surface_model": {"state": "pending", "message": "MDS IGN"},
        "sentinel2": {"state": "pending", "message": "NDVI/NDMI"},
        "terrain": {"state": "pending", "message": "Pendiente y relieve"},
        "semantic_risks": {"state": "pending", "message": "Riesgos inferidos"},
        "soil_moisture": {
            "state": "unavailable",
            "message": "No hay proveedor público de humedad del suelo configurado",
        },
        "recent_precipitation": {
            "state": "unavailable",
            "message": "No hay proveedor de precipitación implementado",
        },
    }
    for layer_id in [
        "terrain_elevation",
        "slope_degrees",
        "aspect_degrees",
        "roughness",
        "local_relief",
        "max_neighbor_step",
        "ndvi",
        "ndmi",
        "sentinel_scl",
        "osm_buildings",
        "osm_roads",
        "osm_water",
        "osm_waterways",
        "osm_wetlands",
        "osm_forest",
        "osm_farmland",
        "osm_grass",
        "osm_scrub",
        "osm_barriers",
        "surface_height",
        "wetness_prior",
        "vegetation_prior",
        "mud_risk",
        "water_accumulation_risk",
        "obstacle_probability",
        "traversability_prior",
        "confidence",
    ]:
        components[layer_id] = {"state": "pending", "message": "Pendiente"}
    payload = {
        "id": job_id,
        "state": "pending",
        "created_at": _now(),
        "updated_at": _now(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "output": config.output.directory,
        "config": config.model_dump(mode="json"),
        "components": components,
        "error": None,
        "log": str(log_path),
    }
    _write_job(job_path, payload)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "public_map_generator.jobs", "worker", str(job_path)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    payload["pid"] = process.pid
    payload["state"] = "running"
    payload["started_at"] = _now()
    payload["updated_at"] = _now()
    _write_job(job_path, payload)
    return job_path


def cancel_job(path: str | Path) -> bool:
    job_path = Path(path)
    payload = read_job(job_path)
    if not payload or payload.get("state") in TERMINAL_STATES:
        return False
    pid = payload.get("pid")
    if pid:
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    payload["state"] = "cancelled"
    payload["finished_at"] = _now()
    payload["updated_at"] = _now()
    _write_job(job_path, payload)
    return True


def job_progress(payload: dict[str, Any]) -> tuple[int, int, int]:
    components = payload.get("components", {})
    tracked = [item for item in components.values() if item.get("state") != "unavailable"]
    completed = sum(item.get("state") == "available" for item in tracked)
    errors = sum(item.get("state") == "error" for item in tracked)
    percent = round(100 * (completed + errors) / max(len(tracked), 1))
    return percent, completed, errors


def run_worker(job_path: Path) -> None:
    payload = read_job(job_path)
    if not payload:
        raise FileNotFoundError(job_path)

    progress_lock = threading.Lock()

    def progress(component: str, state: str, message: str | None) -> None:
        with progress_lock:
            current = read_job(job_path)
            if current.get("state") == "cancelled":
                raise InterruptedError("Trabajo cancelado por el usuario")
            current.setdefault("components", {}).setdefault(component, {})
            current["components"][component].update(
                state=state,
                message=message or "",
                updated_at=_now(),
            )
            current["updated_at"] = _now()
            _write_job(job_path, current)

    try:
        config = AppConfig.model_validate(payload["config"])
        result = generate_map(config, progress=progress)
        payload = read_job(job_path)
        payload["state"] = "completed"
        payload["output"] = str(result)
        payload["finished_at"] = _now()
        payload["updated_at"] = _now()
        _write_job(job_path, payload)
    except InterruptedError:
        payload = read_job(job_path)
        payload["state"] = "cancelled"
        payload["finished_at"] = _now()
        payload["updated_at"] = _now()
        _write_job(job_path, payload)
    except Exception as exc:  # noqa: BLE001
        payload = read_job(job_path)
        payload["state"] = "error"
        payload["error"] = str(exc)
        payload["finished_at"] = _now()
        payload["updated_at"] = _now()
        _write_job(job_path, payload)
        raise


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "worker":
    run_worker(Path(sys.argv[2]))

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rich.console import Console
from shapely.geometry import mapping

from .aoi import build_aoi
from .config import AppConfig, save_config
from .fusion import FusedLayers, fuse_layers, save_fused_layers
from .grid import GridSpec, write_raster
from .io_utils import ensure_empty_directory, sha256_file, write_json
from .preview import create_analysis_preview, create_orthophoto_preview
from .qgis import create_qgis_project
from .rasterize import rasterize_osm
from .sources.ign import (
    align_raster_to_grid,
    download_ign_mds,
    download_ign_mdt,
    download_pnoa_orthophoto,
)
from .sources.osm import download_osm, save_osm_vectors
from .sources.sentinel2 import download_sentinel2_indices
from .terrain import TerrainLayers, derive_terrain_layers, load_elevation, save_terrain_layers

console = Console()
ProgressCallback = Callable[[str, str, str | None], None]


def _read_layer(path: Path) -> np.ndarray:
    with rasterio.open(path) as dataset:
        array = dataset.read(1).astype(np.float32)
        if dataset.nodata is not None:
            array[array == dataset.nodata] = np.nan
    return array


def _record_file(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _save_multiband(
    destination: Path,
    grid: GridSpec,
    terrain: TerrainLayers,
    fused: FusedLayers,
    osm_arrays: dict[str, np.ndarray],
) -> Path:
    layers: list[tuple[str, np.ndarray]] = [
        ("elevation", terrain.elevation),
        ("slope_degrees", terrain.slope),
        ("aspect_degrees", terrain.aspect),
        ("roughness", terrain.roughness),
        ("local_relief", terrain.local_relief),
        ("max_neighbor_step", terrain.max_neighbor_step),
        ("surface_height", fused.surface_height),
        ("wetness_prior", fused.wetness_prior),
        ("vegetation_prior", fused.vegetation_prior),
        ("mud_risk", fused.mud_risk),
        ("water_accumulation_risk", fused.water_accumulation_risk),
        ("obstacle_probability", fused.obstacle_probability),
        ("traversability_prior", fused.traversability_prior),
        ("confidence", fused.confidence),
    ]
    for name in ["buildings", "roads", "water", "waterways", "forest", "farmland", "scrub"]:
        if name in osm_arrays:
            layers.append((f"osm_{name}", osm_arrays[name]))
    stack = np.stack([array.astype(np.float32) for _, array in layers], axis=0)
    return write_raster(destination, stack, grid, descriptions=[name for name, _ in layers])


def generate_map(config: AppConfig, progress: ProgressCallback | None = None) -> Path:
    def report(component: str, state: str, message: str | None = None) -> None:
        if progress:
            progress(component, state, message)

    started = datetime.now(timezone.utc)
    report("workspace", "processing", "Preparando directorios y cuadrícula")
    root = ensure_empty_directory(config.output.directory, overwrite=config.output.overwrite)
    source_dir = root / "source"
    aligned_dir = root / "aligned"
    layer_dir = root / "layers"
    vector_dir = root / "vectors"
    preview_dir = root / "preview"
    for directory in (source_dir, aligned_dir, layer_dir, vector_dir, preview_dir):
        directory.mkdir(parents=True, exist_ok=True)
    save_config(config, root / "config_used.yaml")

    aoi = build_aoi(config.aoi, config.grid.crs, config.grid.padding_m)
    aoi.projected.to_file(root / "aoi_projected.geojson", driver="GeoJSON")
    aoi.wgs84.to_file(root / "aoi_wgs84.geojson", driver="GeoJSON")
    grid = GridSpec.from_bounds(
        aoi.bounds,
        config.grid.crs,
        config.grid.resolution_m,
        config.grid.nodata,
    )
    console.print(
        f"[bold]Cuadrícula:[/bold] {grid.width}×{grid.height} celdas, "
        f"{grid.resolution:g} m, {grid.crs}"
    )

    source_status: dict[str, Any] = {}
    generated_files: dict[str, Path] = {}

    if not config.sources.ign_mdt.enabled:
        raise ValueError("El pipeline base requiere sources.ign_mdt.enabled=true")
    report("elevation", "downloading", "Descargando MDT05 IGN/CNIG")
    raw_mdt = download_ign_mdt(config.sources.ign_mdt, aoi.bounds, source_dir / "ign_mdt05.tif")
    aligned_mdt = align_raster_to_grid(
        raw_mdt,
        aligned_dir / "elevation_aligned.tif",
        grid,
        resampling=Resampling.bilinear,
        source_crs_fallback=config.sources.ign_mdt.crs,
    )
    source_status["ign_mdt"] = {"ok": True, "required": True}
    generated_files["aligned_elevation"] = aligned_mdt
    report("elevation", "available", "Modelo digital de elevación disponible")

    def fetch_orthophoto() -> tuple[Path, Path]:
        report("orthophoto", "downloading", "Descargando ortofoto PNOA")
        raw = download_pnoa_orthophoto(
            config.sources.ign_orthophoto,
            aoi.bounds,
            source_dir / "pnoa_orthophoto.tif",
        )
        aligned = align_raster_to_grid(
            raw,
            aligned_dir / "orthophoto_aligned.tif",
            grid,
            resampling=Resampling.average,
            source_crs_fallback=config.sources.ign_orthophoto.crs,
            dtype="uint8",
        )
        return raw, aligned

    def fetch_osm() -> gpd.GeoDataFrame:
        report("osm", "downloading", "Consultando objetos en OpenStreetMap")
        frame = download_osm(
            config.sources.osm,
            aoi.bounds_wgs84,
            source_dir / "overpass_response.json",
        )
        save_osm_vectors(frame, vector_dir)
        return frame

    def fetch_surface_model() -> tuple[np.ndarray, Path]:
        report("surface_model", "downloading", "Descargando modelo digital de superficies")
        raw = download_ign_mds(config.sources.ign_mds, aoi.bounds, source_dir / "ign_mds.tif")
        aligned = align_raster_to_grid(
            raw,
            aligned_dir / "mds_aligned.tif",
            grid,
            resampling=Resampling.bilinear,
            source_crs_fallback=config.sources.ign_mds.crs,
        )
        return load_elevation(aligned), aligned

    def fetch_sentinel() -> tuple[dict[str, Path | str | float], np.ndarray, np.ndarray]:
        report("sentinel2", "downloading", "Buscando y procesando Sentinel-2")
        result = download_sentinel2_indices(
            config.sources.sentinel2,
            mapping(aoi.geometry_wgs84),
            grid,
            layer_dir / "sentinel2",
        )
        return (
            result,
            _read_layer(Path(result["ndvi"])),
            _read_layer(Path(result["ndmi"])),
        )

    raw_ortho: Path | None = None
    osm_gdf = gpd.GeoDataFrame(
        {"kind": [], "subtype": []}, geometry=[], crs="EPSG:4326"
    )
    mds_array: np.ndarray | None = None
    ndvi_array: np.ndarray | None = None
    ndmi_array: np.ndarray | None = None
    task_functions = {}
    if config.sources.ign_orthophoto.enabled:
        task_functions["orthophoto"] = fetch_orthophoto
    if config.sources.osm.enabled:
        task_functions["osm"] = fetch_osm
    if config.sources.ign_mds.enabled:
        task_functions["surface_model"] = fetch_surface_model
    if config.sources.sentinel2.enabled:
        task_functions["sentinel2"] = fetch_sentinel

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(task_functions)))) as executor:
        futures = {executor.submit(function): name for name, function in task_functions.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                if name == "orthophoto":
                    raw_ortho, aligned_ortho = result
                    source_status["ign_orthophoto"] = {"ok": True}
                    generated_files["orthophoto"] = raw_ortho
                    generated_files["orthophoto_aligned"] = aligned_ortho
                    report("orthophoto", "available", "Ortofoto descargada y alineada")
                elif name == "osm":
                    osm_gdf = result
                    source_status["osm"] = {"ok": True, "features": int(len(osm_gdf))}
                    report("osm", "available", f"{len(osm_gdf)} objetos públicos")
                elif name == "surface_model":
                    mds_array, aligned_mds = result
                    generated_files["mds"] = aligned_mds
                    source_status["ign_mds"] = {"ok": True}
                    report("surface_model", "available", "Modelo de superficies disponible")
                elif name == "sentinel2":
                    sentinel, ndvi_array, ndmi_array = result
                    source_status["sentinel2"] = {
                        "ok": True,
                        "item_id": sentinel["item_id"],
                        "datetime": sentinel["datetime"],
                        "cloud_cover": sentinel["cloud_cover"],
                    }
                    generated_files["ndvi"] = Path(sentinel["ndvi"])
                    generated_files["ndmi"] = Path(sentinel["ndmi"])
                    generated_files["sentinel_scl"] = Path(sentinel["scl"])
                    report("ndvi", "available", "Índice Sentinel-2 calculado")
                    report("ndmi", "available", "Índice Sentinel-2 calculado")
                    report("sentinel_scl", "available", "Clasificación Sentinel-2 disponible")
                    report("sentinel2", "available", f"Escena {sentinel['item_id']}")
            except Exception as exc:  # noqa: BLE001
                source_key = {
                    "orthophoto": "ign_orthophoto",
                    "surface_model": "ign_mds",
                }.get(name, name)
                source_status[source_key] = {"ok": False, "error": str(exc)}
                report(name, "error", str(exc))
                if name == "sentinel2":
                    for component in ["ndvi", "ndmi", "sentinel_scl"]:
                        report(component, "error", "Fuente Sentinel-2 no disponible")
                console.print(f"[yellow]Aviso: falló {name}: {exc}[/yellow]")

    osm_paths = rasterize_osm(osm_gdf, grid, layer_dir / "osm")
    osm_arrays = {
        name: np.nan_to_num(_read_layer(path), nan=0.0)
        for name, path in osm_paths.items()
    }
    generated_files.update({f"osm_{name}": path for name, path in osm_paths.items()})
    for name in osm_paths:
        if source_status.get("osm", {}).get("ok"):
            report(f"osm_{name}", "available", "Capa OSM rasterizada")
        else:
            report(f"osm_{name}", "error", "Fuente OSM no disponible")

    report("terrain", "processing", "Calculando pendiente, relieve y rugosidad")
    elevation = load_elevation(aligned_mdt)
    terrain = derive_terrain_layers(
        elevation,
        grid.resolution,
        config.terrain.roughness_window_m,
        config.terrain.relief_window_m,
    )
    terrain_paths = save_terrain_layers(terrain, grid, layer_dir / "terrain")
    generated_files.update(terrain_paths)
    for name in terrain_paths:
        component = {
            "elevation": "terrain_elevation",
            "slope": "slope_degrees",
            "aspect": "aspect_degrees",
        }.get(name, name)
        report(component, "available", "Capa de terreno calculada")
    report("terrain", "available", "Capas geométricas calculadas")

    source_flags = {
        "mdt": True,
        "orthophoto": source_status.get("ign_orthophoto", {}).get("ok", False),
        "osm": source_status.get("osm", {}).get("ok", False),
        "mds": source_status.get("ign_mds", {}).get("ok", False),
        "sentinel2": source_status.get("sentinel2", {}).get("ok", False),
    }
    report("semantic_risks", "processing", "Calculando obstáculos y riesgos estimados")
    fused = fuse_layers(
        terrain,
        osm_arrays,
        config.terrain,
        config.weights,
        grid.resolution,
        mds=mds_array,
        ndvi=ndvi_array,
        ndmi=ndmi_array,
        source_flags=source_flags,
    )
    fused_paths = save_fused_layers(fused, grid, layer_dir / "fusion")
    generated_files.update(fused_paths)
    for name in fused_paths:
        if name == "surface_height" and not source_status.get("ign_mds", {}).get("ok"):
            report(name, "unavailable", "MDS no disponible")
        else:
            report(name, "available", "Capa semántica calculada")
    report("semantic_risks", "available", "Priors semánticos calculados")

    if config.output.save_multiband:
        generated_files["multiband"] = _save_multiband(
            root / "public_navigation_map.tif", grid, terrain, fused, osm_arrays
        )
    if config.output.save_preview:
        generated_files["analysis_preview"] = create_analysis_preview(
            terrain, fused, preview_dir / "analysis_overview.png"
        )
        if raw_ortho is not None:
            generated_files["orthophoto_preview"] = create_orthophoto_preview(
                raw_ortho, preview_dir / "orthophoto.png"
            )
    if config.output.save_qgis_project:
        qgis_layers = {
            key: value
            for key, value in generated_files.items()
            if value.suffix.lower() in {".tif", ".tiff"}
        }
        generated_files["qgis_project"] = create_qgis_project(root / "public_map.qgs", qgis_layers)

    finished = datetime.now(timezone.utc)
    manifest = {
        "project_name": config.project_name,
        "created_at": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "grid": {
            "crs": grid.crs,
            "resolution_m": grid.resolution,
            "width": grid.width,
            "height": grid.height,
            "bounds": grid.bounds,
        },
        "aoi_bounds_wgs84": aoi.bounds_wgs84,
        "sources": source_status,
        "unavailable_layers": {
            "soil_moisture": (
                "No hay un proveedor público de humedad del suelo configurado para este pipeline."
            ),
            "recent_precipitation": (
                "No hay un proveedor de precipitación reciente implementado actualmente."
            ),
        },
        "inferred_layers": {
            "wetness_prior": {
                "status": "estimated_risk",
                "variables": [
                    "distance_to_osm_water",
                    "slope",
                    "local_relief",
                    "osm_wetlands",
                    "sentinel2_ndmi_when_available",
                ],
                "confidence_layer": "layers/fusion/confidence.tif",
            },
            "mud_risk": {
                "status": "estimated_risk",
                "variables": [
                    "wetness_prior",
                    "slope",
                    "osm_farmland",
                    "osm_grass",
                    "vegetation_prior",
                ],
                "confidence_layer": "layers/fusion/confidence.tif",
            },
            "water_accumulation_risk": {
                "status": "estimated_risk",
                "variables": [
                    "wetness_prior",
                    "slope",
                    "local_relief",
                    "osm_water",
                ],
                "confidence_layer": "layers/fusion/confidence.tif",
            },
        },
        "limitations": [
            "Es un mapa previo; no sustituye la percepción local del robot.",
            "El MDT05 tiene paso de malla de 5 m aunque la salida se remuestree.",
            "OpenStreetMap puede estar incompleto o desactualizado.",
            "wetness_prior es una estimación relativa, no humedad medida.",
            "Los obstáculos temporales o pequeños no son observables de forma fiable.",
        ],
        "files": {
            name: _record_file(path, root)
            for name, path in generated_files.items()
            if path.exists() and path.is_file()
        },
    }
    write_json(root / "metadata.json", manifest)
    report("workspace", "available", "Proyecto completado")
    console.print(f"[green bold]Mapa generado en {root}[/green bold]")
    return root

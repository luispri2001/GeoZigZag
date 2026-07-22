from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import ListedColormap
from rasterio.enums import Resampling
from rasterio.warp import reproject


@dataclass(frozen=True)
class LayerStyle:
    label: str
    category: str
    cmap: str
    unit: str = ""
    fixed_range: tuple[float, float] | None = None
    binary: bool = False
    description: str = ""


@dataclass(frozen=True)
class LayerInfo:
    path: Path
    key: str
    style: LayerStyle


STYLES: dict[str, LayerStyle] = {
    "elevation": LayerStyle("Elevación", "Terreno", "terrain", "m"),
    "slope_degrees": LayerStyle("Pendiente", "Terreno", "magma", "°", (0, 30)),
    "aspect_degrees": LayerStyle("Orientación de ladera", "Terreno", "twilight", "°", (0, 360)),
    "roughness": LayerStyle("Rugosidad", "Terreno", "magma", "m"),
    "local_relief": LayerStyle("Relieve local", "Terreno", "terrain", "m"),
    "max_neighbor_step": LayerStyle("Escalón máximo", "Terreno", "inferno", "m"),
    "buildings": LayerStyle("Edificios", "Semántica OSM", "Reds", binary=True),
    "roads": LayerStyle("Caminos", "Semántica OSM", "Greys", binary=True),
    "water": LayerStyle("Agua", "Semántica OSM", "Blues", binary=True),
    "waterways": LayerStyle("Cursos de agua", "Semántica OSM", "Blues", binary=True),
    "wetlands": LayerStyle("Humedales", "Semántica OSM", "PuBuGn", binary=True),
    "forest": LayerStyle("Bosque", "Semántica OSM", "Greens", binary=True),
    "farmland": LayerStyle("Cultivo", "Semántica OSM", "YlGn", binary=True),
    "grass": LayerStyle("Pastizal", "Semántica OSM", "YlGn", binary=True),
    "scrub": LayerStyle("Matorral", "Semántica OSM", "YlOrBr", binary=True),
    "barriers": LayerStyle("Barreras", "Semántica OSM", "Reds", binary=True),
    "surface_height": LayerStyle("Altura superficial", "Fusión para navegación", "viridis", "m"),
    "wetness_prior": LayerStyle(
        "Prior de humedad", "Fusión para navegación", "Blues", fixed_range=(0, 1)
    ),
    "vegetation_prior": LayerStyle(
        "Prior de vegetación", "Fusión para navegación", "Greens", fixed_range=(0, 1)
    ),
    "mud_risk": LayerStyle(
        "Riesgo estimado de barro",
        "Fusión para navegación",
        "YlOrBr",
        fixed_range=(0, 1),
        description="Inferencia basada en humedad, pendiente y cobertura blanda.",
    ),
    "water_accumulation_risk": LayerStyle(
        "Riesgo estimado de acumulación de agua",
        "Fusión para navegación",
        "PuBu",
        fixed_range=(0, 1),
        description="Inferencia basada en humedad, pendiente, relieve local y agua OSM.",
    ),
    "obstacle_probability": LayerStyle(
        "Probabilidad de obstáculo",
        "Fusión para navegación",
        "inferno",
        fixed_range=(0, 1),
    ),
    "traversability_prior": LayerStyle(
        "Coste de transitabilidad",
        "Fusión para navegación",
        "RdYlGn_r",
        fixed_range=(0, 1),
    ),
    "confidence": LayerStyle("Confianza", "Fusión para navegación", "viridis", fixed_range=(0, 1)),
    "ndvi": LayerStyle("NDVI", "Sentinel-2", "RdYlGn", fixed_range=(-1, 1)),
    "ndmi": LayerStyle("NDMI", "Sentinel-2", "BrBG", fixed_range=(-1, 1)),
    "sentinel_scl": LayerStyle("Clasificación Sentinel-2", "Sentinel-2", "tab20"),
    "orthophoto_aligned": LayerStyle("Ortofoto PNOA", "Imagen base", "gray"),
}


def discover_layers(output_dir: str | Path) -> list[LayerInfo]:
    root = Path(output_dir)
    candidates = list((root / "layers").glob("**/*.tif"))
    orthophoto = root / "aligned" / "orthophoto_aligned.tif"
    if orthophoto.exists():
        candidates.append(orthophoto)
    layers = []
    for path in sorted(candidates):
        key = path.stem
        style = STYLES.get(key, LayerStyle(key.replace("_", " ").title(), "Otras", "viridis"))
        layers.append(LayerInfo(path=path, key=key, style=style))
    return layers


def layer_statistics(path: str | Path) -> dict[str, float | int | str]:
    with rasterio.open(path) as dataset:
        data = dataset.read(1, masked=True)
        values = data.compressed()
        result: dict[str, float | int | str] = {
            "width": dataset.width,
            "height": dataset.height,
            "crs": str(dataset.crs),
            "valid_cells": int(values.size),
        }
        if values.size:
            result.update(
                minimum=float(np.nanmin(values)),
                maximum=float(np.nanmax(values)),
                mean=float(np.nanmean(values)),
                p05=float(np.nanpercentile(values, 5)),
                p95=float(np.nanpercentile(values, 95)),
            )
        return result


def _read_base_rgb(path: Path, reference) -> np.ndarray:
    with rasterio.open(path) as source:
        bands = min(3, source.count)
        destination = np.zeros((bands, reference.height, reference.width), dtype=np.float32)
        for band in range(bands):
            reproject(
                source=rasterio.band(source, band + 1),
                destination=destination[band],
                src_transform=source.transform,
                src_crs=source.crs,
                dst_transform=reference.transform,
                dst_crs=reference.crs,
                resampling=Resampling.bilinear,
            )
    if bands == 1:
        destination = np.repeat(destination, 3, axis=0)
    rgb = np.moveaxis(destination, 0, 2)
    for band in range(3):
        channel = rgb[:, :, band]
        valid = channel[np.isfinite(channel)]
        if valid.size:
            low, high = np.percentile(valid, [2, 98])
            rgb[:, :, band] = np.clip((channel - low) / max(high - low, 1e-9), 0, 1)
    return rgb


def render_layer_png(
    layer: LayerInfo,
    *,
    cmap: str | None = None,
    opacity: float = 1.0,
    base_orthophoto: Path | None = None,
    value_range: tuple[float, float] | None = None,
) -> bytes:
    with rasterio.open(layer.path) as dataset:
        if layer.key == "orthophoto_aligned" and dataset.count >= 3:
            image = _read_base_rgb(layer.path, dataset)
            figure, axis = plt.subplots(figsize=(9, 7), constrained_layout=True)
            axis.imshow(image)
        else:
            data = dataset.read(1, masked=True)
            figure, axis = plt.subplots(figsize=(9, 7), constrained_layout=True)
            if base_orthophoto and base_orthophoto.exists():
                axis.imshow(_read_base_rgb(base_orthophoto, dataset))
            selected_cmap = cmap or layer.style.cmap
            if layer.style.binary:
                selected_cmap = ListedColormap(["#00000000", plt.get_cmap(selected_cmap)(0.8)])
                value_range = (0, 1)
            limits = value_range or layer.style.fixed_range
            if limits is None:
                values = data.compressed()
                limits = tuple(np.nanpercentile(values, [2, 98])) if values.size else (0, 1)
            image_artist = axis.imshow(
                data,
                cmap=selected_cmap,
                vmin=limits[0],
                vmax=limits[1],
                alpha=opacity,
            )
            colorbar = figure.colorbar(image_artist, ax=axis, fraction=0.035, pad=0.02)
            if layer.style.unit:
                colorbar.set_label(layer.style.unit)
        axis.set_title(layer.style.label)
        axis.set_axis_off()
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=140, bbox_inches="tight")
        plt.close(figure)
    return buffer.getvalue()

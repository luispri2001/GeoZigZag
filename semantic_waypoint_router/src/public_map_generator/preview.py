from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio

from .fusion import FusedLayers
from .terrain import TerrainLayers


def create_analysis_preview(
    terrain: TerrainLayers,
    fused: FusedLayers,
    destination: Path,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(13, 10), constrained_layout=True)
    entries = [
        (terrain.elevation, "Elevación (m)", "terrain"),
        (terrain.slope, "Pendiente (°)", "magma"),
        (fused.obstacle_probability, "Probabilidad de obstáculo", "inferno"),
        (fused.traversability_prior, "Coste de transitabilidad", "RdYlGn_r"),
    ]
    for axis, (data, title, cmap) in zip(axes.flat, entries, strict=True):
        image = axis.imshow(data, cmap=cmap)
        axis.set_title(title)
        axis.set_axis_off()
        fig.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    fig.suptitle("Mapa previo generado a partir de datos públicos", fontsize=16)
    fig.savefig(destination, dpi=170)
    plt.close(fig)
    return destination


def create_orthophoto_preview(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source) as dataset:
        rgb = dataset.read([1, 2, 3])
    rgb = np.moveaxis(rgb, 0, 2)
    max_dimension = max(rgb.shape[:2])
    stride = max(1, int(np.ceil(max_dimension / 2500)))
    rgb = rgb[::stride, ::stride]
    plt.figure(figsize=(10, 10))
    plt.imshow(rgb)
    plt.title("Ortofoto PNOA — zona de interés")
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(destination, dpi=170, bbox_inches="tight")
    plt.close()
    return destination

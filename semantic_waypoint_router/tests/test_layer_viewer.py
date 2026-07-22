from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from public_map_generator.layer_viewer import discover_layers, layer_statistics, render_layer_png


def _write_raster(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype="float32",
        crs="EPSG:25830",
        transform=from_origin(0, 10, 1, 1),
        nodata=-9999,
    ) as dataset:
        dataset.write(data.astype(np.float32), 1)


def test_discovers_and_renders_semantic_layer(tmp_path: Path) -> None:
    raster = tmp_path / "layers" / "fusion" / "traversability_prior.tif"
    _write_raster(raster, np.array([[0.0, 0.5], [0.8, 1.0]]))

    layers = discover_layers(tmp_path)

    assert len(layers) == 1
    assert layers[0].style.category == "Fusión para navegación"
    assert render_layer_png(layers[0]).startswith(b"\x89PNG")


def test_layer_statistics(tmp_path: Path) -> None:
    raster = tmp_path / "layers" / "terrain" / "slope_degrees.tif"
    _write_raster(raster, np.array([[1.0, 2.0], [3.0, 4.0]]))

    stats = layer_statistics(raster)

    assert stats["valid_cells"] == 4
    assert stats["minimum"] == 1.0
    assert stats["maximum"] == 4.0

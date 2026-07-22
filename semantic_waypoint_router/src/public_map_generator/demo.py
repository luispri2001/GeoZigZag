from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import ndimage

from .config import TerrainConfig, WeightsConfig
from .fusion import fuse_layers, save_fused_layers
from .grid import GridSpec, write_raster
from .io_utils import ensure_empty_directory, write_json
from .preview import create_analysis_preview
from .terrain import derive_terrain_layers, save_terrain_layers


def create_synthetic_demo(output: str | Path, overwrite: bool = True) -> Path:
    root = ensure_empty_directory(output, overwrite=overwrite)
    layers_dir = root / "layers"
    preview_dir = root / "preview"
    grid = GridSpec.from_bounds((290000.0, 4710000.0, 290600.0, 4710500.0), "EPSG:25830", 2.0)

    y, x = np.mgrid[0 : grid.height, 0 : grid.width]
    elevation = (
        820.0
        + 0.025 * x
        + 0.012 * y
        + 3.5 * np.exp(-(((x - 105) / 35) ** 2 + ((y - 75) / 25) ** 2))
        - 2.0 * np.exp(-(((x - 220) / 45) ** 2 + ((y - 165) / 30) ** 2))
        + 0.15 * np.sin(x / 8.0) * np.cos(y / 10.0)
    ).astype(np.float32)
    terrain = derive_terrain_layers(elevation, grid.resolution, 15.0, 25.0)

    shape = elevation.shape
    osm = {name: np.zeros(shape, dtype=np.float32) for name in [
        "buildings", "roads", "water", "waterways", "wetlands", "forest",
        "farmland", "grass", "scrub", "barriers"
    ]}
    osm["roads"][118:126, :] = 1.0
    osm["buildings"][35:75, 40:78] = 1.0
    osm["buildings"][150:195, 215:255] = 1.0
    osm["water"][195:215, 35:120] = 1.0
    osm["forest"][15:105, 175:295] = 1.0
    osm["farmland"][125:230, 125:205] = 1.0
    osm["scrub"][80:130, 20:95] = 1.0
    osm["barriers"][100:103, 140:230] = 1.0

    mds = elevation.copy()
    mds[osm["buildings"] > 0] += 7.0
    mds[osm["forest"] > 0] += 3.0
    mds = ndimage.gaussian_filter(mds, sigma=0.4)

    fused = fuse_layers(
        terrain,
        osm,
        TerrainConfig(),
        WeightsConfig(),
        grid.resolution,
        mds=mds,
        source_flags={"mdt": True, "osm": True, "mds": True},
    )
    terrain_paths = save_terrain_layers(terrain, grid, layers_dir / "terrain")
    fused_paths = save_fused_layers(fused, grid, layers_dir / "fusion")
    for name, array in osm.items():
        write_raster(layers_dir / "osm" / f"{name}.tif", array, grid, nodata=0.0)
    preview = create_analysis_preview(terrain, fused, preview_dir / "analysis_overview.png")
    write_json(
        root / "metadata.json",
        {
            "type": "synthetic_demo",
            "note": (
                "Datos sintéticos incluidos únicamente para comprobar la instalación sin conexión."
            ),
            "grid": {"crs": grid.crs, "resolution_m": grid.resolution, "bounds": grid.bounds},
            "terrain_layers": [str(path.relative_to(root)) for path in terrain_paths.values()],
            "fusion_layers": [str(path.relative_to(root)) for path in fused_paths.values()],
            "preview": str(preview.relative_to(root)),
        },
    )
    return root

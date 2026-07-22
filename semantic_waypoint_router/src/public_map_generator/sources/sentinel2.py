from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rich.console import Console

from ..config import Sentinel2Config
from ..grid import GridSpec, write_raster
from .common import PublicDataError

console = Console()


def _load_optional_dependencies():
    try:
        import planetary_computer
        import pystac_client
    except ImportError as exc:
        raise PublicDataError(
            "Sentinel-2 requiere las dependencias opcionales. Instala: pip install -e '.[sentinel]'"
        ) from exc
    return planetary_computer, pystac_client


def _read_asset_to_grid(href: str, grid: GridSpec, resampling: Resampling) -> np.ndarray:
    destination = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    with rasterio.open(href) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=np.nan,
            resampling=resampling,
        )
    return destination


def download_sentinel2_indices(
    config: Sentinel2Config,
    aoi_geojson: dict,
    grid: GridSpec,
    output_dir: Path,
) -> dict[str, Path | str | float]:
    planetary_computer, pystac_client = _load_optional_dependencies()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=config.lookback_days)
    console.print("[cyan]Buscando escena Sentinel-2 reciente…[/cyan]")
    catalog = pystac_client.Client.open(config.stac_url)
    search = catalog.search(
        collections=[config.collection],
        intersects=aoi_geojson,
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": config.max_cloud_cover}},
        max_items=100,
    )
    items = list(search.items())
    if not items:
        raise PublicDataError(
            "No se encontró ninguna escena Sentinel-2 que cumpla el filtro de nubes"
        )
    items.sort(key=lambda item: float(item.properties.get("eo:cloud_cover", 100.0)))
    item = planetary_computer.sign(items[0])

    required = {"B04": "red", "B08": "nir", "B11": "swir16", "SCL": "SCL"}
    assets: dict[str, str] = {}
    for preferred, fallback in required.items():
        if preferred in item.assets:
            assets[preferred] = item.assets[preferred].href
        elif fallback in item.assets:
            assets[preferred] = item.assets[fallback].href
        else:
            raise PublicDataError(
                f"La escena Sentinel-2 no contiene el asset {preferred}/{fallback}"
            )

    red = _read_asset_to_grid(assets["B04"], grid, Resampling.bilinear)
    nir = _read_asset_to_grid(assets["B08"], grid, Resampling.bilinear)
    swir = _read_asset_to_grid(assets["B11"], grid, Resampling.bilinear)
    scl = _read_asset_to_grid(assets["SCL"], grid, Resampling.nearest)
    invalid = np.isin(np.rint(scl).astype(np.int16), [0, 1, 3, 8, 9, 10, 11])

    def ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        denominator = a + b
        value = np.divide(a - b, denominator, out=np.full_like(a, np.nan), where=denominator != 0)
        value[invalid] = np.nan
        return np.clip(value, -1.0, 1.0).astype(np.float32)

    ndvi = ratio(nir, red)
    ndmi = ratio(nir, swir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ndvi_path = write_raster(output_dir / "ndvi.tif", ndvi, grid)
    ndmi_path = write_raster(output_dir / "ndmi.tif", ndmi, grid)
    scl_path = write_raster(output_dir / "sentinel_scl.tif", scl.astype(np.float32), grid)
    return {
        "ndvi": ndvi_path,
        "ndmi": ndmi_path,
        "scl": scl_path,
        "item_id": item.id,
        "datetime": item.datetime.isoformat() if item.datetime else "unknown",
        "cloud_cover": float(item.properties.get("eo:cloud_cover", np.nan)),
    }

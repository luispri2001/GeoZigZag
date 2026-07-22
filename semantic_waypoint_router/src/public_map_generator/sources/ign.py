from __future__ import annotations

from io import BytesIO
from math import ceil
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject
from rasterio.windows import Window
from rich.console import Console

from ..config import IGNMDSConfig, IGNMDTConfig, IGNOrthoConfig
from ..grid import GridSpec
from .common import PublicDataError, request_bytes, save_bytes

console = Console()


def _download_wcs_raster(
    *,
    url: str,
    coverage: str,
    crs: str,
    bounds: tuple[float, float, float, float],
    source_resolution_m: float,
    timeout_s: int,
    destination: Path,
    max_pixels_per_side: int = 8000,
) -> Path:
    minx, miny, maxx, maxy = bounds
    width = max(1, int(ceil((maxx - minx) / source_resolution_m)))
    height = max(1, int(ceil((maxy - miny) / source_resolution_m)))
    if width > max_pixels_per_side or height > max_pixels_per_side:
        raise PublicDataError(
            f"Petición WCS demasiado grande ({width}x{height}). "
            "Reduce el AOI o usa menor resolución."
        )

    params = {
        "SERVICE": "WCS",
        "VERSION": "1.0.0",
        "REQUEST": "GetCoverage",
        "COVERAGE": coverage,
        "CRS": crs,
        "BBOX": ",".join(str(v) for v in bounds),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "FORMAT": "GEOTIFFINT16",
    }
    content = request_bytes(url, params=params, timeout=timeout_s)
    if not content.startswith((b"II*\x00", b"MM\x00*")):
        text = content[:2000].decode("utf-8", errors="replace")
        raise PublicDataError(f"La respuesta WCS no parece un GeoTIFF: {text}")
    return save_bytes(destination, content)


def download_ign_mdt(
    config: IGNMDTConfig, bounds: tuple[float, float, float, float], out: Path
) -> Path:
    console.print("[cyan]Descargando MDT05 del IGN/CNIG…[/cyan]")
    return _download_wcs_raster(
        url=config.url,
        coverage=config.coverage,
        crs=config.crs,
        bounds=bounds,
        source_resolution_m=config.source_resolution_m,
        timeout_s=config.timeout_s,
        destination=out,
        max_pixels_per_side=config.max_pixels_per_side,
    )


def download_ign_mds(
    config: IGNMDSConfig, bounds: tuple[float, float, float, float], out: Path
) -> Path:
    console.print("[cyan]Descargando MDS del IGN/CNIG…[/cyan]")
    return _download_wcs_raster(
        url=config.url,
        coverage=config.coverage,
        crs=config.crs,
        bounds=bounds,
        source_resolution_m=config.source_resolution_m,
        timeout_s=config.timeout_s,
        destination=out,
    )


def align_raster_to_grid(
    source: str | Path,
    destination: str | Path,
    grid: GridSpec,
    *,
    resampling: Resampling = Resampling.bilinear,
    source_crs_fallback: str | None = None,
    dtype: str = "float32",
) -> Path:
    out = Path(destination)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source) as src:
        src_crs = source_crs_fallback or src.crs
        if src_crs is None:
            raise PublicDataError(f"El ráster {source} no define CRS y no hay fallback")
        count = src.count
        output_nodata = 0 if np.dtype(dtype) == np.dtype("uint8") else grid.nodata
        profile = grid.profile(count=count, dtype=dtype, nodata=output_nodata)
        with rasterio.open(out, "w", **profile) as dst:
            for band in range(1, count + 1):
                reproject(
                    source=rasterio.band(src, band),
                    destination=rasterio.band(dst, band),
                    src_transform=src.transform,
                    src_crs=src_crs,
                    src_nodata=src.nodata,
                    dst_transform=grid.transform,
                    dst_crs=grid.crs,
                    dst_nodata=output_nodata,
                    resampling=resampling,
                )
    return out


def download_pnoa_orthophoto(
    config: IGNOrthoConfig,
    bounds: tuple[float, float, float, float],
    out: Path,
) -> Path:
    """Descarga una ortofoto por teselas WMS y la guarda como GeoTIFF RGB."""
    minx, miny, maxx, maxy = bounds
    pixel = config.pixel_size_m
    width = max(1, int(ceil((maxx - minx) / pixel)))
    height = max(1, int(ceil((maxy - miny) / pixel)))
    if width * height > config.max_total_pixels:
        raise PublicDataError(
            f"La ortofoto tendría {width * height:,} píxeles; supera max_total_pixels. "
            "Reduce el AOI o aumenta ign_orthophoto.pixel_size_m."
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    transform = from_origin(minx, maxy, pixel, pixel)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 3,
        "dtype": "uint8",
        "crs": config.crs,
        "transform": transform,
        "compress": "jpeg",
        "photometric": "YCBCR",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }

    console.print(f"[cyan]Descargando ortofoto PNOA ({width}x{height} px)…[/cyan]")
    tile_size = config.tile_size_px
    with rasterio.open(out, "w", **profile) as dataset:
        for row_off in range(0, height, tile_size):
            tile_h = min(tile_size, height - row_off)
            tile_maxy = maxy - row_off * pixel
            tile_miny = tile_maxy - tile_h * pixel
            for col_off in range(0, width, tile_size):
                tile_w = min(tile_size, width - col_off)
                tile_minx = minx + col_off * pixel
                tile_maxx = tile_minx + tile_w * pixel
                params = {
                    "SERVICE": "WMS",
                    "VERSION": "1.1.1",
                    "REQUEST": "GetMap",
                    "LAYERS": config.layer,
                    "STYLES": "",
                    "SRS": config.crs,
                    "BBOX": f"{tile_minx},{tile_miny},{tile_maxx},{tile_maxy}",
                    "WIDTH": str(tile_w),
                    "HEIGHT": str(tile_h),
                    "FORMAT": "image/jpeg",
                    "TRANSPARENT": "FALSE",
                    "EXCEPTIONS": "application/vnd.ogc.se_xml",
                }
                payload = request_bytes(config.url, params=params, timeout=config.timeout_s)
                try:
                    image = Image.open(BytesIO(payload)).convert("RGB")
                except Exception as exc:  # noqa: BLE001
                    text = payload[:2000].decode("utf-8", errors="replace")
                    raise PublicDataError(f"El WMS no devolvió una imagen válida: {text}") from exc
                if image.size != (tile_w, tile_h):
                    image = image.resize((tile_w, tile_h), Image.Resampling.BILINEAR)
                data = np.moveaxis(np.asarray(image, dtype=np.uint8), 2, 0)
                dataset.write(data, window=Window(col_off, row_off, tile_w, tile_h))
    return out

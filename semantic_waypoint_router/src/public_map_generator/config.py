from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class AOIConfig(BaseModel):
    type: Literal["center_radius", "bbox", "geojson"] = "center_radius"
    lat: float | None = None
    lon: float | None = None
    radius_m: float | None = None
    bbox: list[float] | None = None  # west, south, east, north
    path: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> AOIConfig:
        if self.type == "center_radius":
            if self.lat is None or self.lon is None or self.radius_m is None:
                raise ValueError("center_radius requiere lat, lon y radius_m")
            if self.radius_m <= 0:
                raise ValueError("radius_m debe ser positivo")
        elif self.type == "bbox":
            if self.bbox is None or len(self.bbox) != 4:
                raise ValueError("bbox requiere [west, south, east, north]")
        elif self.type == "geojson" and not self.path:
            raise ValueError("geojson requiere path")
        return self


class GridConfig(BaseModel):
    crs: str = "EPSG:25830"
    resolution_m: float = 5.0
    padding_m: float = 10.0
    nodata: float = -9999.0


class IGNMDTConfig(BaseModel):
    enabled: bool = True
    url: str = "https://servicios.idee.es/wcs-inspire/mdt"
    coverage: str = "Elevacion25830_5"
    crs: str = "EPSG:25830"
    source_resolution_m: float = 5.0
    max_pixels_per_side: int = 5000
    timeout_s: int = 180


class IGNOrthoConfig(BaseModel):
    enabled: bool = True
    url: str = "https://www.ign.es/wms-inspire/pnoa-ma"
    layer: str = "OI.OrthoimageCoverage"
    crs: str = "EPSG:25830"
    pixel_size_m: float = 0.5
    tile_size_px: int = 1536
    max_total_pixels: int = 120_000_000
    timeout_s: int = 180


class IGNMDSConfig(BaseModel):
    enabled: bool = False
    url: str = "https://wcs-mds.idee.es/mds"
    coverage: str = "mds05"
    crs: str = "EPSG:25830"
    source_resolution_m: float = 5.0
    timeout_s: int = 180


class OSMConfig(BaseModel):
    enabled: bool = True
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    timeout_s: int = 180
    user_agent: str = "public-map-generator/0.1"


class Sentinel2Config(BaseModel):
    enabled: bool = False
    stac_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    collection: str = "sentinel-2-l2a"
    lookback_days: int = 365
    max_cloud_cover: float = 25.0


class SourcesConfig(BaseModel):
    ign_mdt: IGNMDTConfig = Field(default_factory=IGNMDTConfig)
    ign_orthophoto: IGNOrthoConfig = Field(default_factory=IGNOrthoConfig)
    ign_mds: IGNMDSConfig = Field(default_factory=IGNMDSConfig)
    osm: OSMConfig = Field(default_factory=OSMConfig)
    sentinel2: Sentinel2Config = Field(default_factory=Sentinel2Config)


class TerrainConfig(BaseModel):
    roughness_window_m: float = 15.0
    relief_window_m: float = 25.0
    slope_warning_deg: float = 12.0
    slope_blocked_deg: float = 25.0
    roughness_warning_m: float = 0.20
    roughness_blocked_m: float = 0.60
    max_step_warning_m: float = 0.20
    max_step_blocked_m: float = 0.45
    surface_height_warning_m: float = 0.20
    surface_height_blocked_m: float = 0.45
    water_proximity_m: float = 60.0


class WeightsConfig(BaseModel):
    slope: float = 0.24
    roughness: float = 0.20
    max_step: float = 0.18
    obstacles: float = 0.28
    wetness: float = 0.10
    road_bonus: float = 0.20


class OutputConfig(BaseModel):
    directory: str = "outputs/public_map"
    save_multiband: bool = True
    save_preview: bool = True
    save_qgis_project: bool = True
    overwrite: bool = False


class AppConfig(BaseModel):
    project_name: str = "public_map"
    aoi: AOIConfig
    grid: GridConfig = Field(default_factory=GridConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    terrain: TerrainConfig = Field(default_factory=TerrainConfig)
    weights: WeightsConfig = Field(default_factory=WeightsConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return AppConfig.model_validate(payload)


def save_config(config: AppConfig, path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config.model_dump(mode="json"), handle, sort_keys=False, allow_unicode=True)

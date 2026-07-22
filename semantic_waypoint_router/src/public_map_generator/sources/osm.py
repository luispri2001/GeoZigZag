from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import requests
from rich.console import Console
from shapely.geometry import LineString, Point, Polygon

from ..config import OSMConfig
from .common import PublicDataError

console = Console()


def _query(bounds_wgs84: tuple[float, float, float, float]) -> str:
    west, south, east, north = bounds_wgs84
    bbox = f"{south},{west},{north},{east}"
    return f"""
[out:json][timeout:180];
(
  nwr["building"]({bbox});
  way["highway"]({bbox});
  nwr["natural"="water"]({bbox});
  nwr["waterway"]({bbox});
  nwr["landuse"="reservoir"]({bbox});
  nwr["natural"="wetland"]({bbox});
  nwr["natural"="wood"]({bbox});
  nwr["landuse"="forest"]({bbox});
  nwr["landuse"="farmland"]({bbox});
  nwr["landuse"="grass"]({bbox});
  nwr["natural"="scrub"]({bbox});
  nwr["barrier"]({bbox});
);
out geom;
""".strip()


def _feature_kind(tags: dict[str, Any]) -> str:
    if "building" in tags:
        return "building"
    if "highway" in tags:
        return "road"
    if tags.get("natural") == "water" or tags.get("landuse") == "reservoir":
        return "water"
    if "waterway" in tags:
        return "waterway"
    if tags.get("natural") == "wetland":
        return "wetland"
    if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
        return "forest"
    if tags.get("landuse") == "farmland":
        return "farmland"
    if tags.get("landuse") == "grass":
        return "grass"
    if tags.get("natural") == "scrub":
        return "scrub"
    if "barrier" in tags:
        return "barrier"
    return "other"


def _geometry_from_element(element: dict[str, Any]):
    geometry = element.get("geometry") or []
    if geometry:
        coords = [(node["lon"], node["lat"]) for node in geometry]
        if len(coords) >= 4 and coords[0] == coords[-1]:
            polygon = Polygon(coords)
            if polygon.is_valid and not polygon.is_empty:
                return polygon
        if len(coords) >= 2:
            return LineString(coords)
    if element.get("type") == "node" and "lat" in element and "lon" in element:
        return Point(element["lon"], element["lat"])
    return None


def download_osm(
    config: OSMConfig,
    bounds_wgs84: tuple[float, float, float, float],
    out_json: Path,
) -> gpd.GeoDataFrame:
    console.print("[cyan]Consultando OpenStreetMap mediante Overpass…[/cyan]")
    response = requests.post(
        config.overpass_url,
        data={"data": _query(bounds_wgs84)},
        headers={"User-Agent": config.user_agent},
        timeout=config.timeout_s,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise PublicDataError(response.text[:2000]) from exc
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    records: list[dict[str, Any]] = []
    geometries = []
    for element in payload.get("elements", []):
        geom = _geometry_from_element(element)
        if geom is None or geom.is_empty:
            continue
        tags = element.get("tags", {})
        records.append(
            {
                "osm_type": element.get("type"),
                "osm_id": str(element.get("id")),
                "kind": _feature_kind(tags),
                "name": tags.get("name"),
                "subtype": tags.get("building")
                or tags.get("highway")
                or tags.get("natural")
                or tags.get("landuse")
                or tags.get("waterway")
                or tags.get("barrier"),
                "tags_json": json.dumps(tags, ensure_ascii=False),
            }
        )
        geometries.append(geom)

    return gpd.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")


def save_osm_vectors(gdf: gpd.GeoDataFrame, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    if gdf.empty:
        return outputs
    for kind, subset in gdf.groupby("kind"):
        subset = subset.copy()
        geojson = destination / f"osm_{kind}.geojson"
        subset.to_file(geojson, driver="GeoJSON")
        outputs.append(geojson)
    return outputs

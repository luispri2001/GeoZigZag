"""Spanish Cadastre INSPIRE building-footprint adapter.

The public WFS returns GML geometries in the UTM zone requested by the
client.  This module keeps that service-specific work out of the HTTP server
and exposes the same latitude/longitude polygon contract used by GeoZigzag's
semantic planner.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


WFS_URL = "https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx?"
SOURCE_NAME = "Dirección General del Catastro"
DATASET_NAME = "Catastro INSPIRE Buildings (BU)"
_GML = "http://www.opengis.net/gml/3.2"
_BUILDING = "http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0"


class BoundingBox(Protocol):
    south: float
    west: float
    north: float
    east: float


class CatastroError(RuntimeError):
    """Raised when the official building service cannot be queried safely."""


@dataclass(frozen=True)
class CatastroResult:
    features: list[dict[str, object]]
    cached: bool
    fetched_at_unix: int
    query_epsg: int


def supports_bbox(bbox: BoundingBox) -> bool:
    """Return whether a WGS84 bbox could fall inside Spanish territory."""
    return not (
        bbox.east < -18.5
        or bbox.west > 5.0
        or bbox.north < 27.0
        or bbox.south > 44.5
    )


def utm_epsg_for_longitude(longitude: float) -> int:
    """Return the ETRS89 UTM EPSG supported by the Cadastre WFS."""
    zone = int(math.floor((longitude + 180.0) / 6.0)) + 1
    if zone not in {27, 28, 29, 30, 31}:
        raise ValueError(f"Longitude {longitude:.6f} is outside supported Spanish UTM zones.")
    return 25_800 + zone


def _transformers(epsg: int):
    try:
        from pyproj import Transformer
    except ImportError as error:
        raise CatastroError(
            "pyproj is required for Catastro building coordinates; install requirements.txt."
        ) from error
    return (
        Transformer.from_crs(4326, epsg, always_xy=True),
        Transformer.from_crs(epsg, 4326, always_xy=True),
    )


def _projected_bbox(bbox: BoundingBox, to_utm) -> tuple[float, float, float, float]:
    corners = [
        to_utm.transform(bbox.west, bbox.south),
        to_utm.transform(bbox.west, bbox.north),
        to_utm.transform(bbox.east, bbox.south),
        to_utm.transform(bbox.east, bbox.north),
    ]
    eastings = [point[0] for point in corners]
    northings = [point[1] for point in corners]
    return min(eastings), min(northings), max(eastings), max(northings)


def build_wfs_url(bbox: BoundingBox) -> tuple[str, int]:
    epsg = utm_epsg_for_longitude((bbox.west + bbox.east) / 2.0)
    to_utm, _ = _transformers(epsg)
    projected = _projected_bbox(bbox, to_utm)
    params = {
        "service": "wfs",
        "version": "2",
        "request": "getfeature",
        "typenames": "BU.BUILDING",
        "bbox": ",".join(f"{value:.2f}" for value in projected),
        "srsname": f"EPSG::{epsg}",
    }
    return WFS_URL + urllib.parse.urlencode(params), epsg


def _polygon_metrics(points: list[tuple[float, float]]) -> tuple[float, tuple[float, float]]:
    twice_area = 0.0
    centroid_x = 0.0
    centroid_y = 0.0
    vertices = points[:-1] if points and points[0] == points[-1] else points
    for index, first in enumerate(vertices):
        second = vertices[(index + 1) % len(vertices)]
        cross = first[0] * second[1] - second[0] * first[1]
        twice_area += cross
        centroid_x += (first[0] + second[0]) * cross
        centroid_y += (first[1] + second[1]) * cross
    if abs(twice_area) < 1e-9:
        return 0.0, (
            sum(point[0] for point in vertices) / len(vertices),
            sum(point[1] for point in vertices) / len(vertices),
        )
    return abs(twice_area) / 2.0, (
        centroid_x / (3.0 * twice_area),
        centroid_y / (3.0 * twice_area),
    )


def parse_building_gml(payload: bytes, epsg: int) -> list[dict[str, object]]:
    """Convert Cadastre Building exteriors to planner semantic polygons."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise CatastroError(f"Catastro returned invalid GML: {error}") from error
    exception = root.find(".//{http://www.opengis.net/ows/1.1}ExceptionText")
    if exception is not None:
        message = " ".join((exception.text or "").split())
        if "No records" in message:
            return []
        raise CatastroError(message or "Catastro WFS returned an exception.")

    _, to_wgs84 = _transformers(epsg)
    features: list[dict[str, object]] = []
    namespaces = {"bu": _BUILDING, "gml": _GML}
    for building_index, building in enumerate(root.findall(".//bu:Building", namespaces)):
        building_id = building.attrib.get(f"{{{_GML}}}id", f"building-{building_index + 1}")
        exteriors = building.findall(".//gml:exterior//gml:posList", namespaces)
        if not exteriors:
            exteriors = building.findall(".//gml:posList", namespaces)[:1]
        for part_index, position_list in enumerate(exteriors):
            try:
                values = [float(value) for value in (position_list.text or "").split()]
            except ValueError:
                continue
            projected = list(zip(values[0::2], values[1::2]))
            if len(projected) < 4:
                continue
            if projected[0] != projected[-1]:
                projected.append(projected[0])
            area_m2, center_xy = _polygon_metrics(projected)
            if area_m2 < 1.0:
                continue
            ring: list[list[float]] = []
            for easting, northing in projected:
                longitude, latitude = to_wgs84.transform(easting, northing)
                ring.append([latitude, longitude])
            center_lon, center_lat = to_wgs84.transform(*center_xy)
            features.append(
                {
                    "id": f"catastro/{building_id}/{part_index + 1}",
                    "source": SOURCE_NAME,
                    "dataset": DATASET_NAME,
                    "kind": "building",
                    "name": None,
                    "ring": ring,
                    "bbox": {
                        "south": min(point[0] for point in ring),
                        "west": min(point[1] for point in ring),
                        "north": max(point[0] for point in ring),
                        "east": max(point[1] for point in ring),
                    },
                    "center": [center_lat, center_lon],
                    "areaM2": area_m2,
                }
            )
    return features


class CatastroBuildingSource:
    """Fetch and persist bounded official building queries."""

    def __init__(
        self,
        cache_directory: str | Path,
        timeout_s: float = 120.0,
        cache_max_age_days: float = 7.0,
    ):
        self.cache_directory = Path(cache_directory)
        self.timeout_s = float(timeout_s)
        self.cache_max_age_s = float(cache_max_age_days) * 86_400.0
        self._lock = threading.Lock()

    @staticmethod
    def _cache_signature(bbox: BoundingBox) -> str:
        coordinates = ",".join(
            f"{value:.7f}"
            for value in (bbox.south, bbox.west, bbox.north, bbox.east)
        )
        return hashlib.sha256(coordinates.encode("ascii")).hexdigest()[:20]

    def _cache_path(self, bbox: BoundingBox) -> Path:
        return self.cache_directory / f"buildings_{self._cache_signature(bbox)}.json"

    def _read_cache(self, path: Path) -> CatastroResult | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = int(payload["fetchedAtUnix"])
            if self.cache_max_age_s >= 0 and time.time() - fetched_at > self.cache_max_age_s:
                return None
            return CatastroResult(
                features=list(payload["features"]),
                cached=True,
                fetched_at_unix=fetched_at,
                query_epsg=int(payload["queryEpsg"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def fetch(self, bbox: BoundingBox) -> CatastroResult:
        if not supports_bbox(bbox):
            return CatastroResult([], False, int(time.time()), 0)
        cache_path = self._cache_path(bbox)
        with self._lock:
            cached = self._read_cache(cache_path)
            if cached is not None:
                return cached
            url, epsg = build_wfs_url(bbox)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "GeoZigzag/1.0 (+agricultural-route-planning)"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    payload = response.read(20_000_001)
            except Exception as error:
                raise CatastroError(f"Could not download Catastro buildings: {error}") from error
            if len(payload) > 20_000_000:
                raise CatastroError("Catastro response exceeded the 20 MB safety limit.")
            features = parse_building_gml(payload, epsg)
            fetched_at = int(time.time())
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_payload = {
                "schema": "geozigzag-catastro-cache-v1",
                "source": SOURCE_NAME,
                "dataset": DATASET_NAME,
                "fetchedAtUnix": fetched_at,
                "queryEpsg": epsg,
                "features": features,
            }
            temporary = cache_path.with_suffix(".json.part")
            temporary.write_text(
                json.dumps(cache_payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(cache_path)
            return CatastroResult(features, False, fetched_at, epsg)

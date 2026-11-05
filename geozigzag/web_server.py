#!/usr/bin/env python3
"""Preload OSM semantic polygons locally for GeoZigzag Studio.

The historical filename is kept for compatibility. The downloader now stores
buildings, water, forest and scrub polygons from Overpass.

Examples:
  # Preload the current demo mission area and then serve the folder:
  python3 scripts/osm_buildings_preload.py --poi-ids water_1 arbustivo_2 water_2 --buffer-m 120 --serve

  # Preload a custom bbox:
  python3 scripts/osm_buildings_preload.py --bbox 42.3085 -6.2080 42.3130 -6.2000 --serve

  # Preload around a GPS point:
  python3 scripts/osm_buildings_preload.py --center 42.310665 -6.207228 --radius-m 250 --serve
"""

from __future__ import annotations

import argparse
from functools import partial
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Any
from urllib.parse import parse_qs, urlparse

EARTH_RADIUS_M = 6_378_137.0
BUILDING_TILE_DEG = 0.0012  # Must match HTML constant.
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "web" / "osm_semantic_cache"
DEFAULT_DEM_CACHE = REPO_ROOT / "data" / "dem_cache" / "terrarium"
DEFAULT_CATASTRO_CACHE = REPO_ROOT / "data" / "catastro_cache"
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    # z.overpass-api.de often returns HTTP 406 for some clients/queries; keep it last.
    "https://z.overpass-api.de/api/interpreter",
]
USER_AGENT = "GeoZigzagSemanticPreloader/2.0 (+local route planning)"
SEMANTIC_CACHE_LOCK = threading.Lock()

# Same demo POIs as the HTML.
POIS: dict[str, tuple[float, float]] = {
    "pastizal_1": (42.310665, -6.207228),
    "pastizal_2": (42.309610, -6.201653),
    "pastizal_3": (42.310527, -6.203930),
    "arbustivo_1": (42.310360, -6.204549),
    "arbustivo_2": (42.310902, -6.206416),
    "matorral_1": (42.310832, -6.203042),
    "matorral_2": (42.311749, -6.205465),
    "water_1": (42.309282, -6.204025),
    "water_2": (42.312561, -6.204347),
    "village_east": (42.312900, -6.200150),
}


@dataclass(frozen=True)
class BBox:
    south: float
    west: float
    north: float
    east: float

    def expanded_m(self, meters: float) -> "BBox":
        mid_lat = (self.south + self.north) / 2.0
        lat_pad = meters / 111_320.0
        lon_pad = meters / (111_320.0 * max(0.2, math.cos(math.radians(mid_lat))))
        return BBox(
            south=self.south - lat_pad,
            west=self.west - lon_pad,
            north=self.north + lat_pad,
            east=self.east + lon_pad,
        )

    def as_dict(self) -> dict[str, float]:
        return {"south": self.south, "west": self.west, "north": self.north, "east": self.east}


def ll_bbox(points: Iterable[tuple[float, float]]) -> BBox:
    pts = list(points)
    if not pts:
        raise ValueError("No points supplied for bbox.")
    return BBox(
        south=min(p[0] for p in pts),
        west=min(p[1] for p in pts),
        north=max(p[0] for p in pts),
        east=max(p[1] for p in pts),
    )


def center_bbox(lat: float, lon: float, radius_m: float) -> BBox:
    return ll_bbox([(lat, lon)]).expanded_m(radius_m)


def sample_dem_grid(
    elevation_model: Any, bbox: BBox, rows: int, cols: int
) -> list[list[float]]:
    """Sample cell-centre elevations for the browser's local cost grid."""
    if bbox.south >= bbox.north or bbox.west >= bbox.east:
        raise ValueError("Invalid DEM bounding-box order.")
    if rows < 2 or cols < 2:
        raise ValueError("DEM grid needs at least two rows and columns.")
    if rows * cols > 40_000:
        raise ValueError("DEM grid exceeds the 40,000-cell safety limit.")
    values: list[list[float]] = []
    for row in range(rows):
        latitude = bbox.south + (row + 0.5) / rows * (bbox.north - bbox.south)
        value_row: list[float] = []
        for col in range(cols):
            longitude = bbox.west + (col + 0.5) / cols * (bbox.east - bbox.west)
            elevation = float(elevation_model.elevation_m(latitude, longitude))
            if not math.isfinite(elevation):
                raise ValueError(f"DEM returned a non-finite value at ({row}, {col}).")
            value_row.append(elevation)
        values.append(value_row)
    return values


def build_elevation_model(
    *,
    terrain_world: str | Path | None = None,
    dem_geotiff: str | Path | None = None,
    default_real_dem: bool = True,
    dem_cache: str | Path = DEFAULT_DEM_CACHE,
    dem_zoom: int = 15,
) -> Any | None:
    """Configure one explicit DEM source or the real public default."""
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from geozigzag.elevation import (
        GeoTiffElevation,
        MapboxTerrainRgbDirectory,
        TerrariumTileElevation,
    )

    if terrain_world:
        return MapboxTerrainRgbDirectory.from_terrain_world(terrain_world)
    if dem_geotiff:
        return GeoTiffElevation(dem_geotiff)
    if default_real_dem:
        return TerrariumTileElevation(dem_cache, zoom=dem_zoom)
    return None


def public_dem_provenance(model: Any | None) -> dict[str, Any]:
    """Remove local filesystem paths before returning provenance to browsers."""
    provenance = model.provenance() if model is not None else {}
    private_keys = {"dem_directory", "cache_directory", "path"}
    return {key: value for key, value in provenance.items() if key not in private_keys}


def bbox_from_geojson(path: Path, buffer_m: float) -> BBox:
    data = json.loads(path.read_text(encoding="utf-8"))
    points: list[tuple[float, float]] = []

    def walk(coords: Any) -> None:
        if isinstance(coords, list) and len(coords) >= 2 and all(isinstance(v, (int, float)) for v in coords[:2]):
            lon, lat = coords[:2]
            points.append((float(lat), float(lon)))
        elif isinstance(coords, list):
            for item in coords:
                walk(item)

    if data.get("type") == "FeatureCollection":
        for feature in data.get("features", []):
            walk(feature.get("geometry", {}).get("coordinates"))
    elif data.get("type") == "Feature":
        walk(data.get("geometry", {}).get("coordinates"))
    else:
        walk(data.get("coordinates"))
    return ll_bbox(points).expanded_m(buffer_m)


def tile_key(x: int, y: int) -> str:
    return f"{x}:{y}"


def tile_filename(key: str) -> str:
    return "tile_" + key.replace(":", "_") + ".json"


def tiles_for_bbox(bbox: BBox) -> list[tuple[str, BBox]]:
    west = math.floor(bbox.west / BUILDING_TILE_DEG)
    east = math.floor(bbox.east / BUILDING_TILE_DEG)
    south = math.floor(bbox.south / BUILDING_TILE_DEG)
    north = math.floor(bbox.north / BUILDING_TILE_DEG)
    out: list[tuple[str, BBox]] = []
    for y in range(south, north + 1):
        for x in range(west, east + 1):
            key = tile_key(x, y)
            out.append((key, BBox(
                south=y * BUILDING_TILE_DEG,
                west=x * BUILDING_TILE_DEG,
                north=(y + 1) * BUILDING_TILE_DEG,
                east=(x + 1) * BUILDING_TILE_DEG,
            )))
    return out


def overpass_query(bbox: BBox, server_timeout: int = 60) -> str:
    b = bbox
    # Keep the query explicit instead of nwr[] so old Overpass instances behave consistently.
    return (
        f"[out:json][timeout:{max(10, int(server_timeout))}];\n"
        "(\n"
        f"  way[\"building\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"building\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  way[\"natural\"=\"water\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"natural\"=\"water\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  way[\"landuse\"=\"reservoir\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"landuse\"=\"reservoir\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  way[\"natural\"=\"wood\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"natural\"=\"wood\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  way[\"landuse\"=\"forest\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"landuse\"=\"forest\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  way[\"natural\"=\"scrub\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"natural\"=\"scrub\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  way[\"natural\"=\"shrubbery\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"natural\"=\"shrubbery\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  way[\"natural\"=\"heath\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"natural\"=\"heath\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  way[\"landcover\"=\"shrubs\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        f"  relation[\"landcover\"=\"shrubs\"]({b.south:.8f},{b.west:.8f},{b.north:.8f},{b.east:.8f});\n"
        ");\n"
        "out geom;"
    )


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
        body = " ".join(body.split())
        return f"HTTP {exc.code}: {body[:220]}"
    except Exception:
        return f"HTTP {exc.code}: {exc.reason}"


def post_overpass(endpoint: str, query: str, timeout: int) -> dict[str, Any]:
    """Try several Overpass request encodings.

    Some public mirrors reject one encoding with 406 but accept another.  We
    therefore try: POST form, POST raw query, and GET as a last resort.
    """
    headers_base = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    attempts: list[tuple[str, str, bytes | None, dict[str, str]]] = []
    attempts.append((
        "POST form",
        endpoint,
        urllib.parse.urlencode({"data": query}).encode("utf-8"),
        {**headers_base, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
    ))
    attempts.append((
        "POST raw",
        endpoint,
        query.encode("utf-8"),
        {**headers_base, "Content-Type": "text/plain; charset=UTF-8"},
    ))
    # GET can be useful for tiny tile queries and for mirrors that dislike POST.
    attempts.append((
        "GET",
        endpoint + "?" + urllib.parse.urlencode({"data": query}),
        None,
        headers_base,
    ))

    errors: list[str] = []
    for label, url, body, headers in attempts:
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = response.read()
            return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            errors.append(f"{label}: {_read_http_error(exc)}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            errors.append(f"{label}: {exc}")
            break
    raise RuntimeError("; ".join(errors))


def close_ring(ring: list[tuple[float, float]]) -> list[list[float]]:
    clean = [[float(lat), float(lon)] for lat, lon in ring if math.isfinite(lat) and math.isfinite(lon)]
    if len(clean) < 3:
        return []
    if abs(clean[0][0] - clean[-1][0]) > 1e-10 or abs(clean[0][1] - clean[-1][1]) > 1e-10:
        clean.append([clean[0][0], clean[0][1]])
    return clean if len(clean) >= 4 else []


def same_ll(a: list[float], b: list[float], eps: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def join_outer_fragments(fragments: list[list[list[float]]]) -> list[list[list[float]]]:
    remaining = [frag[:] for frag in fragments if len(frag) >= 2]
    rings: list[list[list[float]]] = []
    while remaining:
        ring = remaining.pop(0)
        changed = True
        while changed:
            changed = False
            for i, frag in enumerate(remaining):
                if same_ll(ring[-1], frag[0]):
                    ring = ring + frag[1:]
                elif same_ll(ring[-1], frag[-1]):
                    ring = ring + list(reversed(frag[:-1]))
                elif same_ll(ring[0], frag[-1]):
                    ring = frag[:-1] + ring
                elif same_ll(ring[0], frag[0]):
                    ring = list(reversed(frag[1:])) + ring
                else:
                    continue
                remaining.pop(i)
                changed = True
                break
        closed = close_ring([(lat, lon) for lat, lon in ring])
        if closed:
            rings.append(closed)
    return rings


def route_bbox(ring: list[list[float]]) -> dict[str, float]:
    return {
        "south": min(p[0] for p in ring),
        "west": min(p[1] for p in ring),
        "north": max(p[0] for p in ring),
        "east": max(p[1] for p in ring),
    }


def bbox_overlap(a: dict[str, float], b: BBox) -> bool:
    return not (a["east"] < b.west or a["west"] > b.east or a["north"] < b.south or a["south"] > b.north)


def semantic_kind(tags: dict[str, Any]) -> str | None:
    if tags.get("building"):
        return "building"
    if tags.get("natural") == "water" or tags.get("landuse") == "reservoir":
        return "water"
    if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
        return "forest"
    if tags.get("natural") in {"scrub", "shrubbery", "heath"} or tags.get("landcover") == "shrubs":
        return "scrub"
    return None


def clip_ring_to_bbox(ring: list[list[float]], bbox: BBox) -> list[list[float]]:
    points = close_ring([(point[0], point[1]) for point in ring])
    points = points[:-1] if points else []

    def clip(inside: Any, intersect: Any) -> None:
        nonlocal points
        if not points:
            return
        output: list[list[float]] = []
        previous = points[-1]
        for current in points:
            current_inside = inside(current)
            previous_inside = inside(previous)
            if current_inside:
                if not previous_inside:
                    output.append(intersect(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersect(previous, current))
            previous = current
        points = output

    def at_lon(first: list[float], second: list[float], lon: float) -> list[float]:
        ratio = (lon - first[1]) / ((second[1] - first[1]) or 1e-15)
        return [first[0] + (second[0] - first[0]) * ratio, lon]

    def at_lat(first: list[float], second: list[float], lat: float) -> list[float]:
        ratio = (lat - first[0]) / ((second[0] - first[0]) or 1e-15)
        return [lat, first[1] + (second[1] - first[1]) * ratio]

    clip(lambda point: point[1] >= bbox.west, lambda a, b: at_lon(a, b, bbox.west))
    clip(lambda point: point[1] <= bbox.east, lambda a, b: at_lon(a, b, bbox.east))
    clip(lambda point: point[0] >= bbox.south, lambda a, b: at_lat(a, b, bbox.south))
    clip(lambda point: point[0] <= bbox.north, lambda a, b: at_lat(a, b, bbox.north))
    return close_ring([(point[0], point[1]) for point in points])


def polygon_area_center(ring: list[list[float]]) -> tuple[float, list[float]]:
    """Return approximate metric area and an area-weighted WGS84 center."""
    points = ring[:-1] if len(ring) > 1 and same_ll(ring[0], ring[-1]) else ring
    origin_lat = sum(point[0] for point in points) / len(points)
    origin_lon = sum(point[1] for point in points) / len(points)
    scale = math.cos(math.radians(origin_lat))
    xy = [
        (
            math.radians(lon - origin_lon) * EARTH_RADIUS_M * scale,
            math.radians(lat - origin_lat) * EARTH_RADIUS_M,
        )
        for lat, lon in points
    ]
    twice_area = 0.0
    center_x = 0.0
    center_y = 0.0
    for first, second in zip(xy, xy[1:] + xy[:1]):
        cross = first[0] * second[1] - second[0] * first[1]
        twice_area += cross
        center_x += (first[0] + second[0]) * cross
        center_y += (first[1] + second[1]) * cross
    area_m2 = abs(twice_area) / 2.0
    if abs(twice_area) > 1e-9:
        center_x /= 3.0 * twice_area
        center_y /= 3.0 * twice_area
    else:
        center_x = sum(point[0] for point in xy) / len(xy)
        center_y = sum(point[1] for point in xy) / len(xy)
    center = [
        origin_lat + math.degrees(center_y / EARTH_RADIUS_M),
        origin_lon + math.degrees(center_x / (EARTH_RADIUS_M * max(scale, 1e-9))),
    ]
    return area_m2, center


def parse_semantic_features(data: dict[str, Any], tile_bbox: BBox) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_feature(prefix: str, tags: dict[str, Any], ring: list[list[float]]) -> None:
        ring = clip_ring_to_bbox(ring, tile_bbox)
        if not ring:
            return
        kind = semantic_kind(tags)
        if kind is None:
            return
        bbox = route_bbox(ring)
        if not bbox_overlap(bbox, tile_bbox):
            return
        key = f"{prefix}/{kind}:" + "|".join(f"{p[0]:.7f},{p[1]:.7f}" for p in ring[:4])
        if key in seen:
            return
        seen.add(key)
        area_m2, center = polygon_area_center(ring)
        if area_m2 < 1.0:
            return
        features.append(
            {
                "id": key,
                "source": "OpenStreetMap",
                "kind": kind,
                "name": tags.get("name"),
                "ring": ring,
                "bbox": bbox,
                "center": center,
                "areaM2": area_m2,
            }
        )

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el.get("type") == "way" and semantic_kind(tags) and isinstance(el.get("geometry"), list):
            ring = close_ring([(float(p["lat"]), float(p["lon"])) for p in el["geometry"] if "lat" in p and "lon" in p])
            add_feature(f"way/{el.get('id')}", tags, ring)

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el.get("type") != "relation" or semantic_kind(tags) is None:
            continue
        fragments: list[list[list[float]]] = []
        for m in el.get("members", []) or []:
            if m.get("role") not in (None, "", "outer"):
                continue
            geom = m.get("geometry")
            if not isinstance(geom, list):
                continue
            frag = [[float(p["lat"]), float(p["lon"])] for p in geom if "lat" in p and "lon" in p]
            if len(frag) >= 2:
                fragments.append(frag)
        for ring in join_outer_fragments(fragments):
            add_feature(f"relation/{el.get('id')}", tags, ring)

    return features


def parse_buildings(data: dict[str, Any], tile_bbox: BBox) -> list[dict[str, Any]]:
    """Compatibility helper for callers that still need only buildings."""
    return [
        feature
        for feature in parse_semantic_features(data, tile_bbox)
        if feature["kind"] == "building"
    ]


def _try_download_bbox(bbox: BBox, timeout: int, retries: int, sleep_s: float, label: str = "bbox") -> list[dict[str, Any]]:
    last_error: str | None = None
    query = overpass_query(bbox, server_timeout=max(timeout, 30))
    for attempt in range(retries + 1):
        for endpoint in ENDPOINTS:
            try:
                print(f"    Overpass {label}: {endpoint}", flush=True)
                data = post_overpass(endpoint, query, timeout=timeout)
                return parse_semantic_features(data, bbox)
            except Exception as exc:
                last_error = f"{endpoint}: {exc}"
                print(f"      failed: {last_error[:260]}", flush=True)
        if attempt < retries:
            time.sleep(sleep_s)
    raise RuntimeError(last_error or f"Could not download {label}")


def split_bbox(bbox: BBox, rows: int, cols: int) -> list[BBox]:
    out: list[BBox] = []
    lat_step = (bbox.north - bbox.south) / rows
    lon_step = (bbox.east - bbox.west) / cols
    for r in range(rows):
        for c in range(cols):
            out.append(BBox(
                south=bbox.south + r * lat_step,
                west=bbox.west + c * lon_step,
                north=bbox.south + (r + 1) * lat_step,
                east=bbox.west + (c + 1) * lon_step,
            ))
    return out


def download_bbox_robust(bbox: BBox, timeout: int, retries: int, sleep_s: float) -> list[dict[str, Any]]:
    """Download a whole zone, falling back to smaller chunks if needed."""
    try:
        return _try_download_bbox(bbox, timeout=timeout, retries=retries, sleep_s=sleep_s, label="full bbox")
    except Exception as first_error:
        print(f"Full bbox failed; trying 2x2 chunks. Reason: {first_error}", flush=True)

    buildings: list[dict[str, Any]] = []
    seen: set[str] = set()
    chunk_errors: list[str] = []
    for idx, chunk in enumerate(split_bbox(bbox, 2, 2), start=1):
        try:
            chunk_buildings = _try_download_bbox(chunk, timeout=timeout, retries=retries, sleep_s=sleep_s, label=f"chunk {idx}/4")
            for b in chunk_buildings:
                bid = str(b.get("id", ""))
                if bid and bid not in seen:
                    seen.add(bid)
                    buildings.append(b)
            time.sleep(sleep_s)
        except Exception as exc:
            chunk_errors.append(str(exc))
    if buildings or not chunk_errors:
        return buildings
    raise RuntimeError("; ".join(chunk_errors))


def download_tile(tile_key_: str, tile_bbox: BBox, timeout: int, retries: int, sleep_s: float) -> list[dict[str, Any]]:
    try:
        return _try_download_bbox(tile_bbox, timeout=timeout, retries=retries, sleep_s=sleep_s, label=f"tile {tile_key_}")
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def write_manifest(out_dir: Path, tile_records: list[dict[str, Any]], bbox: BBox) -> None:
    manifest = {
        "schema": "geozigzag-osm-semantic-cache-v2",
        "tileDeg": BUILDING_TILE_DEG,
        "savedAt": int(time.time() * 1000),
        "bbox": bbox.as_dict(),
        "tiles": tile_records,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def cached_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("features", payload.get("buildings", [])))


def kind_counts(features: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {"building": 0, "water": 0, "forest": 0, "scrub": 0}
    for feature in features:
        kind = str(feature.get("kind", "building"))
        if kind in counts:
            counts[kind] += 1
    return counts


def read_cached_bbox(out_dir: Path, bbox: BBox) -> list[dict[str, Any]] | None:
    """Return a complete cached bbox, or ``None`` when any tile is missing."""
    collected: dict[str, dict[str, Any]] = {}
    for key, _ in tiles_for_bbox(bbox):
        path = out_dir / tile_filename(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        for feature in cached_features(payload):
            if not bbox_overlap(feature.get("bbox", {}), bbox):
                continue
            collected.setdefault(str(feature.get("id")), feature)
    normalized_features: list[dict[str, Any]] = []
    for feature in collected.values():
        clipped = clip_ring_to_bbox(feature.get("ring", []), bbox)
        if not clipped:
            continue
        area_m2, center = polygon_area_center(clipped)
        if area_m2 < 1.0:
            continue
        normalized_features.append(
            {
                **feature,
                "ring": clipped,
                "bbox": route_bbox(clipped),
                "center": center,
                "areaM2": area_m2,
            }
        )
    return normalized_features


def tile_cover_bbox(bbox: BBox) -> BBox:
    """Return the tile-aligned area needed to make ``bbox`` fully cacheable."""
    tiles = tiles_for_bbox(bbox)
    return BBox(
        south=min(tile.south for _, tile in tiles),
        west=min(tile.west for _, tile in tiles),
        north=max(tile.north for _, tile in tiles),
        east=max(tile.east for _, tile in tiles),
    )


def write_semantic_bbox_cache(
    out_dir: Path, bbox: BBox, features: list[dict[str, Any]]
) -> None:
    """Atomically store complete tile-aligned semantic query results."""
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for key, tile_bbox in tiles_for_bbox(bbox):
        tile_features = [
            feature
            for feature in features
            if bbox_overlap(feature.get("bbox", {}), tile_bbox)
        ]
        payload = {
            "schema": "geozigzag-osm-semantic-tile-v2",
            "savedAt": int(time.time() * 1000),
            "tileKey": key,
            "tileDeg": BUILDING_TILE_DEG,
            "bbox": tile_bbox.as_dict(),
            "features": tile_features,
        }
        destination = out_dir / tile_filename(key)
        temporary = destination.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(destination)
        records.append(
            {
                "key": key,
                "file": destination.name,
                "bbox": tile_bbox.as_dict(),
                "features": len(tile_features),
                "kinds": kind_counts(tile_features),
            }
        )
    write_manifest(out_dir, records, bbox)


def download_and_cache_semantic_bbox(out_dir: Path, requested_bbox: BBox) -> list[dict[str, Any]]:
    """Download complete OSM tiles once, then return data clipped to the request."""
    with SEMANTIC_CACHE_LOCK:
        cached = read_cached_bbox(out_dir, requested_bbox)
        if cached is not None:
            return cached
        coverage = tile_cover_bbox(requested_bbox)
        features = download_bbox_robust(
            coverage, timeout=25, retries=0, sleep_s=0.0
        )
        write_semantic_bbox_cache(out_dir, coverage, features)
        return read_cached_bbox(out_dir, requested_bbox) or []


def preload(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.bbox:
        bbox = BBox(*map(float, args.bbox))
    elif args.center:
        bbox = center_bbox(float(args.center[0]), float(args.center[1]), float(args.radius_m))
    elif args.geojson:
        bbox = bbox_from_geojson(Path(args.geojson), float(args.buffer_m))
    else:
        ids = args.poi_ids or ["water_1", "arbustivo_2", "water_2"]
        missing = [pid for pid in ids if pid not in POIS]
        if missing:
            raise SystemExit(f"Unknown POI ids: {', '.join(missing)}. Available: {', '.join(POIS)}")
        bbox = ll_bbox([POIS[pid] for pid in ids]).expanded_m(float(args.buffer_m))

    tiles = tiles_for_bbox(bbox)
    if len(tiles) > args.max_tiles:
        raise SystemExit(f"Refusing to download {len(tiles)} tiles. Increase --max-tiles or reduce bbox/buffer.")

    print(f"BBox: {bbox.as_dict()}")
    print(f"Tiles: {len(tiles)} · output: {out_dir.resolve()}")

    records: list[dict[str, Any]] = []
    total_features = 0

    if not args.per_tile:
        missing_tiles: list[tuple[str, BBox, Path]] = []
        for key, tile_bbox in tiles:
            out_file = out_dir / tile_filename(key)
            if out_file.exists() and not args.force:
                try:
                    cached = json.loads(out_file.read_text(encoding="utf-8"))
                    count = len(cached_features(cached))
                    print(f"cached {key}: {count} semantic polygons")
                    total_features += count
                    records.append({"key": key, "file": out_file.name, "bbox": tile_bbox.as_dict(), "features": count, "kinds": kind_counts(cached_features(cached))})
                    continue
                except Exception:
                    pass
            missing_tiles.append((key, tile_bbox, out_file))

        if missing_tiles:
            missing_bbox = BBox(
                south=min(t[1].south for t in missing_tiles),
                west=min(t[1].west for t in missing_tiles),
                north=max(t[1].north for t in missing_tiles),
                east=max(t[1].east for t in missing_tiles),
            )
            print(f"Downloading semantic polygons once for a zone covering {len(missing_tiles)} tiles ...", flush=True)
            all_buildings = download_bbox_robust(missing_bbox, timeout=args.timeout, retries=args.retries, sleep_s=args.sleep)
            print(f"Downloaded {len(all_buildings)} unique semantic polygons; writing tiles ...", flush=True)
            for i, (key, tile_bbox, out_file) in enumerate(missing_tiles, start=1):
                tile_buildings = [b for b in all_buildings if bbox_overlap(b.get("bbox", {}), tile_bbox)]
                payload = {
                    "schema": "geozigzag-osm-semantic-tile-v2",
                    "savedAt": int(time.time() * 1000),
                    "tileKey": key,
                    "tileDeg": BUILDING_TILE_DEG,
                    "bbox": tile_bbox.as_dict(),
                    "features": tile_buildings,
                }
                out_file.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                total_features += len(tile_buildings)
                records.append({"key": key, "file": out_file.name, "bbox": tile_bbox.as_dict(), "features": len(tile_buildings), "kinds": kind_counts(tile_buildings)})
                print(f"[{i:03d}/{len(missing_tiles):03d}] wrote {key}: {len(tile_buildings)} semantic polygons")
    else:
        for i, (key, tile_bbox) in enumerate(tiles, start=1):
            out_file = out_dir / tile_filename(key)
            if out_file.exists() and not args.force:
                try:
                    cached = json.loads(out_file.read_text(encoding="utf-8"))
                    count = len(cached_features(cached))
                    print(f"[{i:03d}/{len(tiles):03d}] cached {key}: {count} semantic polygons")
                    total_features += count
                    records.append({"key": key, "file": out_file.name, "bbox": tile_bbox.as_dict(), "features": count, "kinds": kind_counts(cached_features(cached))})
                    continue
                except Exception:
                    pass

            print(f"[{i:03d}/{len(tiles):03d}] downloading {key} ...", flush=True)
            buildings = download_tile(key, tile_bbox, timeout=args.timeout, retries=args.retries, sleep_s=args.sleep)
            payload = {
                "schema": "geozigzag-osm-semantic-tile-v2",
                "savedAt": int(time.time() * 1000),
                "tileKey": key,
                "tileDeg": BUILDING_TILE_DEG,
                "bbox": tile_bbox.as_dict(),
                "features": buildings,
            }
            out_file.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            total_features += len(buildings)
            records.append({"key": key, "file": out_file.name, "bbox": tile_bbox.as_dict(), "features": len(buildings), "kinds": kind_counts(buildings)})
            time.sleep(args.sleep)

    write_manifest(out_dir, records, bbox)
    print(f"Done: {total_features} tile feature references across {len(tiles)} tiles.")
    if total_features == 0:
        print("\nWARNING: Overpass answered successfully, but no supported semantic polygons were found.")
        print("Zoom out slightly or confirm that the features are mapped in OpenStreetMap.")
    if args.serve:
        serve(
            args.port,
            auto_port=args.auto_port,
            max_port_tries=args.max_port_tries,
            terrain_world=args.terrain_world,
            dem_geotiff=args.dem_geotiff,
            default_real_dem=not args.no_dem,
            dem_cache=args.dem_cache,
            dem_zoom=args.dem_zoom,
            catastro_enabled=not args.no_catastro,
            catastro_cache=args.catastro_cache,
            catastro_cache_days=args.catastro_cache_days,
        )


class SemanticRequestHandler(SimpleHTTPRequestHandler):
    """Serve static files and proxy bounded semantic Overpass requests."""

    api_max_area_m2 = 9_000_000.0
    catastro_max_area_m2 = 4_000_000.0
    elevation_model: Any | None = None
    catastro_source: Any | None = None

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/dem/status":
            model = self.elevation_model
            self._json_response(
                200,
                {
                    "available": model is not None,
                    "source": public_dem_provenance(model),
                },
            )
            return
        if parsed.path == "/api/dem/grid":
            try:
                if self.elevation_model is None:
                    self._json_response(
                        503,
                        {"error": "No DEM configured. Restart the server with --terrain-world."},
                    )
                    return
                query = parse_qs(parsed.query)
                bbox = BBox(
                    south=float(query["south"][0]),
                    west=float(query["west"][0]),
                    north=float(query["north"][0]),
                    east=float(query["east"][0]),
                )
                rows = int(query["rows"][0])
                cols = int(query["cols"][0])
                elevations = sample_dem_grid(self.elevation_model, bbox, rows, cols)
                self._json_response(
                    200,
                    {
                        "schema": "geozigzag-dem-grid-v1",
                        "bbox": bbox.as_dict(),
                        "rows": rows,
                        "cols": cols,
                        "elevations_m": elevations,
                        "source": public_dem_provenance(self.elevation_model),
                    },
                )
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                self._json_response(400, {"error": str(exc)})
            except Exception as exc:
                self._json_response(502, {"error": f"DEM sampling failed: {exc}"})
            return
        if parsed.path == "/api/catastro/buildings":
            try:
                if self.catastro_source is None:
                    self._json_response(503, {"error": "Catastro building service is disabled."})
                    return
                query = parse_qs(parsed.query)
                bbox = BBox(
                    south=float(query["south"][0]),
                    west=float(query["west"][0]),
                    north=float(query["north"][0]),
                    east=float(query["east"][0]),
                )
                if bbox.south >= bbox.north or bbox.west >= bbox.east:
                    raise ValueError("Invalid bounding-box order.")
                mid_lat = (bbox.south + bbox.north) / 2.0
                width = (bbox.east - bbox.west) * 111_320.0 * max(
                    0.2, math.cos(math.radians(mid_lat))
                )
                height = (bbox.north - bbox.south) * 111_320.0
                if width * height > self.catastro_max_area_m2:
                    raise ValueError("Requested area exceeds the Catastro 4 km² safety limit.")
                result = self.catastro_source.fetch(bbox)
                self._json_response(
                    200,
                    {
                        "schema": "geozigzag-catastro-buildings-response-v1",
                        "source": "Dirección General del Catastro",
                        "dataset": "Catastro INSPIRE Buildings (BU)",
                        "bbox": bbox.as_dict(),
                        "features": result.features,
                        "count": len(result.features),
                        "cached": result.cached,
                        "fetchedAtUnix": result.fetched_at_unix,
                        "queryEpsg": result.query_epsg,
                    },
                )
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                self._json_response(400, {"error": str(exc)})
            except Exception as exc:
                self._json_response(502, {"error": f"Catastro building download failed: {exc}"})
            return
        if parsed.path != "/api/osm/semantic":
            super().do_GET()
            return
        try:
            query = parse_qs(parsed.query)
            bbox = BBox(
                south=float(query["south"][0]),
                west=float(query["west"][0]),
                north=float(query["north"][0]),
                east=float(query["east"][0]),
            )
            if bbox.south >= bbox.north or bbox.west >= bbox.east:
                raise ValueError("Invalid bounding-box order.")
            mid_lat = (bbox.south + bbox.north) / 2.0
            width = (bbox.east - bbox.west) * 111_320.0 * max(
                0.2, math.cos(math.radians(mid_lat))
            )
            height = (bbox.north - bbox.south) * 111_320.0
            if width * height > self.api_max_area_m2:
                raise ValueError("Requested area exceeds the 9 km² safety limit.")
            features = read_cached_bbox(DEFAULT_OUT_DIR, bbox)
            source = "local semantic cache"
            if features is None:
                features = download_and_cache_semantic_bbox(DEFAULT_OUT_DIR, bbox)
                source = "OpenStreetMap/Overpass (cached)"
            self._json_response(
                200,
                {
                    "schema": "geozigzag-osm-semantic-response-v1",
                    "source": source,
                    "bbox": bbox.as_dict(),
                    "features": features,
                    "counts": kind_counts(features),
                },
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            self._json_response(400, {"error": str(exc)})
        except Exception as exc:
            self._json_response(502, {"error": f"Overpass download failed: {exc}"})


def serve(
    port: int,
    auto_port: bool = True,
    max_port_tries: int = 20,
    terrain_world: str | Path | None = None,
    dem_geotiff: str | Path | None = None,
    default_real_dem: bool = True,
    dem_cache: str | Path = DEFAULT_DEM_CACHE,
    dem_zoom: int = 15,
    catastro_enabled: bool = True,
    catastro_cache: str | Path = DEFAULT_CATASTRO_CACHE,
    catastro_cache_days: float = 7.0,
) -> None:
    SemanticRequestHandler.elevation_model = build_elevation_model(
        terrain_world=terrain_world,
        dem_geotiff=dem_geotiff,
        default_real_dem=default_real_dem,
        dem_cache=dem_cache,
        dem_zoom=dem_zoom,
    )
    if catastro_enabled:
        from geozigzag.catastro import CatastroBuildingSource

        SemanticRequestHandler.catastro_source = CatastroBuildingSource(
            catastro_cache,
            cache_max_age_days=catastro_cache_days,
        )
    else:
        SemanticRequestHandler.catastro_source = None
    handler = partial(SemanticRequestHandler, directory=str(REPO_ROOT))
    last_error: OSError | None = None
    selected_port = port
    tries = max(1, int(max_port_tries)) if auto_port else 1
    for offset in range(tries):
        selected_port = port + offset
        try:
            server = ThreadingHTTPServer(("0.0.0.0", selected_port), handler)
            break
        except OSError as exc:
            last_error = exc
            if exc.errno == 98 and auto_port:
                print(f"Port {selected_port} is already in use; trying {selected_port + 1} ...")
                continue
            raise
    else:
        raise OSError(f"Could not bind any port from {port} to {port + tries - 1}: {last_error}")

    print("\nLocal server started.")
    print(f"Open: http://localhost:{selected_port}/web/index.html")
    print("Semantic endpoints: /api/osm/semantic and /api/catastro/buildings")
    if SemanticRequestHandler.elevation_model is not None:
        source = public_dem_provenance(SemanticRequestHandler.elevation_model)
        print(f"Real DEM enabled: {source.get('type', 'configured source')}.")
        print("Endpoints: /api/dem/status and /api/dem/grid")
    else:
        print("DEM disabled explicitly. Terrain-aware A* will stop with a visible error.")
    print("Stop with Ctrl+C.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download OSM building, water, forest and scrub polygons for GeoZigzag."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--bbox", nargs=4, metavar=("SOUTH", "WEST", "NORTH", "EAST"), help="Bounding box to download.")
    source.add_argument("--center", nargs=2, metavar=("LAT", "LON"), help="Center point. Use with --radius-m.")
    source.add_argument("--geojson", help="Read bbox from GeoJSON file and expand by --buffer-m.")
    source.add_argument("--poi-ids", nargs="+", help="Built-in demo POI ids, e.g. water_1 arbustivo_2 water_2.")
    parser.add_argument("--radius-m", type=float, default=250.0, help="Radius for --center. Default: 250 m.")
    parser.add_argument("--buffer-m", type=float, default=120.0, help="Buffer around --geojson or --poi-ids bbox. Default: 120 m.")
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Output directory. Default: web/osm_semantic_cache.",
    )
    parser.add_argument("--timeout", type=int, default=45, help="Per-endpoint timeout in seconds. Default: 45.")
    parser.add_argument("--retries", type=int, default=2, help="Retries after trying all endpoints. Default: 2.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Pause between tile downloads. Default: 0.25 s.")
    parser.add_argument("--max-tiles", type=int, default=300, help="Safety cap. Default: 300 tiles.")
    parser.add_argument("--force", action="store_true", help="Redownload existing tiles.")
    parser.add_argument("--per-tile", action="store_true", help="Old behavior: download each cache tile separately. Default is faster: one bbox request then split locally.")
    parser.add_argument("--serve", action="store_true", help="Serve this folder after downloading.")
    parser.add_argument(
        "--serve-only",
        action="store_true",
        help="Start the web/API server without preloading a fixed area.",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for --serve. Default: 8000.")
    parser.add_argument("--auto-port", action=argparse.BooleanOptionalAction, default=True, help="If the port is busy, try the next ports automatically. Default: enabled.")
    parser.add_argument("--max-port-tries", type=int, default=20, help="How many ports to try when --auto-port is enabled. Default: 20.")
    dem_source = parser.add_mutually_exclusive_group()
    dem_source.add_argument(
        "--terrain-world",
        help="gazebo_terrain_generator directory containing metadata.json and dem/.",
    )
    dem_source.add_argument(
        "--dem-geotiff",
        help="Real single-band elevation GeoTIFF; its CRS is read from the file.",
    )
    dem_source.add_argument(
        "--no-dem",
        action="store_true",
        help="Disable the default real AWS Terrain Tiles DEM.",
    )
    parser.add_argument(
        "--dem-cache",
        default=str(DEFAULT_DEM_CACHE),
        help="Local cache for downloaded Terrarium tiles. Default: data/dem_cache/terrarium.",
    )
    parser.add_argument(
        "--dem-zoom",
        type=int,
        default=15,
        help="Terrarium zoom, between 0 and 15. Default: 15.",
    )
    parser.add_argument(
        "--no-catastro",
        action="store_true",
        help="Disable the default Spanish Cadastre INSPIRE building source.",
    )
    parser.add_argument(
        "--catastro-cache",
        default=str(DEFAULT_CATASTRO_CACHE),
        help="Local cache for Catastro GML responses. Default: data/catastro_cache.",
    )
    parser.add_argument(
        "--catastro-cache-days",
        type=float,
        default=7.0,
        help="Refresh cached Catastro footprints after this many days. Default: 7.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.serve_only:
        serve(
            args.port,
            auto_port=args.auto_port,
            max_port_tries=args.max_port_tries,
            terrain_world=args.terrain_world,
            dem_geotiff=args.dem_geotiff,
            default_real_dem=not args.no_dem,
            dem_cache=args.dem_cache,
            dem_zoom=args.dem_zoom,
            catastro_enabled=not args.no_catastro,
            catastro_cache=args.catastro_cache,
            catastro_cache_days=args.catastro_cache_days,
        )
    else:
        preload(args)


if __name__ == "__main__":
    main()

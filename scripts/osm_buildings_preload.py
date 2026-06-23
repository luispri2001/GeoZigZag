#!/usr/bin/env python3
"""Preload OSM buildings locally for GeoZigzag Studio.

This script downloads OSM building footprints from Overpass and stores them as
small tile JSON files in web/osm_buildings_cache so the HTML can validate and
draw local buildings without waiting for Overpass every time.

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
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Any

EARTH_RADIUS_M = 6_378_137.0
BUILDING_TILE_DEG = 0.0012  # Must match HTML constant.
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "web" / "osm_buildings_cache"
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    # z.overpass-api.de often returns HTTP 406 for some clients/queries; keep it last.
    "https://z.overpass-api.de/api/interpreter",
]
USER_AGENT = "GeoZigzagBuildingPreloader/1.2 (+local route validation)"

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


def parse_buildings(data: dict[str, Any], tile_bbox: BBox) -> list[dict[str, Any]]:
    buildings: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_building(prefix: str, ring: list[list[float]]) -> None:
        if not ring:
            return
        bbox = route_bbox(ring)
        if not bbox_overlap(bbox, tile_bbox):
            return
        key = prefix + ":" + "|".join(f"{p[0]:.7f},{p[1]:.7f}" for p in ring[:4])
        if key in seen:
            return
        seen.add(key)
        buildings.append({"id": key, "ring": ring, "bbox": bbox})

    for el in data.get("elements", []):
        if el.get("type") == "way" and el.get("tags", {}).get("building") and isinstance(el.get("geometry"), list):
            ring = close_ring([(float(p["lat"]), float(p["lon"])) for p in el["geometry"] if "lat" in p and "lon" in p])
            add_building(f"way/{el.get('id')}", ring)

    for el in data.get("elements", []):
        if el.get("type") != "relation" or not el.get("tags", {}).get("building"):
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
            add_building(f"relation/{el.get('id')}", ring)

    return buildings


def _try_download_bbox(bbox: BBox, timeout: int, retries: int, sleep_s: float, label: str = "bbox") -> list[dict[str, Any]]:
    last_error: str | None = None
    query = overpass_query(bbox, server_timeout=max(timeout, 30))
    for attempt in range(retries + 1):
        for endpoint in ENDPOINTS:
            try:
                print(f"    Overpass {label}: {endpoint}", flush=True)
                data = post_overpass(endpoint, query, timeout=timeout)
                return parse_buildings(data, bbox)
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
        "schema": "geozigzag-osm-buildings-cache-v1",
        "tileDeg": BUILDING_TILE_DEG,
        "savedAt": int(time.time() * 1000),
        "bbox": bbox.as_dict(),
        "tiles": tile_records,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


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
    total_buildings = 0

    if not args.per_tile:
        missing_tiles: list[tuple[str, BBox, Path]] = []
        for key, tile_bbox in tiles:
            out_file = out_dir / tile_filename(key)
            if out_file.exists() and not args.force:
                try:
                    cached = json.loads(out_file.read_text(encoding="utf-8"))
                    count = len(cached.get("buildings", []))
                    print(f"cached {key}: {count} buildings")
                    total_buildings += count
                    records.append({"key": key, "file": out_file.name, "bbox": tile_bbox.as_dict(), "buildings": count})
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
            print(f"Downloading buildings once for missing zone covering {len(missing_tiles)} tiles ...", flush=True)
            all_buildings = download_bbox_robust(missing_bbox, timeout=args.timeout, retries=args.retries, sleep_s=args.sleep)
            print(f"Downloaded {len(all_buildings)} unique building footprints; writing tiles ...", flush=True)
            for i, (key, tile_bbox, out_file) in enumerate(missing_tiles, start=1):
                tile_buildings = [b for b in all_buildings if bbox_overlap(b.get("bbox", {}), tile_bbox)]
                payload = {
                    "schema": "geozigzag-osm-buildings-tile-v1",
                    "savedAt": int(time.time() * 1000),
                    "tileKey": key,
                    "tileDeg": BUILDING_TILE_DEG,
                    "bbox": tile_bbox.as_dict(),
                    "buildings": tile_buildings,
                }
                out_file.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                total_buildings += len(tile_buildings)
                records.append({"key": key, "file": out_file.name, "bbox": tile_bbox.as_dict(), "buildings": len(tile_buildings)})
                print(f"[{i:03d}/{len(missing_tiles):03d}] wrote {key}: {len(tile_buildings)} buildings")
    else:
        for i, (key, tile_bbox) in enumerate(tiles, start=1):
            out_file = out_dir / tile_filename(key)
            if out_file.exists() and not args.force:
                try:
                    cached = json.loads(out_file.read_text(encoding="utf-8"))
                    count = len(cached.get("buildings", []))
                    print(f"[{i:03d}/{len(tiles):03d}] cached {key}: {count} buildings")
                    total_buildings += count
                    records.append({"key": key, "file": out_file.name, "bbox": tile_bbox.as_dict(), "buildings": count})
                    continue
                except Exception:
                    pass

            print(f"[{i:03d}/{len(tiles):03d}] downloading {key} ...", flush=True)
            buildings = download_tile(key, tile_bbox, timeout=args.timeout, retries=args.retries, sleep_s=args.sleep)
            payload = {
                "schema": "geozigzag-osm-buildings-tile-v1",
                "savedAt": int(time.time() * 1000),
                "tileKey": key,
                "tileDeg": BUILDING_TILE_DEG,
                "bbox": tile_bbox.as_dict(),
                "buildings": buildings,
            }
            out_file.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            total_buildings += len(buildings)
            records.append({"key": key, "file": out_file.name, "bbox": tile_bbox.as_dict(), "buildings": len(buildings)})
            time.sleep(args.sleep)

    write_manifest(out_dir, records, bbox)
    print(f"Done: {total_buildings} building footprints across {len(tiles)} tiles.")
    if total_buildings == 0:
        print("\nWARNING: Overpass answered successfully, but no building=* footprints were found in this bbox.")
        print("This usually means one of these things:")
        print("  1) the bbox is too small or not centered on the houses you want to avoid;")
        print("  2) the houses are visible in satellite imagery but not mapped as building=* in OSM;")
        print("  3) you need a larger buffer, e.g. --buffer-m 300 or --buffer-m 500.")
        print("Try for example:")
        print("  python3 scripts/osm_buildings_preload.py --poi-ids water_1 arbustivo_2 water_2 --buffer-m 500 --force --serve")
        print("Or use a manual bbox: --bbox SOUTH WEST NORTH EAST --force --serve\n")
    if args.serve:
        serve(args.port, auto_port=args.auto_port, max_port_tries=args.max_port_tries)


def serve(port: int, auto_port: bool = True, max_port_tries: int = 20) -> None:
    handler = partial(SimpleHTTPRequestHandler, directory=str(REPO_ROOT))
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
    print("Stop with Ctrl+C.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download OSM building footprints for GeoZigzag local route validation.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--bbox", nargs=4, metavar=("SOUTH", "WEST", "NORTH", "EAST"), help="Bounding box to download.")
    source.add_argument("--center", nargs=2, metavar=("LAT", "LON"), help="Center point. Use with --radius-m.")
    source.add_argument("--geojson", help="Read bbox from GeoJSON file and expand by --buffer-m.")
    source.add_argument("--poi-ids", nargs="+", help="Built-in demo POI ids, e.g. water_1 arbustivo_2 water_2.")
    parser.add_argument("--radius-m", type=float, default=250.0, help="Radius for --center. Default: 250 m.")
    parser.add_argument("--buffer-m", type=float, default=120.0, help="Buffer around --geojson or --poi-ids bbox. Default: 120 m.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory. Default: web/osm_buildings_cache.")
    parser.add_argument("--timeout", type=int, default=45, help="Per-endpoint timeout in seconds. Default: 45.")
    parser.add_argument("--retries", type=int, default=2, help="Retries after trying all endpoints. Default: 2.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Pause between tile downloads. Default: 0.25 s.")
    parser.add_argument("--max-tiles", type=int, default=300, help="Safety cap. Default: 300 tiles.")
    parser.add_argument("--force", action="store_true", help="Redownload existing tiles.")
    parser.add_argument("--per-tile", action="store_true", help="Old behavior: download each cache tile separately. Default is faster: one bbox request then split locally.")
    parser.add_argument("--serve", action="store_true", help="Serve this folder after downloading.")
    parser.add_argument("--port", type=int, default=8000, help="Port for --serve. Default: 8000.")
    parser.add_argument("--auto-port", action=argparse.BooleanOptionalAction, default=True, help="If the port is busy, try the next ports automatically. Default: enabled.")
    parser.add_argument("--max-port-tries", type=int, default=20, help="How many ports to try when --auto-port is enabled. Default: 20.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    preload(args)


if __name__ == "__main__":
    main()

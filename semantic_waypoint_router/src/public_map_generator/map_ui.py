from __future__ import annotations

import base64
import html
import json
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from folium.plugins import Draw, Fullscreen, MeasureControl, MousePosition
from pyproj import Transformer

from .layer_viewer import LayerInfo
from .semantic import SemanticAnnotation, annotation_collection

BASE_MAPS = {
    "Estándar · OpenStreetMap": {
        "tiles": "OpenStreetMap",
        "attr": "© OpenStreetMap contributors",
    },
    "Satélite · Esri World Imagery": {
        "tiles": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/"
            "tile/{z}/{y}/{x}"
        ),
        "attr": "Esri, Maxar, Earthstar Geographics and contributors",
    },
    "Relieve · OpenTopoMap": {
        "tiles": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attr": "© OpenStreetMap contributors, SRTM | © OpenTopoMap",
    },
    "Híbrido · Esri": {
        "tiles": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/"
            "tile/{z}/{y}/{x}"
        ),
        "attr": "Esri, Maxar, Earthstar Geographics and contributors",
        "labels": True,
    },
}


def raster_bounds_wgs84(path: str | Path) -> list[list[float]]:
    with rasterio.open(path) as dataset:
        transformer = Transformer.from_crs(dataset.crs, "EPSG:4326", always_xy=True)
        west, south = transformer.transform(dataset.bounds.left, dataset.bounds.bottom)
        east, north = transformer.transform(dataset.bounds.right, dataset.bounds.top)
    return [[south, west], [north, east]]


def raster_overlay_png(layer: LayerInfo) -> tuple[str, list[list[float]], tuple[float, float]]:
    return _raster_overlay_png_cached(layer, layer.path.stat().st_mtime_ns)


@lru_cache(maxsize=64)
def _raster_overlay_png_cached(
    layer: LayerInfo,
    modified_ns: int,  # noqa: ARG001
) -> tuple[str, list[list[float]], tuple[float, float]]:
    with rasterio.open(layer.path) as dataset:
        data = dataset.read(1, masked=True)
        values = data.compressed()
        limits = layer.style.fixed_range
        if limits is None:
            limits = tuple(np.nanpercentile(values, [2, 98])) if values.size else (0.0, 1.0)
        cmap = plt.get_cmap(layer.style.cmap).copy()
        cmap.set_bad((0, 0, 0, 0))
        if layer.style.binary:
            rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
            color = np.array(cmap(0.8)) * 255
            rgba[np.asarray(data) > 0] = color.astype(np.uint8)
        else:
            normalized = np.clip(
                (data.filled(np.nan) - limits[0]) / max(limits[1] - limits[0], 1e-9),
                0,
                1,
            )
            rgba = (cmap(normalized) * 255).astype(np.uint8)
            rgba[np.ma.getmaskarray(data)] = 0
        image = BytesIO()
        plt.imsave(image, rgba, format="png")
    encoded = base64.b64encode(image.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}", raster_bounds_wgs84(layer.path), limits


def generate_contours_geojson(elevation_path: str | Path, interval_m: float) -> dict[str, Any]:
    with rasterio.open(elevation_path) as dataset:
        elevation = dataset.read(1, masked=True)
        valid = elevation.compressed()
        if not valid.size:
            return {"type": "FeatureCollection", "features": []}
        start = np.ceil(valid.min() / interval_m) * interval_m
        levels = np.arange(start, valid.max() + interval_m, interval_m)
        if not levels.size:
            return {"type": "FeatureCollection", "features": []}
        rows, columns = elevation.shape
        xs = dataset.transform.c + (np.arange(columns) + 0.5) * dataset.transform.a
        ys = dataset.transform.f + (np.arange(rows) + 0.5) * dataset.transform.e
        figure, axis = plt.subplots()
        contours = axis.contour(xs, ys, elevation.filled(np.nan), levels=levels)
        transformer = Transformer.from_crs(dataset.crs, "EPSG:4326", always_xy=True)
        features = []
        for level, segments in zip(contours.levels, contours.allsegs, strict=True):
            for segment in segments:
                if len(segment) < 2:
                    continue
                coordinates = [list(transformer.transform(x, y)) for x, y in segment]
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": coordinates},
                        "properties": {
                            "elevation_m": float(level),
                            "source": "derived_from_public_dem",
                        },
                    }
                )
        plt.close(figure)
    return {"type": "FeatureCollection", "features": features}


def sample_raster(path: str | Path, latitude: float, longitude: float) -> float | None:
    with rasterio.open(path) as dataset:
        transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
        x, y = transformer.transform(longitude, latitude)
        value = next(dataset.sample([(x, y)], masked=True))[0]
        if np.ma.is_masked(value) or not np.isfinite(value) or value == dataset.nodata:
            return None
        return float(value)


def load_vector_layers(output_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for path in sorted((output_dir / "vectors").glob("*.geojson")):
        try:
            frame = gpd.read_file(path).to_crs("EPSG:4326")
            if not frame.empty:
                result.append((path.stem.replace("osm_", "").title(), json.loads(frame.to_json())))
        except Exception:  # noqa: BLE001
            continue
    return result


def build_map(
    *,
    center: tuple[float, float],
    zoom: int,
    base_map: str,
    layers: list[tuple[LayerInfo, float]],
    annotations: list[SemanticAnnotation],
    aoi_geojson: dict[str, Any] | None = None,
    contours: dict[str, Any] | None = None,
    vector_layers: list[tuple[str, dict[str, Any]]] | None = None,
    route_geojson: dict[str, Any] | None = None,
    enable_drawing: bool = True,
) -> folium.Map:
    provider = BASE_MAPS[base_map]
    map_object = folium.Map(location=center, zoom_start=zoom, tiles=None, control_scale=True)
    active_legend: tuple[list[str], tuple[float, float], str] | None = None
    folium.TileLayer(
        tiles=provider["tiles"], attr=provider["attr"], name=base_map, overlay=False
    ).add_to(map_object)
    if provider.get("labels"):
        folium.TileLayer(
            tiles=(
                "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/"
                "World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
            ),
            attr="Esri",
            name="Etiquetas",
            overlay=True,
        ).add_to(map_object)

    for layer, opacity in layers:
        image, bounds, limits = raster_overlay_png(layer)
        folium.raster_layers.ImageOverlay(
            image=image,
            bounds=bounds,
            opacity=opacity,
            name=layer.style.label,
            interactive=True,
            cross_origin=False,
            zindex=2,
        ).add_to(map_object)
        if not layer.style.binary:
            colors = [plt.get_cmap(layer.style.cmap)(value) for value in np.linspace(0, 1, 7)]
            color_hex = [
                "#{:02x}{:02x}{:02x}".format(*(int(channel * 255) for channel in color[:3]))
                for color in colors
            ]
            active_legend = (
                color_hex,
                limits,
                f"{layer.style.label} {layer.style.unit}".strip(),
            )

    if active_legend:
        colors, limits, caption = active_legend
        gradient = ",".join(colors)
        legend_html = f"""
        <style>
          .pmg-map-legend {{
            position: fixed; top: 10px; right: 54px; z-index: 9999;
            width: min(270px, calc(100vw - 120px)); box-sizing: border-box;
            background: rgba(255,255,255,.94); border-radius: 5px;
            padding: 6px 9px; box-shadow: 0 1px 5px rgba(0,0,0,.28);
            color: #1f2937; font: 11px/1.2 system-ui, sans-serif;
          }}
          .pmg-map-legend__bar {{height: 8px; border-radius: 3px;
            background: linear-gradient(90deg, {gradient});}}
          .pmg-map-legend__range {{display:flex; justify-content:space-between; margin-top:2px;}}
          .pmg-map-legend__label {{overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; margin-top:2px;}}
          @media (max-width: 600px) {{
            .pmg-map-legend {{right: 48px; width: min(180px, calc(100vw - 105px));}}
          }}
        </style>
        <div class="pmg-map-legend">
          <div class="pmg-map-legend__bar"></div>
          <div class="pmg-map-legend__range">
            <span>{limits[0]:.3g}</span><span>{limits[1]:.3g}</span>
          </div>
          <div class="pmg-map-legend__label" title="{html.escape(caption)}">
            {html.escape(caption)}
          </div>
        </div>
        """
        map_object.get_root().html.add_child(folium.Element(legend_html))

    for name, geojson in vector_layers or []:
        folium.GeoJson(
            geojson,
            name=f"OSM · {name}",
            tooltip=folium.GeoJsonTooltip(fields=["kind", "subtype"], aliases=["Clase", "Tipo"]),
            style_function=lambda _feature: {"color": "#e11d48", "weight": 2, "fillOpacity": 0.25},
        ).add_to(map_object)
    if contours and contours.get("features"):
        folium.GeoJson(
            contours,
            name="Curvas de nivel",
            tooltip=folium.GeoJsonTooltip(fields=["elevation_m"], aliases=["Cota (m)"]),
            style_function=lambda _feature: {"color": "#6b4423", "weight": 1, "opacity": 0.8},
        ).add_to(map_object)
    if aoi_geojson:
        folium.GeoJson(
            aoi_geojson,
            name="Área de trabajo",
            style_function=lambda _feature: {
                "color": "#2563eb",
                "weight": 3,
                "fillOpacity": 0.08,
            },
        ).add_to(map_object)
    if route_geojson and route_geojson.get("features"):
        route_feature = next(
            (
                feature
                for feature in route_geojson["features"]
                if feature.get("geometry", {}).get("type") == "LineString"
            ),
            None,
        )
        if route_feature:
            properties = route_feature.get("properties", {})
            coordinates = route_feature["geometry"].get("coordinates", [])
            folium.GeoJson(
                route_feature,
                name="Ruta planificada",
                tooltip=(
                    f"Ruta · {properties.get('distance_m', 0):.1f} m · "
                    f"{properties.get('waypoint_count', 0)} waypoints"
                ),
                style_function=lambda _feature: {
                    "color": "#0f62fe",
                    "weight": 5,
                    "opacity": 0.95,
                },
            ).add_to(map_object)
            if coordinates:
                start_lon, start_lat = coordinates[0]
                end_lon, end_lat = coordinates[-1]
                folium.CircleMarker(
                    [start_lat, start_lon],
                    radius=7,
                    color="#ffffff",
                    weight=2,
                    fill=True,
                    fill_color="#16a34a",
                    fill_opacity=1,
                    tooltip="Inicio de ruta",
                ).add_to(map_object)
                folium.CircleMarker(
                    [end_lat, end_lon],
                    radius=7,
                    color="#ffffff",
                    weight=2,
                    fill=True,
                    fill_color="#dc2626",
                    fill_opacity=1,
                    tooltip="Fin de ruta",
                ).add_to(map_object)
        target_features = [
            feature
            for feature in route_geojson["features"]
            if feature.get("properties", {}).get("waypoint_type") == "mission_target"
            and feature.get("properties", {}).get("source") != "user_origin"
        ]
        for index, feature in enumerate(target_features, start=1):
            properties = feature["properties"]
            longitude, latitude = feature["geometry"]["coordinates"]
            automatic = properties.get("source") == "automatic_semantic_layer"
            label = properties.get("name", f"WP{index}")
            semantic = properties.get("semantic_layer")
            tooltip = f"{index}. {label}"
            if semantic:
                tooltip += f" · {semantic}"
            folium.CircleMarker(
                [latitude, longitude],
                radius=6,
                color="#ffffff",
                weight=2,
                fill=True,
                fill_color="#7c3aed" if automatic else "#f97316",
                fill_opacity=1,
                tooltip=tooltip,
            ).add_to(map_object)
    if annotations:
        folium.GeoJson(
            annotation_collection(annotations),
            name="Anotaciones manuales",
            tooltip=folium.GeoJsonTooltip(
                fields=["annotation_type", "description", "source", "created_at"],
                aliases=["Tipo", "Descripción", "Fuente", "Creada"],
            ),
            style_function=lambda _feature: {
                "color": "#dc2626",
                "weight": 4,
                "fillColor": "#f97316",
                "fillOpacity": 0.45,
            },
            marker=folium.CircleMarker(radius=7, color="#dc2626", fill=True),
        ).add_to(map_object)

    if enable_drawing:
        Draw(
            export=False,
            position="topleft",
            draw_options={
                "polyline": True,
                "polygon": True,
                "rectangle": True,
                "circle": False,
                "circlemarker": False,
                "marker": True,
            },
            edit_options={"edit": False, "remove": False},
        ).add_to(map_object)
    Fullscreen(position="topleft", title="Pantalla completa").add_to(map_object)
    MeasureControl(position="bottomleft", primary_length_unit="meters").add_to(map_object)
    MousePosition(position="bottomright", separator=" · ", num_digits=6).add_to(map_object)
    folium.LayerControl(collapsed=True, position="topright").add_to(map_object)
    return map_object

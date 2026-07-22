from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import streamlit as st
import yaml
from shapely.geometry import shape
from streamlit_folium import st_folium

from public_map_generator.config import AOIConfig, AppConfig, GridConfig, OutputConfig, load_config
from public_map_generator.export import annotations_csv, project_archive
from public_map_generator.jobs import cancel_job, job_progress, read_job, start_job
from public_map_generator.layer_viewer import LayerInfo, discover_layers, layer_statistics
from public_map_generator.map_ui import (
    BASE_MAPS,
    build_map,
    generate_contours_geojson,
    load_vector_layers,
    sample_raster,
)
from public_map_generator.route_planner import (
    PlanningProfile,
    RoutePlan,
    SemanticRoutePlanner,
    save_route_bundle,
)
from public_map_generator.semantic import SemanticAnnotation, annotation_collection

st.set_page_config(
    page_title="Semantic Waypoint Router",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .block-container {padding-top: 2.5rem; padding-bottom: 1rem; max-width: 1900px;}
    .app-heading {display:flex; align-items:baseline; justify-content:space-between; gap:1rem;}
    .app-heading strong {font-size:1.45rem; line-height:1.2; white-space:nowrap;}
    .app-heading span {font-size:.82rem; color:#9ca3af; text-align:right;}
    .source-public {border-left: 4px solid #2563eb; padding-left: .7rem;}
    .source-inferred {border-left: 4px solid #f59e0b; padding-left: .7rem;}
    .source-manual {border-left: 4px solid #dc2626; padding-left: .7rem;}
    @media (max-width: 768px) {
        .block-container {padding: 2.5rem .5rem 1rem;}
        .app-heading {align-items:flex-start; flex-direction:column; gap:.15rem;}
        .app-heading strong {font-size:1.2rem;}
        .app-heading span {font-size:.72rem; text-align:left;}
        iframe[title="streamlit_folium.st_folium"] {min-height: 56vh;}
    }
</style>
""",
    unsafe_allow_html=True,
)

if "annotations" not in st.session_state:
    st.session_state.annotations = []
if "map_center" not in st.session_state:
    st.session_state.map_center = (42.3135981, -6.2027894)
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 14
if "aoi_geojson" not in st.session_state:
    st.session_state.aoi_geojson = None
if "contours" not in st.session_state:
    st.session_state.contours = None
if "route_plan" not in st.session_state:
    st.session_state.route_plan = None
if "route_project" not in st.session_state:
    st.session_state.route_project = None
if "hidden_route_project" not in st.session_state:
    st.session_state.hidden_route_project = None
if "mission_waypoints" not in st.session_state:
    st.session_state.mission_waypoints = [
        {"name": "WP1", "latitude": 42.31415, "longitude": -6.20410},
        {"name": "WP2", "latitude": 42.31295, "longitude": -6.20520},
    ]
if "waypoint_editor_version" not in st.session_state:
    st.session_state.waypoint_editor_version = 0


def available_outputs() -> list[Path]:
    root = Path("outputs")
    if not root.exists():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and (path / "layers").exists()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


@st.cache_data(ttl=86_400, show_spinner=False)
def search_location(query: str) -> list[dict[str, Any]]:
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": 5},
        headers={"User-Agent": "public-map-generator/0.2 (semantic mapping UI)"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(show_spinner=False)
def cached_contours(path: str, modified_ns: int, interval: float) -> dict:  # noqa: ARG001
    return generate_contours_geojson(path, interval)


@st.cache_data(show_spinner=False)
def cached_vectors(output: str, modified_ns: int) -> list:  # noqa: ARG001
    return load_vector_layers(Path(output))


@st.cache_data(show_spinner=False)
def cached_layer_statistics(path: str, modified_ns: int) -> dict:  # noqa: ARG001
    return layer_statistics(path)


def selected_output_widget(outputs: list[Path]) -> Path | None:
    if not outputs:
        return None
    labels = [str(path) for path in outputs]
    preferred = st.session_state.get("selected_output", labels[0])
    index = labels.index(preferred) if preferred in labels else 0
    value = Path(st.selectbox("Proyecto", labels, index=index, key="project_select"))
    st.session_state.selected_output = str(value)
    return value


def geometry_from_drawing(drawing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not drawing:
        return None
    if drawing.get("type") == "Feature":
        return drawing.get("geometry")
    if drawing.get("type") in {
        "Point",
        "LineString",
        "Polygon",
        "MultiPolygon",
        "MultiLineString",
    }:
        return drawing
    return None


def project_aoi_geometry(output_dir: Path | None) -> dict[str, Any] | None:
    if output_dir is None:
        return None
    path = output_dir / "aoi_wgs84.geojson"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") == "FeatureCollection" and payload.get("features"):
        return payload["features"][0].get("geometry")
    if payload.get("type") == "Feature":
        return payload.get("geometry")
    return payload


def latest_saved_route(output_dir: Path | None) -> RoutePlan | None:
    if output_dir is None:
        return None
    candidates = sorted(
        (output_dir / "routes").glob("*/route.yaml"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        return None
    payload = yaml.safe_load(candidates[0].read_text(encoding="utf-8"))["route"]
    return RoutePlan(
        name=payload["name"],
        mode=payload["mode"],
        waypoints=payload["waypoints"],
        metrics=payload["metrics"],
        profile=payload["profile"],
        targets=payload.get("targets", []),
    )


def layer_source(layer: LayerInfo, metadata: dict[str, Any]) -> tuple[str, str, str]:
    created = metadata.get("created_at", "No disponible")
    if layer.style.category == "Semántica OSM":
        return "OpenStreetMap / Overpass", created, "Dato público cartografiado"
    if layer.style.category == "Sentinel-2":
        date = metadata.get("sources", {}).get("sentinel2", {}).get("datetime", created)
        return "Sentinel-2 L2A", date, "Dato público derivado de satélite"
    if layer.key == "orthophoto_aligned":
        return "PNOA Máxima Actualidad (IGN)", created, "Ortofoto pública"
    if layer.style.category == "Terreno":
        return "MDT05 IGN/CNIG", created, "Dato público o derivado del MDT"
    return "Fusión de capas públicas", created, "Inferencia / prior, no observación confirmada"


def layer_groups(layers: list[LayerInfo]) -> dict[str, list[LayerInfo]]:
    environmental_keys = {
        "wetness_prior",
        "vegetation_prior",
        "mud_risk",
        "water_accumulation_risk",
        "ndvi",
        "ndmi",
        "sentinel_scl",
    }
    return {
        "Terreno": [
            layer for layer in layers if layer.style.category in {"Terreno", "Imagen base"}
        ],
        "Medioambiente": [
            layer
            for layer in layers
            if layer.key in environmental_keys or layer.style.category == "Sentinel-2"
        ],
        "Obstáculos públicos": [
            layer for layer in layers if layer.style.category == "Semántica OSM"
        ],
        "Semantic Mapping": [
            layer
            for layer in layers
            if layer.style.category == "Fusión para navegación"
            and layer.key not in environmental_keys
        ],
    }


def layer_widget_key(project: Path, layer_key: str, field: str) -> str:
    return f"layer::{project.name}::{layer_key}::{field}"


def freshness_status(metadata: dict[str, Any], max_age_days: int = 30) -> str:
    created_at = metadata.get("created_at")
    if not created_at:
        return "Fecha no disponible"
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return "Fecha no disponible"
    age_days = (datetime.now(timezone.utc) - created).days
    return "Desactualizada" if age_days > max_age_days else "Disponible"


def render_layer_group(
    name: str,
    group_layers: list[LayerInfo],
    project: Path,
    metadata: dict[str, Any],
) -> list[tuple[LayerInfo, float]]:
    recommended = {"elevation", "buildings", "water", "obstacle_probability"}
    for layer in group_layers:
        visibility_key = layer_widget_key(project, layer.key, "visible")
        if visibility_key not in st.session_state:
            st.session_state[visibility_key] = layer.key in recommended
    visible_count = sum(
        bool(st.session_state[layer_widget_key(project, layer.key, "visible")])
        for layer in group_layers
    )
    with st.expander(f"{name} · {visible_count}/{len(group_layers)}", expanded=name == "Terreno"):
        show_col, hide_col, only_col = st.columns(3)
        if show_col.button("Todo", key=f"show::{project.name}::{name}", width="stretch"):
            for layer in group_layers:
                st.session_state[layer_widget_key(project, layer.key, "visible")] = True
            st.rerun()
        if hide_col.button("Nada", key=f"hide::{project.name}::{name}", width="stretch"):
            for layer in group_layers:
                st.session_state[layer_widget_key(project, layer.key, "visible")] = False
            st.rerun()
        if only_col.button("Solo", key=f"only::{project.name}::{name}", width="stretch"):
            for available in discover_layers(project):
                st.session_state[layer_widget_key(project, available.key, "visible")] = (
                    available in group_layers
                )
            st.rerun()

        selected: list[tuple[LayerInfo, float]] = []
        for layer in group_layers:
            visibility_key = layer_widget_key(project, layer.key, "visible")
            opacity_key = layer_widget_key(project, layer.key, "opacity")
            if opacity_key not in st.session_state:
                st.session_state[opacity_key] = (
                    0.35 if layer.key in {"obstacle_probability", "traversability_prior"} else 0.65
                )
            toggle_col, options_col = st.columns([5, 1], vertical_alignment="center")
            icon = "●" if layer.style.binary else "◐"
            visible = toggle_col.checkbox(f"{icon} {layer.style.label}", key=visibility_key)
            with options_col.popover("⋯"):
                st.slider("Opacidad", 0.1, 1.0, step=0.05, key=opacity_key)
                source, date, evidence = layer_source(layer, metadata)
                statistics = cached_layer_statistics(str(layer.path), layer.path.stat().st_mtime_ns)
                freshness = freshness_status(metadata) if statistics["valid_cells"] else "Sin datos"
                status_icon = {
                    "Desactualizada": "◷",
                    "Sin datos": "—",
                }.get(freshness, "✅")
                st.caption(f"{status_icon} {freshness} · {evidence}")
                st.caption(f"Fuente: {source}")
                st.caption(f"Fecha/descarga: {date}")
                st.caption(f"Resolución: {metadata.get('grid', {}).get('resolution_m', 'N/D')} m")
                if layer.style.description:
                    st.info(layer.style.description)
                st.download_button(
                    "Exportar GeoTIFF",
                    layer.path.read_bytes,
                    file_name=layer.path.name,
                    mime="image/tiff",
                    key=f"download::{project.name}::{layer.key}",
                    width="stretch",
                )
            if visible:
                selected.append((layer, float(st.session_state[opacity_key])))
        return selected


center = st.session_state.map_center
st.markdown(
    '<div class="app-heading"><strong>Rutas semánticas por waypoints</strong>'
    f"<span>Mapa de apoyo · {center[0]:.4f}, {center[1]:.4f}</span></div>",
    unsafe_allow_html=True,
)
interaction_mode = st.segmented_control(
    "Interacción",
    ["Explorar", "Consultar", "Dibujar"],
    default="Explorar",
    label_visibility="collapsed",
    help=(
        "Explorar permite mover el mapa sin recalcular la aplicación. "
        "Consultar devuelve valores al hacer clic. Dibujar activa la edición."
    ),
)

outputs = available_outputs()
with st.sidebar:
    st.subheader("Semantic Waypoint Router")
    st.caption("Misión principal + contexto cartográfico público")
    with st.form("location_search", border=False):
        query = st.text_input(
            "Buscar ubicación",
            placeholder="Lugar, dirección o latitud, longitud",
            label_visibility="collapsed",
        )
        search_clicked = st.form_submit_button("Buscar", width="stretch")
    if search_clicked and query:
        try:
            parts = [float(item.strip()) for item in query.split(",")]
            if len(parts) == 2:
                st.session_state.map_center = (parts[0], parts[1])
                st.session_state.map_zoom = 16
                st.session_state.search_results = []
        except ValueError:
            try:
                st.session_state.search_results = search_location(query)
                if not st.session_state.search_results:
                    st.warning("No se encontraron ubicaciones.")
            except requests.RequestException as exc:
                st.warning(f"El buscador público no está disponible: {exc}")
    if st.session_state.get("search_results"):
        chosen = st.selectbox(
            "Resultados",
            st.session_state.search_results,
            format_func=lambda item: item["display_name"],
            label_visibility="collapsed",
        )
        if st.button("Ir al resultado", width="stretch"):
            st.session_state.map_center = (float(chosen["lat"]), float(chosen["lon"]))
            st.session_state.map_zoom = 15
            st.session_state.search_results = []
            st.rerun()
    st.divider()

    with st.expander("Configuración de generación", expanded=not outputs):
        generation_name = st.text_input("Nombre del proyecto", "semantic_map")
        resolution = st.selectbox("Resolución de análisis (m)", [1.0, 2.0, 5.0, 10.0], index=2)
        radius = st.number_input("Radio si no hay AOI (m)", 50, 5000, 500, 50)
        update_existing = st.checkbox(
            "Actualizar aunque exista en caché",
            help="Regenera las fuentes y reemplaza únicamente el proyecto con este nombre.",
        )
        st.caption(
            "MDT, PNOA, OSM, MDS y Sentinel-2 se intentan automáticamente. "
            "Los fallos opcionales no detienen el resto."
        )

    selected_output = selected_output_widget(outputs)
    metadata: dict[str, Any] = {}
    layers: list[LayerInfo] = []
    if selected_output:
        metadata_path = selected_output / "metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        layers = discover_layers(selected_output)

    with st.expander("Mapas base", expanded=True):
        base_map = st.radio(
            "Mapa base",
            list(BASE_MAPS),
            label_visibility="collapsed",
        )
        st.caption("Solo uno activo. Se sirve en línea y no se almacena como dato propio.")

    selected_layers: list[tuple[LayerInfo, float]] = []
    if selected_output:
        for group_name, group_layers in layer_groups(layers).items():
            if group_layers:
                selected_layers.extend(
                    render_layer_group(group_name, group_layers, selected_output, metadata)
                )
        order_key = f"layer-order-v2::{selected_output.name}"
        category_rank = {
            "Imagen base": 0,
            "Terreno": 1,
            "Sentinel-2": 2,
            "Semántica OSM": 3,
            "Fusión para navegación": 4,
        }
        available_keys = [
            layer.key
            for layer in sorted(
                layers, key=lambda item: (category_rank.get(item.style.category, 2), item.key)
            )
        ]
        stored_order = st.session_state.get(order_key, available_keys)
        st.session_state[order_key] = [key for key in stored_order if key in available_keys] + [
            key for key in available_keys if key not in stored_order
        ]
        order_index = {key: index for index, key in enumerate(st.session_state[order_key])}
        selected_layers.sort(key=lambda item: order_index[item[0].key])
        continuous_count = sum(not layer.style.binary for layer, _opacity in selected_layers)
        if continuous_count > 2:
            st.warning(
                "Hay varias capas continuas superpuestas. Reduce su opacidad o usa “Solo” "
                "para evitar una lectura ambigua."
            )
        with st.expander(f"Orden y visibilidad · {len(selected_layers)} activas"):
            if selected_layers:
                ordered_layer = st.selectbox(
                    "Capa",
                    [item[0] for item in selected_layers],
                    format_func=lambda layer: layer.style.label,
                )
                up_col, down_col, reset_col = st.columns(3)
                current_index = st.session_state[order_key].index(ordered_layer.key)
                if up_col.button("Subir", width="stretch", disabled=current_index == 0):
                    order = st.session_state[order_key]
                    order[current_index - 1], order[current_index] = (
                        order[current_index],
                        order[current_index - 1],
                    )
                    st.rerun()
                if down_col.button(
                    "Bajar",
                    width="stretch",
                    disabled=current_index == len(st.session_state[order_key]) - 1,
                ):
                    order = st.session_state[order_key]
                    order[current_index + 1], order[current_index] = (
                        order[current_index],
                        order[current_index + 1],
                    )
                    st.rerun()
                if reset_col.button("Recom.", width="stretch"):
                    for layer in layers:
                        st.session_state[
                            layer_widget_key(selected_output, layer.key, "visible")
                        ] = layer.key in {
                            "elevation",
                            "buildings",
                            "water",
                            "obstacle_probability",
                        }
                    st.rerun()
                st.caption("La primera capa se dibuja debajo; la última queda encima.")
            else:
                st.caption("No hay superposiciones activas.")
    else:
        st.info("Selecciona un proyecto o genera mapas para ver el catálogo de capas.")

    elevation = next((layer for layer in layers if layer.key == "elevation"), None)
    with st.expander("Curvas de nivel"):
        show_contours = st.checkbox("Mostrar curvas", disabled=elevation is None)
        contour_interval = st.number_input(
            "Intervalo (m)", 1.0, 100.0, 10.0, 1.0, disabled=not show_contours
        )
    with st.expander("Objetos OSM detallados"):
        show_osm_vectors = st.checkbox(
            "Cargar vectores consultables",
            value=False,
            help="Puede tardar en áreas urbanas. Las capas ráster siguen disponibles sin esto.",
        )

    with st.expander("Planificación de ruta", expanded=True):
        st.markdown("**Origen WGS84**")
        origin_latitude = st.number_input(
            "Latitud de inicio",
            min_value=-90.0,
            max_value=90.0,
            value=42.3135981,
            step=0.000001,
            format="%.7f",
        )
        origin_longitude = st.number_input(
            "Longitud de inicio",
            min_value=-180.0,
            max_value=180.0,
            value=-6.2027894,
            step=0.000001,
            format="%.7f",
        )
        st.caption("Ejemplo: centro de Tabuyo del Monte, León.")
        if st.button("Centrar mapa en el origen", width="stretch"):
            st.session_state.map_center = (origin_latitude, origin_longitude)
            st.session_state.map_zoom = 16
            st.rerun()

        st.markdown("**Waypoints manuales**")
        manual_waypoints = st.data_editor(
            st.session_state.mission_waypoints,
            key=f"waypoint_editor_{st.session_state.waypoint_editor_version}",
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "name": st.column_config.TextColumn("Nombre", required=True),
                "latitude": st.column_config.NumberColumn(
                    "Latitud", min_value=-90.0, max_value=90.0, format="%.7f", required=True
                ),
                "longitude": st.column_config.NumberColumn(
                    "Longitud",
                    min_value=-180.0,
                    max_value=180.0,
                    format="%.7f",
                    required=True,
                ),
            },
        )
        st.session_state.mission_waypoints = manual_waypoints
        st.caption("Añade, elimina o reordena objetivos. También puedes importarlos del dibujo.")

        semantic_layer_labels = {
            "Bosque": "forest",
            "Matorral": "scrub",
            "Masas de agua": "water",
            "Cursos de agua": "waterways",
            "Humedales": "wetlands",
            "Pastizal": "grass",
            "Cultivo": "farmland",
        }
        automatic_waypoint_labels = st.multiselect(
            "Waypoints automáticos",
            list(semantic_layer_labels),
            default=["Bosque", "Matorral", "Masas de agua", "Cursos de agua"],
            help=(
                "Crea objetivos de aproximación para recursos cartografiados. Agua y humedales "
                "siguen siendo obstáculos para la trayectoria."
            ),
        )
        minimum_target_area = st.number_input(
            "Área mínima automática (m²)",
            min_value=1.0,
            max_value=100_000.0,
            value=100.0,
            step=25.0,
        )
        max_automatic_targets = st.number_input("Máximo de objetivos automáticos", 1, 100, 20, 1)
        waypoint_spacing = st.number_input(
            "Separación de waypoints (m)",
            min_value=0.5,
            max_value=50.0,
            value=2.0,
            step=0.5,
            disabled=selected_output is None,
        )
        with st.expander("Seguridad y costes"):
            route_clearance = st.number_input("Margen al obstáculo (m)", 0.0, 20.0, 1.5, 0.5)
            max_route_slope = st.number_input("Pendiente máxima (°)", 1.0, 60.0, 25.0, 1.0)
            max_route_step = st.number_input(
                "Desnivel máximo por celda MDT (m)", 0.05, 5.0, 1.0, 0.05
            )
            road_preference = st.slider("Preferencia por caminos", 0.0, 0.9, 0.45, 0.05)
        st.info(
            "El mapa solo apoya la misión: edificios, agua, barreras y desniveles condicionan "
            "el recorrido. Los recursos elegidos añaden waypoints de aproximación segura."
        )

    annotation_types = [
        "Charco",
        "Barro",
        "Tronco",
        "Obstáculo",
        "Zona peligrosa",
        "Zona no transitable",
        "Nota personalizada",
    ]
    with st.expander(f"Anotaciones · {len(st.session_state.annotations)}"):
        annotation_type = st.selectbox(
            "Tipo de nueva anotación",
            annotation_types,
        )
        annotation_description = st.text_area("Descripción opcional", height=70)
        st.caption("Dibuja en el mapa y después pulsa “Guardar dibujo como anotación”.")
        if st.session_state.annotations:
            edit_index = st.selectbox(
                "Editar anotación",
                range(len(st.session_state.annotations)),
                format_func=lambda index: (
                    f"{st.session_state.annotations[index].annotation_type} · "
                    f"{st.session_state.annotations[index].geometry['type']}"
                ),
            )
            edited = st.session_state.annotations[edit_index]
            edited_type = st.selectbox(
                "Tipo guardado",
                annotation_types,
                index=annotation_types.index(edited.annotation_type),
                key="edited_annotation_type",
            )
            edited_description = st.text_area(
                "Descripción guardada",
                value=edited.description,
                key=f"edited_description_{edited.id}",
            )
            edit_col, delete_col = st.columns(2)
            if edit_col.button("Actualizar", width="stretch"):
                edited.annotation_type = edited_type
                edited.description = edited_description
                st.success("Anotación actualizada.")
                st.rerun()
            if delete_col.button("Eliminar", width="stretch", type="secondary"):
                st.session_state.annotations.pop(edit_index)
                st.rerun()

    with st.expander("Fuentes y metadatos"):
        if not selected_output:
            st.info("Genera o selecciona un proyecto para consultar sus fuentes.")
        else:
            for source_name, status in metadata.get("sources", {}).items():
                icon = "✅" if status.get("ok") else "⚠️"
                availability = "disponible" if status.get("ok") else "fallo/no disponible"
                st.caption(f"{icon} {source_name}: {availability}")
                if status.get("error"):
                    st.caption(status["error"][:180])
            for layer_name, reason in metadata.get("unavailable_layers", {}).items():
                st.caption(f"— {layer_name}: Sin datos · {reason}")
            st.caption(f"Generado: {metadata.get('created_at', 'No disponible')}")
            if st.button("Actualizar catálogo de capas", width="stretch"):
                st.cache_data.clear()
                st.success("Caché de visualización actualizada.")
                st.rerun()
            if st.button("Limpiar caché de la aplicación", width="stretch"):
                st.cache_data.clear()
                st.success("Caché limpiada. Los datos guardados no se han eliminado.")

    with st.expander("Configurar exportación"):
        export_layers = st.multiselect(
            "Capas incluidas en el proyecto ZIP",
            layers,
            default=layers,
            format_func=lambda layer: layer.style.label,
        )
        if selected_output:
            preview_path = selected_output / "preview" / "analysis_overview.png"
            if preview_path.exists():
                st.download_button(
                    "Descargar vista PNG",
                    preview_path.read_bytes,
                    file_name=f"{selected_output.name}_preview.png",
                    mime="image/png",
                    width="stretch",
                )

clean_manual_waypoints = [
    {
        "name": str(item.get("name") or f"WP{index}"),
        "latitude": float(item["latitude"]),
        "longitude": float(item["longitude"]),
    }
    for index, item in enumerate(manual_waypoints, start=1)
    if item.get("latitude") is not None and item.get("longitude") is not None
]
selected_semantic_layers = [semantic_layer_labels[label] for label in automatic_waypoint_labels]
route_disabled = selected_output is None or (
    not clean_manual_waypoints and not selected_semantic_layers
)

target = Path("outputs") / generation_name
active_payload = read_job(st.session_state.active_job) if st.session_state.get("active_job") else {}
job_is_running = active_payload.get("state") in {"pending", "running"}
route_button_col, map_button_col = st.columns([1.4, 1.0])
route_clicked = route_button_col.button(
    "Generar ruta entre waypoints",
    type="primary",
    width="stretch",
    disabled=route_disabled,
    help="Conecta el origen, los waypoints manuales y los recursos automáticos.",
)
generate_clicked = map_button_col.button(
    "Preparar mapa semántico",
    width="stretch",
    disabled=job_is_running,
    help="Actualiza las capas públicas que ayudan al planificador.",
)
area_label = "AOI seleccionada" if st.session_state.aoi_geojson else f"Centro + {radius} m"
if job_is_running:
    percent, completed, errors = job_progress(active_payload)
    general_state = f"Generando {percent}% · {completed} completadas · {errors} errores"
elif active_payload.get("state") == "completed":
    general_state = "Última generación completada"
elif active_payload.get("state") == "error":
    general_state = "Última generación con error"
else:
    general_state = "Listo"
st.caption(
    f"Misión: {len(clean_manual_waypoints)} objetivos manuales · "
    f"Mapa de apoyo: {area_label} · {general_state}"
)

if route_clicked and selected_output:
    profile = PlanningProfile(
        clearance_m=float(route_clearance),
        max_slope_deg=float(max_route_slope),
        max_step_m=float(max_route_step),
        road_preference=float(road_preference),
    )
    try:
        with st.spinner("Conectando waypoints sobre el mapa semántico…"):
            planner = SemanticRoutePlanner(
                selected_output,
                profile=profile,
                constraint_geometry=project_aoi_geometry(selected_output),
            )
            plan = planner.plan_waypoint_mission(
                (float(origin_longitude), float(origin_latitude)),
                manual_waypoints=clean_manual_waypoints,
                semantic_layers=selected_semantic_layers,
                waypoint_spacing_m=float(waypoint_spacing),
                minimum_semantic_area_m2=float(minimum_target_area),
                max_automatic_targets=int(max_automatic_targets),
            )
            bundle = save_route_bundle(plan, selected_output)
        st.session_state.route_plan = plan
        st.session_state.route_project = str(selected_output)
        st.session_state.hidden_route_project = None
        st.session_state.route_bundle = str(bundle)
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudo generar una ruta válida: {exc}")

if generate_clicked:
    if st.session_state.aoi_geojson:
        generation_bounds = shape(st.session_state.aoi_geojson["geometry"]).bounds
        generation_aoi = AOIConfig(type="bbox", bbox=list(generation_bounds))
    else:
        generation_aoi = AOIConfig(
            type="center_radius",
            lat=float(st.session_state.map_center[0]),
            lon=float(st.session_state.map_center[1]),
            radius_m=float(radius),
        )
    if target.exists() and not update_existing:
        cached_config_path = target / "config_used.yaml"
        if cached_config_path.exists():
            cached_config = load_config(cached_config_path)
            same_area = cached_config.aoi.model_dump() == generation_aoi.model_dump()
            same_resolution = cached_config.grid.resolution_m == float(resolution)
            if same_area and same_resolution:
                st.session_state.selected_output = str(target)
                st.info("Proyecto recuperado de caché; no se han repetido las descargas.")
                st.rerun()
        target = Path("outputs") / (f"{generation_name}_{datetime.now().strftime('%Y%m%dT%H%M%S')}")
        st.info(f"El área cambió. La nueva ejecución se guardará como `{target.name}`.")
    generation_config = AppConfig(
        project_name=generation_name,
        aoi=generation_aoi,
        grid=GridConfig(resolution_m=float(resolution)),
        output=OutputConfig(directory=str(target), overwrite=bool(update_existing)),
    )
    generation_config.sources.ign_orthophoto.enabled = True
    generation_config.sources.osm.enabled = True
    generation_config.sources.ign_mds.enabled = True
    generation_config.sources.sentinel2.enabled = True
    try:
        st.session_state.active_job = str(start_job(generation_config))
        st.session_state.progressive_layer_count = 0
        st.success("Generación iniciada en segundo plano. Puedes seguir usando el mapa.")
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"No se pudo iniciar el trabajo: {exc}")


@st.fragment(run_every=2)
def generation_status_panel() -> None:
    job_path = st.session_state.get("active_job")
    if not job_path:
        return
    payload = read_job(job_path)
    if not payload:
        st.warning("No se puede leer el estado del trabajo.")
        return
    percent, completed, errors = job_progress(payload)
    if (
        payload.get("state") == "running"
        and completed > st.session_state.get("progressive_layer_count", 0)
        and (Path(payload["output"]) / "layers").exists()
    ):
        st.session_state.progressive_layer_count = completed
        st.session_state.selected_output = payload["output"]
        st.cache_data.clear()
        st.rerun(scope="app")
    with st.expander(
        f"Generación · {payload.get('state', 'desconocido')} · {percent}%",
        expanded=payload.get("state") in {"pending", "running", "error"},
    ):
        st.progress(percent / 100, text=f"{completed} capas/grupos completados · {errors} errores")
        if payload.get("started_at"):
            started = datetime.fromisoformat(payload["started_at"])
            end = (
                datetime.fromisoformat(payload["finished_at"])
                if payload.get("finished_at")
                else datetime.now(timezone.utc)
            )
            st.caption(f"Tiempo transcurrido: {max(0, int((end - started).total_seconds()))} s")
        with st.expander("Estado individual"):
            labels = {
                "workspace": "Proyecto",
                "elevation": "Elevación",
                "orthophoto": "Ortofoto",
                "osm": "Obstáculos OSM",
                "surface_model": "Modelo de superficies",
                "sentinel2": "Satélite e índices",
                "terrain": "Pendiente y terreno",
                "semantic_risks": "Riesgos semánticos",
                "soil_moisture": "Humedad del suelo",
                "recent_precipitation": "Precipitación reciente",
            }
            state_icons = {
                "pending": "○",
                "downloading": "↓",
                "processing": "◌",
                "available": "✓",
                "unavailable": "—",
                "error": "!",
            }
            for component, item in payload.get("components", {}).items():
                icon = state_icons.get(item.get("state"), "·")
                component_label = labels.get(component, component.replace("_", " ").title())
                st.caption(
                    f"{icon} {component_label} · {item.get('state')} · {item.get('message', '')}"
                )
        if payload.get("state") in {"pending", "running"}:
            if st.button("Cancelar generación", key=f"cancel::{payload['id']}"):
                cancel_job(job_path)
                st.warning("Cancelación solicitada. Los archivos ya creados se conservan.")
                st.rerun(scope="fragment")
        elif payload.get("state") == "error":
            st.error(payload.get("error", "Error desconocido"))
            if st.button("Reintentar", key=f"retry::{payload['id']}"):
                config = AppConfig.model_validate(payload["config"])
                config.output.overwrite = True
                st.session_state.active_job = str(start_job(config))
                st.session_state.progressive_layer_count = 0
                st.rerun(scope="app")
        elif payload.get("state") == "completed":
            st.success("Todas las tareas posibles han terminado.")
            if st.session_state.get("handled_job") != payload["id"]:
                st.session_state.handled_job = payload["id"]
                st.session_state.selected_output = payload["output"]
                st.cache_data.clear()
                st.rerun(scope="app")


generation_status_panel()

if selected_output and show_contours and elevation:
    with st.spinner("Generando curvas de nivel…"):
        try:
            st.session_state.contours = cached_contours(
                str(elevation.path), elevation.path.stat().st_mtime_ns, float(contour_interval)
            )
        except Exception as exc:  # noqa: BLE001
            st.session_state.contours = None
            st.warning(f"No se pudieron generar las curvas: {exc}")
else:
    st.session_state.contours = None

vector_layers = []
if selected_output and show_osm_vectors:
    vectors_dir = selected_output / "vectors"
    modified_ns = max(
        (path.stat().st_mtime_ns for path in vectors_dir.glob("*.geojson")), default=0
    )
    vector_layers = cached_vectors(str(selected_output), modified_ns)

active_route: RoutePlan | None = None
if (
    selected_output
    and st.session_state.route_project == str(selected_output)
    and isinstance(st.session_state.route_plan, RoutePlan)
):
    active_route = st.session_state.route_plan
elif selected_output and st.session_state.hidden_route_project != str(selected_output):
    try:
        active_route = latest_saved_route(selected_output)
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        active_route = None

map_object = build_map(
    center=st.session_state.map_center,
    zoom=st.session_state.map_zoom,
    base_map=base_map,
    layers=selected_layers,
    annotations=st.session_state.annotations,
    aoi_geojson=st.session_state.aoi_geojson,
    contours=st.session_state.contours,
    vector_layers=vector_layers,
    route_geojson=active_route.geojson() if active_route else None,
    enable_drawing=interaction_mode == "Dibujar",
)

returned_by_mode = {
    "Explorar": [],
    "Consultar": ["last_clicked"],
    "Dibujar": ["last_active_drawing", "bounds"],
}
map_data = st_folium(
    map_object,
    height=760,
    use_container_width=True,
    returned_objects=returned_by_mode[interaction_mode],
    center=st.session_state.map_center,
    zoom=st.session_state.map_zoom,
    key="semantic_map",
)

drawing_geometry = geometry_from_drawing(map_data.get("last_active_drawing"))
action_col, selection_col, export_col, coordinate_col = st.columns([1.5, 1.4, 1.3, 1.8])
with action_col:
    if st.button(
        "Guardar dibujo como anotación",
        width="stretch",
        disabled=interaction_mode != "Dibujar" or drawing_geometry is None,
        help="Guarda el último punto, línea o polígono dibujado como información manual.",
    ):
        signature = json.dumps(drawing_geometry, sort_keys=True)
        if signature == st.session_state.get("last_saved_drawing"):
            st.warning("Este dibujo ya está guardado.")
        else:
            st.session_state.annotations.append(
                SemanticAnnotation.manual(annotation_type, drawing_geometry, annotation_description)
            )
            st.session_state.last_saved_drawing = signature
            st.success("Anotación manual guardada.")
            st.rerun()
with selection_col:
    if st.button(
        "Usar dibujo como AOI",
        width="stretch",
        disabled=drawing_geometry is None
        or interaction_mode != "Dibujar"
        or drawing_geometry.get("type") not in {"Polygon", "MultiPolygon"},
    ):
        st.session_state.aoi_geojson = {
            "type": "Feature",
            "properties": {"source": "user_selected"},
            "geometry": drawing_geometry,
        }
        selected_shape = shape(drawing_geometry)
        st.session_state.map_center = (selected_shape.centroid.y, selected_shape.centroid.x)
        st.success("Área de trabajo seleccionada.")
        st.rerun()
    visible_bounds = map_data.get("bounds")
    if st.button(
        "Usar vista actual como AOI",
        width="stretch",
        disabled=interaction_mode != "Dibujar" or not visible_bounds,
        help="Selecciona como área el rectángulo que ocupa actualmente el mapa.",
    ):
        south_west = visible_bounds["_southWest"]
        north_east = visible_bounds["_northEast"]
        visible_polygon = {
            "type": "Polygon",
            "coordinates": [
                [
                    [south_west["lng"], south_west["lat"]],
                    [north_east["lng"], south_west["lat"]],
                    [north_east["lng"], north_east["lat"]],
                    [south_west["lng"], north_east["lat"]],
                    [south_west["lng"], south_west["lat"]],
                ]
            ],
        }
        st.session_state.aoi_geojson = {
            "type": "Feature",
            "properties": {"source": "visible_map_extent"},
            "geometry": visible_polygon,
        }
        st.success("La vista actual se ha guardado como área de trabajo.")
        st.rerun()
with export_col:
    if selected_output:
        session_config = {
            "project": str(selected_output),
            "base_map": base_map,
            "center": st.session_state.map_center,
            "zoom": map_data.get("zoom", st.session_state.map_zoom),
            "active_layers": [layer.key for layer, _opacity in selected_layers],
            "aoi": st.session_state.aoi_geojson,
            "data_scope": "public_and_user_provided_only",
        }
        st.download_button(
            "Exportar proyecto ZIP",
            data=lambda: project_archive(
                selected_output,
                st.session_state.annotations,
                session_config,
                st.session_state.contours,
                include_layers=[layer.path for layer in export_layers],
            ),
            file_name=f"{selected_output.name}_semantic_project.zip",
            mime="application/zip",
            width="stretch",
        )
with coordinate_col:
    clicked = map_data.get("last_clicked")
    if clicked:
        st.caption(f"Punto consultado: {clicked['lat']:.6f}, {clicked['lng']:.6f}")
    elif interaction_mode == "Explorar":
        st.caption("Exploración fluida activa: mueve y amplía el mapa libremente.")
    elif interaction_mode == "Dibujar":
        st.caption("Usa las herramientas de dibujo situadas sobre el mapa.")
    else:
        st.caption("Haz clic en el mapa para consultar valores.")

line_geometry = (
    drawing_geometry if drawing_geometry and drawing_geometry.get("type") == "LineString" else None
)

with st.container(border=True):
    add_line_col, add_point_col, waypoint_help_col = st.columns([1.2, 1.2, 2.2])
    if add_line_col.button(
        "Añadir línea dibujada",
        width="stretch",
        disabled=line_geometry is None,
        help="Añade todos los vértices de la última línea como waypoints manuales.",
    ):
        updated = list(clean_manual_waypoints)
        for longitude, latitude in line_geometry["coordinates"]:
            updated.append(
                {
                    "name": f"WP{len(updated) + 1}",
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                }
            )
        st.session_state.mission_waypoints = updated
        st.session_state.waypoint_editor_version += 1
        st.rerun()
    if add_point_col.button(
        "Añadir punto consultado",
        width="stretch",
        disabled=clicked is None,
    ):
        updated = list(clean_manual_waypoints)
        updated.append(
            {
                "name": f"WP{len(updated) + 1}",
                "latitude": float(clicked["lat"]),
                "longitude": float(clicked["lng"]),
            }
        )
        st.session_state.mission_waypoints = updated
        st.session_state.waypoint_editor_version += 1
        st.rerun()
    waypoint_help_col.caption(
        "Naranja: objetivo manual · Morado: objetivo semántico automático · Azul: ruta"
    )

    route_state, route_clear = st.columns([4.0, 1.0])
    with route_state:
        if active_route:
            mission_count = len(active_route.targets)
            st.caption(
                f"Ruta activa · {active_route.metrics['distance_m']:.1f} m · "
                f"{active_route.metrics['waypoint_count']} waypoints · "
                f"{mission_count} objetivos de misión"
            )
        else:
            st.caption(
                f"Origen + {len(clean_manual_waypoints)} manuales + objetivos automáticos "
                f"de {', '.join(automatic_waypoint_labels) or 'ninguna capa'}"
            )
    with route_clear:
        if st.button("Quitar ruta", width="stretch", disabled=active_route is None):
            st.session_state.route_plan = None
            st.session_state.route_project = None
            st.session_state.hidden_route_project = str(selected_output)
            st.rerun()

    if active_route:
        distance_col, waypoint_col, target_col, profile_col = st.columns(4)
        distance_col.metric("Distancia", f"{active_route.metrics['distance_m']:.1f} m")
        waypoint_col.metric("Waypoints", active_route.metrics["waypoint_count"])
        target_col.metric(
            "Objetivos",
            len(active_route.targets),
        )
        profile_col.metric("Capas usadas", len(active_route.profile["used_layers"]))
        csv_col, yaml_col, geojson_col = st.columns(3)
        csv_col.download_button(
            "Descargar CSV",
            active_route.csv_text(),
            f"{active_route.name}.csv",
            "text/csv",
            width="stretch",
        )
        yaml_col.download_button(
            "Descargar YAML",
            active_route.yaml_text(),
            f"{active_route.name}.yaml",
            "application/yaml",
            width="stretch",
        )
        geojson_col.download_button(
            "Descargar GeoJSON",
            json.dumps(active_route.geojson(), ensure_ascii=False, indent=2),
            f"{active_route.name}.geojson",
            "application/geo+json",
            width="stretch",
        )
        with st.expander("Qué ha tenido en cuenta la ruta"):
            st.write("Capas activas: " + ", ".join(active_route.profile["used_layers"]))
            st.caption(
                "Ignoradas por defecto: " + ", ".join(active_route.profile["ignored_by_default"])
            )

if clicked and layers:
    with st.expander("Ficha del punto seleccionado", expanded=True):
        st.write(f"**Coordenadas WGS84:** {clicked['lat']:.7f}, {clicked['lng']:.7f}")
        sample_keys = [
            "elevation",
            "slope_degrees",
            "wetness_prior",
            "mud_risk",
            "water_accumulation_risk",
            "obstacle_probability",
            "traversability_prior",
            "confidence",
        ]
        columns = st.columns(3)
        for index, layer in enumerate(item for item in layers if item.key in sample_keys):
            value = sample_raster(layer.path, clicked["lat"], clicked["lng"])
            source, date, evidence = layer_source(layer, metadata)
            with columns[index % 3]:
                st.metric(
                    layer.style.label,
                    "Sin datos" if value is None else f"{value:.3g} {layer.style.unit}",
                )
                st.caption(f"{evidence} · {source} · {date}")
        if show_contours and elevation:
            elevation_value = sample_raster(elevation.path, clicked["lat"], clicked["lng"])
            if elevation_value is not None:
                associated_contour = round(elevation_value / contour_interval) * contour_interval
                st.caption(f"Curva de nivel más próxima: {associated_contour:g} m")

if selected_layers:
    with st.expander("Leyendas, fuentes y estado de capas", expanded=False):
        for layer, opacity in selected_layers:
            source, date, evidence = layer_source(layer, metadata)
            stats = cached_layer_statistics(str(layer.path), layer.path.stat().st_mtime_ns)
            status = "Disponible" if stats["valid_cells"] else "Sin datos válidos"
            st.markdown(f"**{layer.style.label}** · opacidad {opacity:.0%} · {status}")
            st.caption(
                f"{evidence} | Fuente: {source} | Fecha/descarga: {date} | "
                f"Resolución: {metadata.get('grid', {}).get('resolution_m', 'N/D')} m"
            )

if st.session_state.annotations:
    with st.expander("Exportar anotaciones por separado"):
        geojson = json.dumps(
            annotation_collection(st.session_state.annotations), ensure_ascii=False, indent=2
        )
        geo_col, csv_col = st.columns(2)
        geo_col.download_button(
            "GeoJSON",
            geojson,
            "semantic_annotations.geojson",
            "application/geo+json",
            width="stretch",
        )
        csv_col.download_button(
            "CSV",
            annotations_csv(st.session_state.annotations),
            "semantic_annotations.csv",
            "text/csv",
            width="stretch",
        )

st.caption(
    "Solo datos públicos y anotaciones introducidas por el usuario. Sin telemetría, sensores, "
    "SLAM, GPS en directo, ROS ni conexiones con robots. Los priors indican riesgo estimado."
)

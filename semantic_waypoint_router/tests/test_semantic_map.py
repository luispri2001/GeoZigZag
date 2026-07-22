from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import rasterio
from rasterio.transform import from_origin

from public_map_generator.export import annotations_csv, project_archive
from public_map_generator.map_ui import generate_contours_geojson, sample_raster
from public_map_generator.semantic import SemanticAnnotation, annotation_collection


def _write_elevation(path: Path) -> None:
    data = np.array([[100, 105, 110], [105, 110, 115], [110, 115, 120]], dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=3,
        height=3,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-5.0, 43.0, 0.01, 0.01),
        nodata=-9999,
    ) as dataset:
        dataset.write(data, 1)


def test_manual_annotation_keeps_provenance() -> None:
    annotation = SemanticAnnotation.manual(
        "Barro",
        {"type": "Point", "coordinates": [-5.0, 42.99]},
        "Marcado durante revisión cartográfica",
    )
    collection = annotation_collection([annotation])

    assert collection["features"][0]["properties"]["source"] == "manual"
    assert collection["features"][0]["properties"]["confidence"] is None
    assert collection["features"][0]["properties"]["validation"] == "confirmed_by_user"
    assert "Barro" in annotations_csv([annotation])


def test_contours_and_point_sampling(tmp_path: Path) -> None:
    elevation = tmp_path / "elevation.tif"
    _write_elevation(elevation)

    contours = generate_contours_geojson(elevation, 5.0)
    value = sample_raster(elevation, 42.995, -4.995)

    assert contours["type"] == "FeatureCollection"
    assert contours["features"]
    assert value == 100.0


def test_project_archive_is_reopenable(tmp_path: Path) -> None:
    (tmp_path / "metadata.json").write_text('{"project_name": "demo"}', encoding="utf-8")
    layer_path = tmp_path / "layers" / "terrain" / "elevation.tif"
    layer_path.parent.mkdir(parents=True)
    layer_path.write_bytes(b"test-geotiff")
    annotation = SemanticAnnotation.manual(
        "Tronco", {"type": "Point", "coordinates": [-5.0, 43.0]}
    )

    archive_data = project_archive(
        tmp_path,
        [annotation],
        {"base_map": "standard"},
        include_layers=[layer_path],
    )
    archive_path = tmp_path / "project.zip"
    archive_path.write_bytes(archive_data)

    with ZipFile(archive_path) as archive:
        assert "project.json" in archive.namelist()
        assert "annotations.geojson" in archive.namelist()
        assert "layers/terrain/elevation.tif" in archive.namelist()
        project = json.loads(archive.read("project.json"))
        assert project["base_map"] == "standard"

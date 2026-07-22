from __future__ import annotations

import csv
import json
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .semantic import SemanticAnnotation, annotation_collection


def annotations_csv(annotations: list[SemanticAnnotation]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "id",
            "type",
            "geometry_type",
            "description",
            "source",
            "confidence",
            "created_at",
            "source_date",
            "validation",
        ],
    )
    writer.writeheader()
    for annotation in annotations:
        writer.writerow(
            {
                "id": annotation.id,
                "type": annotation.annotation_type,
                "geometry_type": annotation.geometry.get("type"),
                "description": annotation.description,
                "source": annotation.source,
                "confidence": annotation.confidence,
                "created_at": annotation.created_at,
                "source_date": annotation.source_date,
                "validation": annotation.validation,
            }
        )
    return buffer.getvalue()


def project_archive(
    output_dir: str | Path,
    annotations: list[SemanticAnnotation],
    session_config: dict,
    contours: dict | None = None,
    include_layers: list[Path] | None = None,
) -> bytes:
    root = Path(output_dir)
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "annotations.geojson",
            json.dumps(annotation_collection(annotations), ensure_ascii=False, indent=2),
        )
        archive.writestr("annotations.csv", annotations_csv(annotations))
        archive.writestr(
            "project.json", json.dumps(session_config, ensure_ascii=False, indent=2)
        )
        if contours:
            archive.writestr(
                "contours.geojson", json.dumps(contours, ensure_ascii=False, indent=2)
            )
        for relative in ["metadata.json", "config_used.yaml", "aoi_wgs84.geojson"]:
            source = root / relative
            if source.exists():
                archive.write(source, relative)
        for source in include_layers or []:
            if source.exists() and source.is_file() and source.is_relative_to(root):
                archive.write(source, source.relative_to(root))
        for preview in (root / "preview").glob("*.png"):
            archive.write(preview, preview.relative_to(root))
        for route_file in (root / "routes").glob("**/*"):
            if route_file.is_file():
                archive.write(route_file, route_file.relative_to(root))
    return buffer.getvalue()

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

AnnotationSource = Literal["manual", "public", "inferred"]


@dataclass
class SemanticAnnotation:
    id: str
    annotation_type: str
    geometry: dict[str, Any]
    description: str
    source: AnnotationSource
    confidence: float | None
    created_at: str
    source_date: str | None = None
    validation: str = "unreviewed"

    @classmethod
    def manual(
        cls, annotation_type: str, geometry: dict[str, Any], description: str = ""
    ) -> SemanticAnnotation:
        return cls(
            id=str(uuid4()),
            annotation_type=annotation_type,
            geometry=geometry,
            description=description,
            source="manual",
            confidence=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            validation="confirmed_by_user",
        )

    def to_feature(self) -> dict[str, Any]:
        properties = asdict(self)
        geometry = properties.pop("geometry")
        return {"type": "Feature", "id": self.id, "geometry": geometry, "properties": properties}


def annotation_collection(annotations: list[SemanticAnnotation]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [annotation.to_feature() for annotation in annotations],
    }


def save_annotations(path: str | Path, annotations: list[SemanticAnnotation]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(annotation_collection(annotations), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination

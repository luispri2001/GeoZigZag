from __future__ import annotations

import html
from pathlib import Path


def create_qgis_project(project_path: Path, layer_paths: dict[str, Path]) -> Path:
    """Crea un proyecto QGIS básico con rutas relativas y todas las capas ráster."""
    project_path.parent.mkdir(parents=True, exist_ok=True)
    layers_xml = []
    tree_xml = []
    for index, (name, path) in enumerate(layer_paths.items(), start=1):
        relative = path.relative_to(project_path.parent).as_posix()
        layer_id = f"{name}_{index}"
        safe_name = html.escape(name)
        safe_path = html.escape(relative)
        tree_xml.append(
            f'<layer-tree-layer name="{safe_name}" checked="Qt::Checked" expanded="1" id="{layer_id}"/>'
        )
        layers_xml.append(
            f"""
    <maplayer type="raster" hasScaleBasedVisibilityFlag="0" autoRefreshEnabled="0" autoRefreshTime="0">
      <id>{layer_id}</id>
      <datasource>{safe_path}</datasource>
      <layername>{safe_name}</layername>
      <provider>gdal</provider>
      <customproperties/>
      <pipe>
        <rasterrenderer type="singlebandgray" opacity="1" alphaBand="-1" grayBand="1">
          <rasterTransparency/>
          <minMaxOrigin><limits>MinMax</limits><extent>WholeRaster</extent><statAccuracy>Estimated</statAccuracy></minMaxOrigin>
          <contrastEnhancement><algorithm>StretchToMinimumMaximum</algorithm></contrastEnhancement>
        </rasterrenderer>
        <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
        <huesaturation colorizeOn="0" colorizeRed="255" colorizeGreen="128" colorizeBlue="128" colorizeStrength="100" grayscaleMode="0" saturation="0"/>
        <rasterresampler maxOversampling="2"/>
      </pipe>
    </maplayer>
""".rstrip()
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<qgis projectname="Public Map Generator" version="3.34.0">
  <homePath path="."/>
  <title>Public Map Generator</title>
  <layer-tree-group name="" checked="Qt::Checked" expanded="1">
    {''.join(tree_xml)}
  </layer-tree-group>
  <projectlayers>
    {''.join(layers_xml)}
  </projectlayers>
  <properties/>
</qgis>
"""
    project_path.write_text(xml, encoding="utf-8")
    return project_path

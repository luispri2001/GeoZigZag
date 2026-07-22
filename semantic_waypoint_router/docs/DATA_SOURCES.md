# Fuentes de datos

## IGN/CNIG MDT05

- Servicio: WCS 1.0.0/2.0.1.
- Cobertura utilizada en UTM 30: `Elevacion25830_5`.
- Paso de malla: 5 m.
- Uso: elevación, pendiente, rugosidad y relieve local.

## PNOA Máxima Actualidad

- Servicio: WMS 1.1.1.
- Capa: `OI.OrthoimageCoverage`.
- Uso: visualización y referencia espacial.
- El cliente divide la petición en teselas para evitar límites de tamaño del servidor.

## OpenStreetMap

- Acceso: Overpass API.
- Elementos: edificios, carreteras, pistas, caminos, agua, humedales, bosques, cultivos, matorral y barreras.
- Limitación: la ausencia de un elemento no demuestra que no exista.

## Sentinel-2

- Colección: `sentinel-2-l2a`.
- Acceso: STAC de Microsoft Planetary Computer.
- Índices: NDVI y NDMI.
- Las clases de nube, sombra y nieve de SCL se eliminan antes del cálculo.

## MDS

- Conector experimental al servicio `https://wcs-mds.idee.es/mds`.
- Cobertura configurada: `mds05`.
- Está desactivado por defecto y cualquier fallo se registra en `metadata.json`.

## Mapas base y búsqueda

- Mapa estándar: teselas de OpenStreetMap.
- Satélite e híbrido: Esri World Imagery; la atribución se muestra en el mapa.
- Relieve: OpenTopoMap, derivado de OSM y SRTM.
- Búsqueda: servicio público Nominatim de OpenStreetMap, con caché temporal.

Los mapas base son únicamente una referencia visual y no se exportan como si fueran datos propios.
Cada proveedor puede no estar disponible temporalmente y requiere conexión a Internet.

## Interpretación semántica

- Los objetos OSM son datos públicos cartografiados, no observaciones actuales.
- `wetness_prior`, `obstacle_probability` y `traversability_prior` son inferencias calculadas.
- NDMI es un índice espectral de humedad de vegetación/superficie, no una medición de humedad del
  suelo ni confirmación de barro.
- Charcos, barro y troncos solo se representan como anotaciones manuales, salvo que en el futuro se
  incorpore un método público verificable; nunca se generan como detecciones confirmadas.

## Riesgos inferidos

`mud_risk` combina el prior de humedad, pendiente y coberturas blandas (cultivo, pastizal y
vegetación). `water_accumulation_risk` combina el prior de humedad, pendiente, relieve local y agua
cartografiada. Ambos se expresan en `[0, 1]`, se acompañan de la capa `confidence` y son riesgos
estimados, no detecciones de barro o charcos.

Actualmente no hay proveedor implementado de humedad del suelo ni precipitación reciente. Estas
entradas aparecen como **Fuente no disponible** en el estado del trabajo y no se sustituyen por
valores simulados.

# Arquitectura

```text
AOI
 ├── centro + radio
 ├── bbox WGS84
 └── GeoJSON
        │
        ▼
Cuadrícula común EPSG:25830
        │
        ├── IGN MDT05 ───────► elevación, pendiente, rugosidad y saltos
        ├── PNOA WMS ────────► ortofoto de referencia
        ├── OSM/Overpass ────► edificios, vías, agua, vegetación y barreras
        ├── IGN MDS opcional ► altura sobre el terreno
        └── Sentinel-2 ──────► NDVI y NDMI
                                │
                                ▼
                       Fusión probabilística
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
 obstacle_probability   traversability_prior      confidence
          │                     │                     │
          └──────────────┬──────┴─────────────────────┘
                         ▼
                Costmap explicable de ruta
                         │
            ┌────────────┼─────────────┐
            ▼            ▼             ▼
       origen/A*    waypoints     objetivos OSM
                    editables     automáticos
```

## Criterios de diseño

1. La misión ordenada de waypoints es el producto principal de la aplicación.
2. Todas las capas analíticas se alinean con la misma cuadrícula y sirven de contexto al planner.
3. Los datos originales se conservan en `source/`.
4. Cada ejecución guarda la configuración exacta utilizada.
5. Las fuentes opcionales pueden fallar sin impedir la creación del mapa base.
6. El MDT es obligatorio porque proporciona la geometría mínima del terreno.
7. La salida diferencia entre medidas geométricas y estimaciones o priors.

## Interfaz cartográfica

La interfaz no llama directamente a los descargadores. Consume proyectos generados mediante un
catálogo común de capas y mantiene separados estos componentes:

```text
Streamlit UI
 ├── búsqueda pública (Nominatim)
 ├── estado de mapa y AOI
 ├── panel de capas
 ├── editor de anotaciones
 ├── configuración y métricas de ruta
 └── exportación
        │
        ▼
Folium map adapter
 ├── proveedores XYZ de mapa base
 ├── overlays GeoTIFF reproyectados visualmente
 ├── vectores OSM consultables
 ├── ruta, inicio y fin
 └── dibujo, consulta puntual y curvas de nivel
        │
        ▼
Pipeline geoespacial existente + archivos locales del proyecto
```

`layer_viewer.py` define el catálogo y la simbología; `map_ui.py` adapta el catálogo al mapa;
`semantic.py` define anotaciones con procedencia; `export.py` crea productos portables. Esta
separación permite sustituir un proveedor cartográfico sin cambiar el procesamiento principal.

`jobs.py` ejecuta el pipeline en un proceso separado y guarda un manifiesto de trabajo atómico en
`outputs/.jobs/`. La interfaz consulta ese manifiesto desde un fragmento de Streamlit cada dos
segundos. El proceso principal mantiene el mapa interactivo, puede solicitar cancelación y carga
progresivamente las capas que ya existen. PNOA, OSM, MDS y Sentinel-2 se consultan mediante un
pool limitado a cuatro tareas; cada resultado se incorpora de forma independiente.

`route_planner.py` adapta el barrido de cobertura y el A* de GeoZigZag a la cuadrícula ráster real.
Mantiene separados tres conceptos: capas visibles, capas relevantes para costes y capas objetivo.
El perfil normal usa bloqueos públicos explícitos y terreno; las capas ambientales quedan fuera
hasta que una misión las solicita. Los puntos WGS84 se proyectan al CRS del proyecto para calcular
distancias, yaw y colisiones, y se transforman de nuevo a WGS84 al exportar.

La misión de waypoints preserva el orden indicado por el usuario y añade después componentes
semánticos ordenados por vecino más próximo. Solo se aceptan objetivos conectados al origen. Para
una masa semántica inaccesible se calcula, cuando existe a menos de 250 m, un punto de aproximación
perteneciente al mismo componente transitable que el origen.

No existe ningún adaptador de robot, ROS, telemetría o sensores en esta arquitectura.

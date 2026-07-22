# Validación incluida

Comprobaciones ejecutadas antes de empaquetar:

- Carga de la configuración YAML de ejemplo.
- Creación y reproyección del AOI.
- Cálculo de pendiente sobre un plano conocido.
- Conservación de zonas sin datos.
- Fusión de edificios como obstáculos letales.
- Bonificación de coste en vías cartografiadas.
- Generación completa del ejemplo sintético y de sus GeoTIFF/PNG.

Resultado local: **5 pruebas superadas**.

No se descargaron datos reales durante el empaquetado porque el entorno de creación no disponía de acceso de red desde el contenedor. Los endpoints y parámetros están configurados a partir de la documentación pública indicada en el README.

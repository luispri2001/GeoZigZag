# Uso del visor semántico

## Flujo

1. Ejecuta `streamlit run app.py`.
2. Busca una dirección, lugar o coordenadas WGS84.
3. Elige mapa estándar, satélite, relieve o híbrido.
4. Usa **Explorar** para desplazarte y hacer zoom sin recargar la aplicación. Cambia a
   **Consultar** para leer valores o a **Dibujar** para seleccionar geometrías.
5. Activa capas geográficas, ambientales o semánticas y ajusta su opacidad.
6. Dibuja un rectángulo o polígono y pulsa **Usar dibujo como AOI**.
7. Pulsa **Generar mapas del área**. Todas las fuentes configuradas se intentan en segundo plano;
   el panel de estado se puede plegar y el mapa continúa disponible.
8. Haz clic en el mapa para consultar elevación, pendiente, priors y confianza.
9. Para documentar un problema, dibuja punto, línea o polígono, selecciona el tipo y guarda la
   anotación. La aplicación registra su fuente como `manual`.
10. Abre **Planificación de ruta** y escribe el origen. Por defecto se propone Tabuyo del Monte:
    `42.3135981, -6.2027894`.
11. Edita la tabla de waypoints. Puedes crear filas, importar una línea dibujada o añadir el último
    punto consultado.
12. Activa los recursos para objetivos automáticos: bosque, matorral, masas o cursos de agua,
    humedales, pastizal o cultivo. Ajusta el área mínima y el número máximo de objetivos.
13. Pulsa **Generar ruta entre waypoints**. Revisa la línea azul, los objetivos manuales naranjas,
    los recursos automáticos morados y las métricas.
14. Descarga la ruta en CSV, YAML o GeoJSON, o exporta el ZIP completo del proyecto.

## Capas usadas por el planificador

Mostrar una capa y usarla para calcular la ruta son decisiones independientes. El perfil normal
solo bloquea edificios, agua, cursos de agua, barreras, pendientes y escalones fuera de límite.
Los obstáculos inferidos añaden coste y las vías reducen coste. Las capas de vegetación, humedad,
barro y satélite no participan por defecto.

Las clases activadas pasan a ser capas objetivo: se detectan zonas conectadas con el área mínima
configurada y se ordenan por proximidad después de los waypoints manuales. Agua, cursos de agua y
humedales conservan además su función de bloqueo: su waypoint se coloca fuera del recurso. Si una
masa no contiene celdas alcanzables, puede crearse un punto de aproximación seguro a un máximo de
250 m; el YAML y GeoJSON indican la relación y distancia al elemento.

Si no hay conectividad entre dos objetivos, la aplicación detiene el cálculo y lo muestra. No usa
una línea recta de respaldo porque podría atravesar un edificio o una masa de agua.

## Evidencia y procedencia

- **Pública:** elementos obtenidos de IGN, PNOA, OpenStreetMap o Sentinel-2.
- **Derivada/inferida:** cálculos de terreno y priors. No confirman que un fenómeno esté presente.
- **Manual:** geometría y descripción añadidas por el usuario.
- **Sin datos:** se muestra explícitamente cuando una celda o proveedor no aporta información.

Una anotación manual confirma que el usuario la registró; la aplicación no puede confirmar por sí
misma la existencia física del elemento. La fecha de creación y la fecha de la fuente son campos
distintos.

## Caché y actualización

El buscador y los productos visuales usan la caché de Streamlit. **Actualizar catálogo de capas** o
**Limpiar caché** no elimina archivos generados. Para volver a consultar fuentes públicas hay que
generar un proyecto nuevo o confirmar explícitamente la sustitución de una salida existente.

Los proyectos se reutilizan cuando coinciden nombre, AOI y resolución. Si cambia el área sin pedir
una sustitución, se crea una salida con sufijo temporal para conservar el proyecto anterior.

## Limitaciones

- Los mapas base y la búsqueda requieren Internet.
- La cancelación termina el proceso de generación y conserva los archivos parciales ya escritos;
  no garantiza que un servidor remoto interrumpa inmediatamente una respuesta en curso.
- Folium adapta bien el mapa a escritorio y móvil; en pantallas pequeñas el panel lateral se abre
  como panel superpuesto de Streamlit.
- La captura PNG compuesta del visor no se exporta todavía; sí se exportan previews, GeoTIFF,
  GeoJSON, CSV y configuración del proyecto.
- No se reciben datos de robots, GPS en directo, cámaras, LiDAR, SLAM, odometría ni ROS.

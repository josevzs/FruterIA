# CARON — inventario urbano español

Herramienta de inventario cartográfico de elementos urbanos para cualquier municipio español, inspirada en el trabajo *les villes rangées* de Armelle Caron.

## Qué hace

Descarga geometría vectorial (edificios, manzanas, parcelas) de cualquier municipio español desde OpenStreetMap y el Catastro, la clasifica por área y la reorganiza en un inventario ordenado de mayor a menor, exportable a **SVG**, **DXF** y **PDF**.

## Estructura

```
caron-inventario/
├── app/
│   ├── downloader.py   — descarga OSM + Catastro WFS INSPIRE
│   ├── caron.py        — algoritmo de clasificación y disposición
│   ├── exporter.py     — exportadores DXF / SVG / PDF
│   └── main.py         — API FastAPI
├── static/
│   └── index.html      — webapp (vanilla JS, sin dependencias)
├── requirements.txt
└── README.md
```

## Instalar

```bash
pip install -r requirements.txt
```

## Ejecutar la webapp

```bash
uvicorn app.main:app --reload --port 8000
```

Abre `http://localhost:8000` en el navegador.

### Con Docker

```bash
docker compose up -d        # http://localhost:8014
```

## Datos desde tsuchi

En los servidores privados, FruterIA puede recibir los datos de
[tsuchi](https://github.com/josevzs/tsuchiCore), el backbone de datos que comparten las
herramientas: tsuchi descarga los edificios, parcelas o manzanas de una ventana y los pasa
aquí, y FruterIA se abre con el trabajo cargado; solo queda ajustar el layout y pulsar
**Generar**.

- `GET /api/tsuchi/info` y `POST /api/tsuchi/import` ([app/tsuchi.py](app/tsuchi.py)): baja el
  GeoPackage de `TSUCHI_DATA_HOSTS` (por defecto `tsuchi-data`), lo pasa a EPSG:25830 y deja
  el trabajo preparado; devuelve `/?job=<id>`. Con `TSUCHI_IMPORT_KEY`, el hub debe enviarla
  en `X-Tsuchi-Key`.
- `POST /api/jobs/<id>/run` recalcula el inventario con otro layout sobre los datos que el
  trabajo ya tiene, sin volver a descargar.
- El contenedor tiene que unirse a la red Docker `tsuchi` del hub: ver el comentario de
  [docker-compose.yml](docker-compose.yml).

## Uso desde línea de comandos

```bash
python -m app.cli --municipio "Moraleja de Enmedio" --elemento edificios --output output/
```

*(El CLI se generará en una próxima versión)*

## Fuentes de datos

- **Edificios**: Catastro BU WFS INSPIRE (fallback: OpenStreetMap Overpass)
- **Manzanas**: Poligonización de la red viaria de OSM
- **Parcelas**: Catastro CP WFS INSPIRE (fallback: landuse de OSM)
- **Límites municipales**: OpenStreetMap (Nominatim + Overpass)

## Créditos

Concepto original: [Armelle Caron](https://www.armellecaron.fr/) — *les villes rangées*

---

*Proyecto académico — Introducción al Urbanismo — 2º Arquitectura*

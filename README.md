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

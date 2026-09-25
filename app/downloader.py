"""
Descarga geometria vectorial de municipios espanoles.
Fuentes: Catastro INSPIRE bulk (principal) + OSM Overpass (fallback).
"""

import io
import os
import threading
import time
import zipfile
import tempfile
from pathlib import Path

import requests
import geopandas as gpd
from shapely.geometry import Polygon, LineString, MultiPolygon
from shapely.ops import unary_union, polygonize, linemerge

HEADERS = {
    "User-Agent": "CaronInventario/1.0 (academic project; contact: github.com/caron-inventario)"
}
# Overpass va a menudo saturado (504/429): se prueban varias instancias públicas
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS         = OVERPASS_MIRRORS[0]
NOMINATIM        = "https://nominatim.openstreetmap.org"
CATASTRO_INSPIRE_BASE = "https://www.catastro.hacienda.gob.es/INSPIRE"

# Caché local: evita re-descargar ZIPs (pueden ser 1-50 MB)
_CACHE_DIR = Path(tempfile.gettempdir()) / "caron_catastro_cache"
_CACHE_DIR.mkdir(exist_ok=True)


# ── Búsqueda ──────────────────────────────────────────────────────────────────

# Nominatim pide como mucho 1 petición por segundo; la UI busca mientras se escribe, así que
# se cachean las respuestas y se espacian las peticiones desde el servidor.
_search_cache: dict[str, list[dict]] = {}
_search_lock = threading.Lock()
_search_last = [0.0]


def buscar_municipio(q: str) -> list[dict]:
    """Busca municipios espanoles por nombre. Devuelve lista de candidatos."""
    key = " ".join(q.lower().split())
    if key in _search_cache:
        return _search_cache[key]
    with _search_lock:
        if key in _search_cache:
            return _search_cache[key]
        wait = 1.0 - (time.monotonic() - _search_last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            out = _buscar_nominatim(q)
        finally:
            _search_last[0] = time.monotonic()
        if len(_search_cache) > 500:
            _search_cache.clear()
        _search_cache[key] = out
        return out


def _buscar_nominatim(q: str) -> list[dict]:
    r = requests.get(
        f"{NOMINATIM}/search",
        params={
            "q": q,
            "countrycodes": "es",
            "format": "jsonv2",
            "limit": 10,
            "addressdetails": 1,
        },
        headers=HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    out = []
    for item in r.json():
        if item.get("type") not in (
            "administrative", "city", "town", "village", "hamlet", "municipality"
        ):
            if item.get("class") != "boundary":
                continue
        addr = item.get("address", {})
        bb = item.get("boundingbox") or []          # [sur, norte, oeste, este] como texto
        out.append({
            "display_name": item["display_name"],
            "osm_id":       item["osm_id"],
            "osm_type":     item["osm_type"],
            "lat":          float(item["lat"]),
            "lon":          float(item["lon"]),
            "bounds":       [[float(bb[0]), float(bb[2])], [float(bb[1]), float(bb[3])]] if len(bb) == 4 else None,
            "municipio":    (addr.get("municipality") or addr.get("city")
                             or addr.get("town") or addr.get("village")
                             or addr.get("hamlet") or q),
            "provincia":    addr.get("province") or addr.get("state_district") or addr.get("county", ""),
            "comunidad":    addr.get("state", ""),
        })
    return out


# ── Límite municipal ───────────────────────────────────────────────────────────

def _overpass(query: str, timeout: int = 120) -> dict:
    """Consulta Overpass probando las instancias por turno; 429/5xx y cortes pasan a la siguiente."""
    errors = []
    for attempt, url in enumerate(OVERPASS_MIRRORS * 2):
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=timeout)
            if r.status_code in (429, 502, 503, 504):
                errors.append(f"{url.split('/')[2]}: HTTP {r.status_code}")
            else:
                r.raise_for_status()
                return r.json()
        except (requests.ConnectionError, requests.Timeout, ValueError) as e:
            errors.append(f"{url.split('/')[2]}: {type(e).__name__}")
        if attempt >= len(OVERPASS_MIRRORS) - 1:
            time.sleep(3)                       # segunda vuelta: dar respiro a los servidores
    raise RuntimeError("OpenStreetMap (Overpass) no responde ahora mismo; prueba en unos minutos. "
                       + "; ".join(errors[-3:]))


def obtener_limite(osm_type: str, osm_id: int) -> gpd.GeoDataFrame:
    """Obtiene el limite municipal desde OSM como GDF en EPSG:25830.
    Almacena ref_ine (código INE de 5 dígitos) para descargas del Catastro.
    """
    code = {"node": "node", "way": "way", "relation": "relation"}.get(
        osm_type.lower(), "relation"
    )
    data = _overpass(f"""
        [out:json][timeout:60];
        {code}({osm_id});
        out geom;
    """)
    elements = data.get("elements", [])
    if not elements:
        raise ValueError(f"No se encontro limite para {osm_type}/{osm_id}")

    el   = elements[0]
    tags = el.get("tags", {})
    # ref:ine → código INE del municipio (5 dígitos: PP + MMM)
    ref_ine = tags.get("ref:ine", "")

    geom = None

    if el["type"] == "relation":
        members_way = [
            m for m in el.get("members", [])
            if m.get("type") == "way" and m.get("role", "") in ("outer", "")
        ]
        lines = []
        for m in members_way:
            coords = [(nd["lon"], nd["lat"]) for nd in m.get("geometry", [])]
            if len(coords) >= 2:
                lines.append(LineString(coords))

        if lines:
            merged = linemerge(unary_union(lines))
            polys  = list(polygonize(merged))
            if not polys:
                lines2 = []
                for m in el.get("members", []):
                    if m.get("type") == "way":
                        coords = [(nd["lon"], nd["lat"]) for nd in m.get("geometry", [])]
                        if len(coords) >= 2:
                            lines2.append(LineString(coords))
                if lines2:
                    polys = list(polygonize(linemerge(unary_union(lines2))))
            if polys:
                geom = unary_union(polys)

    elif el["type"] == "way":
        coords = [(nd["lon"], nd["lat"]) for nd in el.get("geometry", [])]
        if len(coords) >= 3:
            geom = Polygon(coords)

    if geom is None or geom.is_empty:
        all_coords = []
        for m in el.get("members", []):
            all_coords.extend([(nd["lon"], nd["lat"]) for nd in m.get("geometry", [])])
        if all_coords:
            from shapely.geometry import MultiPoint
            hull = MultiPoint(all_coords).convex_hull
            if not hull.is_empty:
                geom = hull
        if geom is None or geom.is_empty:
            raise ValueError(f"No se pudo construir el limite de {osm_type}/{osm_id}")

    gdf = gpd.GeoDataFrame({"geometry": [geom], "ref_ine": [ref_ine]}, crs="EPSG:4326")
    return gdf.to_crs("EPSG:25830")


# ── Utilidades ─────────────────────────────────────────────────────────────────

def _bbox_wgs(gdf: gpd.GeoDataFrame) -> tuple[float, float, float, float]:
    b = gdf.to_crs("EPSG:4326").total_bounds
    return float(b[0]), float(b[1]), float(b[2]), float(b[3])


def _clip_to_boundary(gdf: gpd.GeoDataFrame, boundary_utm: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    bnd = unary_union(boundary_utm.geometry)
    gdf = gdf[gdf.geometry.intersects(bnd)].copy()
    gdf.geometry = gdf.geometry.intersection(bnd)
    # el recorte en el borde puede dejar colecciones con líneas o puntos: solo lo poligonal
    gdf.geometry = gdf.geometry.apply(_solo_poligonos)
    gdf = gdf[gdf.geometry.notna() & gdf.geometry.is_valid & ~gdf.geometry.is_empty].copy()
    return gdf.reset_index(drop=True)


def _solo_poligonos(g):
    if g is None or g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    if g.geom_type == "GeometryCollection":
        parts = [p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")]
        return unary_union(parts) if parts else None
    return None


def _ref_ine(boundary_utm: gpd.GeoDataFrame) -> str:
    """Extrae el código INE almacenado en el GDF de límite."""
    if "ref_ine" in boundary_utm.columns:
        v = str(boundary_utm["ref_ine"].iloc[0])
        if v and v != "nan":
            return v
    return ""


# ── Catastro INSPIRE bulk downloads ───────────────────────────────────────────
# Servidor: www.catastro.hacienda.gob.es/INSPIRE (estático, distinto al WFS)
# La URL exacta de cada municipio se obtiene del ATOM feed de provincia:
#   https://www.catastro.hacienda.gob.es/INSPIRE/{Tipo}/{PP}/ES.SDGC.{tipo}.atom_{PP}.xml
# Tipos: Buildings (BU), CadastralParcels (CP)

import re as _re

def _catastro_atom_url(ref_ine: str, tipo_cap: str, tipo_lower: str) -> str | None:
    """Busca en el ATOM feed de provincia la URL de descarga exacta del municipio."""
    province = ref_ine[:2]
    muni_code = ref_ine[:5].zfill(5)

    atom_url = (
        f"{CATASTRO_INSPIRE_BASE}/{tipo_cap}/{province}/"
        f"ES.SDGC.{tipo_lower}.atom_{province}.xml"
    )
    try:
        r = requests.get(atom_url, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            print(f"[catastro_atom] HTTP {r.status_code} para {atom_url}")
            return None
        # Buscar href que contenga el código municipal exacto
        pattern = rf'href="([^"]*/{muni_code}[^"]*\.zip)"'
        m = _re.search(pattern, r.text)
        if m:
            return m.group(1)
        print(f"[catastro_atom] no encontrado {muni_code} en el feed")
        return None
    except Exception as e:
        print(f"[catastro_atom] {e}")
        return None


def _catastro_inspire_bulk(
    ref_ine: str,
    tipo_cap: str,   # "Buildings" o "CadastralParcels"
    tipo_code: str,  # "BU" o "CP"
    boundary_utm: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame | None:
    """Descarga y parsea el GML del Catastro INSPIRE para un municipio."""
    if not ref_ine or len(ref_ine) < 5:
        return None

    muni_code = ref_ine[:5].zfill(5)
    tipo_lower = tipo_code.lower()

    cache_key = f"catastro_{tipo_code}_{muni_code}.gml"
    cache_file = _CACHE_DIR / cache_key

    if not cache_file.exists():
        # Obtener la URL real desde el ATOM feed
        url = _catastro_atom_url(ref_ine, tipo_cap, tipo_lower)
        if not url:
            return None

        print(f"[catastro_bulk] descargando {url}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=300)
            if r.status_code != 200:
                print(f"[catastro_bulk] HTTP {r.status_code}")
                return None
            raw = r.content
            if len(raw) < 1000:
                return None

            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                # Para BU: usar building.gml (excluir buildingpart y otherconstruction)
                # Para CP: usar el primero que no sea metadata
                gml_names = [n for n in z.namelist() if n.lower().endswith(".gml")]
                # CP trae también cadastralzoning.gml (zonas, no parcelas): según el orden
                # del ZIP se cogía ese en lugar de las parcelas
                exclude   = {"buildingpart", "otherconstruction", "cadastralzoning"}
                main_gml  = next(
                    (n for n in gml_names
                     if not any(ex in n.lower() for ex in exclude)),
                    None
                )
                if main_gml is None:
                    main_gml = max(gml_names, key=lambda n: z.getinfo(n).file_size)
                if main_gml is None:
                    print("[catastro_bulk] no hay .gml en el ZIP")
                    return None

                print(f"[catastro_bulk] extrayendo {main_gml} ({z.getinfo(main_gml).file_size//1024} KB)")
                with z.open(main_gml) as gf:
                    cache_file.write_bytes(gf.read())

            print(f"[catastro_bulk] guardado en caché: {cache_file}")
        except Exception as e:
            print(f"[catastro_bulk] error descarga: {e}")
            return None
    else:
        print(f"[catastro_bulk] usando caché: {cache_file}")

    try:
        gdf = gpd.read_file(str(cache_file))
        if gdf is None or len(gdf) == 0:
            return None
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:25830")
        gdf = gdf.to_crs("EPSG:25830")
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
        gdf = gdf[gdf.geometry.is_valid & ~gdf.geometry.is_empty].reset_index(drop=True)
        if len(gdf) == 0:
            return None
        print(f"[catastro_bulk] {len(gdf)} geometrías antes de clip")
        return _clip_to_boundary(gdf, boundary_utm)
    except Exception as e:
        print(f"[catastro_bulk] error parseando GML: {e}")
        cache_file.unlink(missing_ok=True)
        return None


def _sin_catastro(ref: str, que: str) -> str:
    """Motivo legible de que el Catastro no haya dado nada (antes salía «0 encontrados»)."""
    if not ref:
        return (f"OSM no tiene el código INE de este municipio y el Catastro lo necesita para "
                f"descargar {que}. Prueba con fuente Auto u OSM.")
    if ref[:2] in ("01", "20", "31", "48"):
        return (f"Este municipio está en territorio foral (País Vasco o Navarra), que tiene su propio "
                f"catastro: no hay {que} en el Catastro estatal. Prueba con fuente OSM.")
    return (f"El Catastro no ha servido {que} para el municipio {ref[:5]} (su servidor no responde o no "
            f"lo publica). Prueba otra vez en un rato o con fuente Auto u OSM.")


# ── Edificios ──────────────────────────────────────────────────────────────────

def descargar_edificios(
    boundary_utm: gpd.GeoDataFrame,
    fuente: str = "catastro",
) -> gpd.GeoDataFrame:
    """Descarga edificios.
    fuente: 'catastro' | 'osm' | 'auto' (catastro con fallback OSM)
    """
    ref = _ref_ine(boundary_utm)

    if fuente in ("catastro", "auto"):
        if ref:
            gdf = _catastro_inspire_bulk(ref, "buildings", "BU", boundary_utm)
            if gdf is not None and len(gdf) > 0:
                print(f"[edificios] {len(gdf)} edificios del Catastro INSPIRE")
                gdf.attrs["fuente_real"] = "catastro"
                return gdf
            print("[edificios] Catastro bulk falló" + ("" if fuente == "auto" else " — prueba con fuente=auto u osm"))
        else:
            print("[edificios] ref:ine no disponible en OSM" + ("" if fuente == "auto" else " — prueba con fuente=auto u osm"))
        if fuente == "catastro":
            raise ValueError(_sin_catastro(ref, "edificios"))
        print("[edificios] usando OSM como fallback")

    # OSM Overpass
    lon0, lat0, lon1, lat1 = _bbox_wgs(boundary_utm)
    data = _overpass(f"""
        [out:json][timeout:90];
        (
          way["building"]({lat0},{lon0},{lat1},{lon1});
          relation["building"]["type"="multipolygon"]({lat0},{lon0},{lat1},{lon1});
        );
        out geom;
    """)

    polys = []
    for el in data.get("elements", []):
        if el["type"] == "way":
            coords = [(nd["lon"], nd["lat"]) for nd in el.get("geometry", [])]
            if len(coords) >= 3:
                try:
                    p = Polygon(coords)
                    if p.is_valid and p.area > 0:
                        polys.append({"geometry": p})
                except Exception:
                    pass

    if not polys:
        return gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:25830")

    gdf = gpd.GeoDataFrame(polys, crs="EPSG:4326").to_crs("EPSG:25830")
    gdf = _clip_to_boundary(gdf, boundary_utm)
    gdf.attrs["fuente_real"] = "osm"
    print(f"[edificios] {len(gdf)} edificios de OSM")
    return gdf


# ── Manzanas ──────────────────────────────────────────────────────────────────

def descargar_manzanas(
    boundary_utm: gpd.GeoDataFrame,
    min_area: float = 500.0,
    max_area: float = 200000.0,
) -> gpd.GeoDataFrame:
    """Poligoniza la red viaria de OSM para obtener manzanas urbanas."""
    lon0, lat0, lon1, lat1 = _bbox_wgs(boundary_utm)

    data = _overpass(f"""
        [out:json][timeout:90];
        (
          way["highway"~"motorway|trunk|primary|secondary|tertiary|residential|living_street|pedestrian|footway|service"]({lat0},{lon0},{lat1},{lon1});
          way["railway"]({lat0},{lon0},{lat1},{lon1});
          way["waterway"~"river|stream|canal"]({lat0},{lon0},{lat1},{lon1});
        );
        out geom;
    """)

    lines = []
    for el in data.get("elements", []):
        if el["type"] == "way":
            coords = [(nd["lon"], nd["lat"]) for nd in el.get("geometry", [])]
            if len(coords) >= 2:
                lines.append(LineString(coords))

    if not lines:
        return gpd.GeoDataFrame(columns=["geometry", "area"], crs="EPSG:25830")

    bnd_wgs = unary_union(
        gpd.GeoDataFrame(geometry=boundary_utm.geometry, crs="EPSG:25830")
        .to_crs("EPSG:4326")
        .geometry
    )
    lines.append(bnd_wgs.boundary)

    merged = unary_union(lines)
    polys  = list(polygonize(merged))

    if not polys:
        return gpd.GeoDataFrame(columns=["geometry", "area"], crs="EPSG:25830")

    gdf = gpd.GeoDataFrame(geometry=polys, crs="EPSG:4326").to_crs("EPSG:25830")
    gdf["area"] = gdf.geometry.area
    gdf = gdf[(gdf.area >= min_area) & (gdf.area <= max_area)].copy()
    gdf = _clip_to_boundary(gdf, boundary_utm)
    gdf["area"] = gdf.geometry.area
    return gdf[(gdf.area >= min_area)].reset_index(drop=True)


# ── Parcelas ───────────────────────────────────────────────────────────────────

def descargar_parcelas(
    boundary_utm: gpd.GeoDataFrame,
    fuente: str = "catastro",
) -> gpd.GeoDataFrame:
    """Descarga parcelas catastrales.
    fuente: 'catastro' | 'osm' | 'auto' (catastro con fallback OSM landuse)
    """
    ref = _ref_ine(boundary_utm)

    if fuente in ("catastro", "auto"):
        if ref:
            gdf = _catastro_inspire_bulk(ref, "CadastralParcels", "CP", boundary_utm)
            if gdf is not None and len(gdf) > 0:
                print(f"[parcelas] {len(gdf)} parcelas del Catastro INSPIRE")
                gdf.attrs["fuente_real"] = "catastro"
                return gdf
            print("[parcelas] Catastro bulk falló" + ("" if fuente == "auto" else " — prueba con fuente=auto u osm"))
        else:
            print("[parcelas] ref:ine no disponible en OSM" + ("" if fuente == "auto" else " — prueba con fuente=auto u osm"))
        if fuente == "catastro":
            raise ValueError(_sin_catastro(ref, "parcelas"))
        print("[parcelas] usando OSM landuse como fallback")

    # Fallback: landuse de OSM como proxy de parcelas
    lon0, lat0, lon1, lat1 = _bbox_wgs(boundary_utm)
    return _parcelas_osm_fallback(boundary_utm, lon0, lat0, lon1, lat1)


def _parcelas_osm_fallback(boundary_utm, lon0, lat0, lon1, lat1) -> gpd.GeoDataFrame:
    data = _overpass(f"""
        [out:json][timeout:60];
        (
          way["landuse"~"residential|commercial|industrial|retail|farmland|meadow|grass|forest"]({lat0},{lon0},{lat1},{lon1});
          way["natural"~"wood|scrub|heath|grassland"]({lat0},{lon0},{lat1},{lon1});
        );
        out geom;
    """)
    polys = []
    for el in data.get("elements", []):
        if el["type"] == "way":
            coords = [(nd["lon"], nd["lat"]) for nd in el.get("geometry", [])]
            if len(coords) >= 3:
                try:
                    p = Polygon(coords)
                    if p.is_valid and p.area > 0:
                        polys.append({"geometry": p})
                except Exception:
                    pass
    if not polys:
        return gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:25830")
    gdf = gpd.GeoDataFrame(polys, crs="EPSG:4326").to_crs("EPSG:25830")
    result = _clip_to_boundary(gdf, boundary_utm)
    result.attrs["fuente_real"] = "osm"
    return result

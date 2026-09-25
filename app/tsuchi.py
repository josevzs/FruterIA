"""
Importación desde tsuchi («tsuchi import v1»).

tsuchi (github.com/josevzs/tsuchiCore) es el backbone de datos de los despliegues
privados (panda-server, el estudio): descarga una vez los edificios, parcelas o
manzanas de una ventana y se los pasa a FruterIA. El hub hace un POST con el
manifiesto del activo y enlaces a sus ficheros; aquí se baja el GeoPackage, se
lleva a EPSG:25830 (el CRS con el que trabaja todo el inventario) y se deja un
trabajo *preparado*: el usuario llega a la UI con los datos cargados, ajusta el
layout y pulsa Generar.

Sin tsuchi, FruterIA sigue igual: estas rutas solo se usan si alguien las llama.
Con TSUCHI_IMPORT_KEY definida, el hub tiene que mandarla en X-Tsuchi-Key.
"""

import io
import os
from urllib.parse import urlparse

import geopandas as gpd
import requests
from shapely.geometry import box

CONTRACT = 1
IMPORT_KEY = os.environ.get("TSUCHI_IMPORT_KEY", "")
# Hosts de los que se aceptan ficheros: el servicio tsuchi-data de la red Docker
# compartida. Evita que la importación sirva para pedir URLs arbitrarias.
DATA_HOSTS = {h.strip() for h in os.environ.get("TSUCHI_DATA_HOSTS", "tsuchi-data").split(",") if h.strip()}
MAX_BYTES = int(os.environ.get("TSUCHI_MAX_MB", "500")) * 1024 * 1024

# conjunto de tsuchi → elemento del inventario Caron
ELEMENTOS = {
    "catastro/buildings": "edificios",
    "osm/buildings": "edificios",
    "catastro/parcels": "parcelas",
    "osm/blocks": "manzanas",
}


class TsuchiImportError(ValueError):
    """Petición de importación que no se puede aceptar (mensaje apto para el usuario)."""


def info() -> dict:
    return {"accepts": ["vector"], "datasets": sorted(ELEMENTOS), "contract": CONTRACT, "version": "1.0.0"}


def check_key(key: str | None) -> bool:
    return not IMPORT_KEY or key == IMPORT_KEY


def load(body: dict) -> dict:
    """Valida la petición, baja el GeoPackage y devuelve lo que necesita el trabajo."""
    if body.get("tsuchi") != CONTRACT:
        raise TsuchiImportError(f"contrato tsuchi {body.get('tsuchi')!r} no soportado (este es v{CONTRACT})")
    asset = body.get("asset") or {}
    key = f"{asset.get('provider')}/{asset.get('dataset')}"
    elemento = (body.get("options") or {}).get("elemento") or ELEMENTOS.get(key)
    if asset.get("kind") != "vector" or elemento not in ("edificios", "parcelas", "manzanas"):
        raise TsuchiImportError(f"FruterIA no sabe qué hacer con {key} ({asset.get('kind')})")

    files = body.get("files") or []
    gpkg = next((f for f in files if str(f.get("name", "")).lower().endswith(".gpkg")), None)
    if gpkg is None:
        raise TsuchiImportError("el activo no trae ningún GeoPackage")
    url = urlparse(str(gpkg.get("url", "")))
    if url.scheme not in ("http", "https") or url.hostname not in DATA_HOSTS:
        raise TsuchiImportError(f"solo se aceptan ficheros de {sorted(DATA_HOSTS)}, no de {url.hostname!r}")

    w = asset.get("window") or {}
    try:
        west, south, east, north = (float(w[k]) for k in ("west", "south", "east", "north"))
    except (KeyError, TypeError, ValueError):
        raise TsuchiImportError("asset.window debe traer west/south/east/north") from None

    gdf = _read_gpkg(gpkg["url"])
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    gdf = (gdf.set_crs(asset.get("crs") or "EPSG:25830") if gdf.crs is None else gdf).to_crs("EPSG:25830")
    gdf = gdf.reset_index(drop=True)
    gdf.attrs["fuente_real"] = f"tsuchi:{key}"

    # la ventana hace de «límite municipal» para los mapas exportados
    boundary = gpd.GeoDataFrame({"geometry": [box(west, south, east, north)]}, crs="EPSG:4326").to_crs("EPSG:25830")
    return {
        "elemento": elemento,
        "gdf": gdf,
        "boundary": boundary,
        "fuente_real": f"tsuchi:{key}",
        "label": asset.get("title") or key,
        "tsuchi_asset": asset.get("id"),
    }


def _read_gpkg(url: str) -> gpd.GeoDataFrame:
    with requests.get(url, stream=True, timeout=(10, 300)) as r:
        r.raise_for_status()
        buf = io.BytesIO()
        for chunk in r.iter_content(chunk_size=1 << 20):
            buf.write(chunk)
            if buf.tell() > MAX_BYTES:
                raise TsuchiImportError(f"el GeoPackage supera {MAX_BYTES // 1048576} MB")
    buf.seek(0)
    return gpd.read_file(buf)

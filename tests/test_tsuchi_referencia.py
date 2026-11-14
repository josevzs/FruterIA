"""Referencia de territorio (tsuchiCore docs/traspaso.md, desde 2026-11-14): un trabajo que llega de
una ventana de tsuchi guarda de qué ventana y territorio sale; sin ellos, todo como antes.

    python -m pytest tests
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import pytest
from shapely.geometry import box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import main, tsuchi  # noqa: E402

SITE = {"id": "3af8f867e7", "slug": "el-pardo-vaso", "name": "El Pardo · vaso 1:25.000",
        "window": {"west": -3.78, "south": 40.29, "east": -3.76, "north": 40.30}, "crs": "EPSG:25830",
        "geometry": {"type": "Polygon", "coordinates": []}, "radius_m": None, "notes": "",
        "escala": 25000, "nivel": "media", "madre_id": None}
TERRITORIO = {"id": "0b1c2d3e4f", "slug": "el-pardo", "nombre": "El Pardo"}


def cuerpo(**extra) -> dict:
    """Un «tsuchi import v1» como el que manda el hub (ejemplo de tsuchiCore docs/contratos)."""
    return {
        "tsuchi": 1,
        "producer": {"system": "tsuchi", "version": "0.4.0"},
        "asset": {"id": "catastro-buildings-8f88c6f1", "provider": "catastro", "dataset": "buildings",
                  "kind": "vector", "title": "Edificios catastrales", "crs": "EPSG:25830",
                  "window": {"west": -3.78, "south": 40.29, "east": -3.76, "north": 40.30}},
        "files": [{"name": "buildings.gpkg",
                   "url": "http://tsuchi-data:8000/api/assets/catastro-buildings-8f88c6f1/files/buildings.gpkg"}],
        "options": {},
        **extra,
    }


@pytest.fixture(autouse=True)
def gpkg(monkeypatch):
    """El GeoPackage de tsuchi-data, sin red: dos edificios dentro de la ventana."""
    gdf = gpd.GeoDataFrame({"geometry": [box(-3.771, 40.294, -3.7705, 40.2944),
                                         box(-3.769, 40.296, -3.7688, 40.2962)]}, crs="EPSG:4326")
    monkeypatch.setattr(tsuchi, "_read_gpkg", lambda url: gdf.copy())


def test_desde_una_ventana_el_trabajo_guarda_de_donde_sale():
    ref = tsuchi.load(cuerpo(site=SITE, territorio=TERRITORIO))["tsuchi_ref"]
    assert ref["ventana"] == {"id": "3af8f867e7", "slug": "el-pardo-vaso", "name": "El Pardo · vaso 1:25.000"}
    assert ref["territorio"] == TERRITORIO
    visto = datetime.fromisoformat(ref["visto"])
    assert visto.utcoffset() == timedelta(0)
    assert abs((datetime.now(timezone.utc) - visto).total_seconds()) < 60
    assert set(ref) == {"ventana", "territorio", "visto"}


def test_sin_ventana_no_hay_referencia_y_todo_sigue_igual():
    data = tsuchi.load(cuerpo())
    assert data["tsuchi_ref"] is None
    assert data["elemento"] == "edificios" and data["label"] == "Edificios catastrales" and len(data["gdf"]) == 2


@pytest.mark.parametrize("extra, territorio", [
    ({"site": "3af8f867e7"}, None),                                   # no es un objeto: se ignora
    ({"site": {"slug": "sin-id"}, "territorio": TERRITORIO}, None),   # sin id de ventana no hay referencia
])
def test_una_referencia_mal_formada_se_ignora_sin_rechazar(extra, territorio):
    assert tsuchi.load(cuerpo(**extra))["tsuchi_ref"] is None


def test_la_ventana_sin_territorio_se_guarda_igual():
    ref = tsuchi.load(cuerpo(site=SITE, territorio="El Pardo"))["tsuchi_ref"]
    assert ref["ventana"]["id"] == "3af8f867e7" and ref["territorio"] is None


@pytest.mark.parametrize("extra, esperado", [
    ({"site": SITE, "territorio": TERRITORIO}, "El Pardo"),
    ({}, None),
])
def test_la_api_devuelve_la_referencia_del_trabajo(extra, esperado):
    r = main.tsuchi_import(cuerpo(**extra), x_tsuchi_key=None)
    job_id = r["job_id"]
    try:
        st = main.status(job_id)
        assert st["status"] == "staged"
        assert (st["tsuchi_ref"] or {}).get("territorio", {}).get("nombre") == esperado
        # Generar sobre los datos importados: la referencia sigue en el trabajo terminado
        main._run_worker(job_id, main.LayoutRequest().caron_params())
        st = main.status(job_id)
        assert st["status"] == "done" and st["n"] == 2
        assert (st["tsuchi_ref"] or {}).get("territorio", {}).get("nombre") == esperado
    finally:
        main._jobs.pop(job_id, None)

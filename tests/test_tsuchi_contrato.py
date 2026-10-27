"""El contrato con tsuchi (app/tsuchi.py): qué versiones anuncia y cómo rechaza las demás.

    python -m pytest tests
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import tsuchi  # noqa: E402


def test_anuncia_lo_que_admite():
    i = tsuchi.info()
    assert i["contracts"] == [1] and i["contract"] == 1 and i["producer"]["system"] == "fruteria"


def test_una_version_que_no_conoce_se_rechaza_diciendo_cuales_valen():
    with pytest.raises(tsuchi.TsuchiImportError, match=r"v2 \(de tsuchi 0.4.0\) no admitido en FruterIA: se admite v1"):
        tsuchi.load({"tsuchi": 2, "producer": {"system": "tsuchi", "version": "0.4.0"}})


def test_un_host_que_no_es_tsuchi_no_se_toca():
    with pytest.raises(tsuchi.TsuchiImportError, match="solo se aceptan ficheros"):
        tsuchi.load({"tsuchi": 1, "asset": {"provider": "osm", "dataset": "buildings", "kind": "vector"},
                     "files": [{"name": "b.gpkg", "url": "http://otra/b.gpkg"}]})

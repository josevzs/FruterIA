"""
FastAPI backend para el inventario Caron de municipios espanoles.
"""

import asyncio
import os
import threading
import uuid
from datetime import datetime
from typing import Optional, Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Header
from fastapi.responses import Response, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import downloader as dl
from . import caron
from .caron import CaronParams
from . import exporter
from . import tsuchi
from .exporter import SvgStyle, PdfParams, MapStyle, export_map_dxf

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Caron Inventario", version="1.0.0")

STATIC_DIR = Path(__file__).parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── Job store (in-memory) ──────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
# Cada job guarda su GeoDataFrame y su resultado: sin límite, la memoria solo crece.
MAX_JOBS = int(os.environ.get("CARON_MAX_JOBS", "30"))


def _new_job(data: dict) -> str:
    """Registra un job y descarta los más antiguos que ya no estén en marcha."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = data
    idle = [j for j, d in _jobs.items() if d["status"] in ("done", "error", "staged") and j != job_id]
    for j in idle[: max(0, len(_jobs) - MAX_JOBS)]:
        _jobs.pop(j, None)
    return job_id


def _job_worker(
    job_id: str, osm_type: str, osm_id: int,
    elemento: str, params: CaronParams, fuente: str = "catastro",
):
    try:
        _jobs[job_id]["status"] = "downloading"
        boundary = dl.obtener_limite(osm_type, osm_id)

        _jobs[job_id]["status"] = f"downloading_{elemento}"
        if elemento == "edificios":
            gdf = dl.descargar_edificios(boundary, fuente=fuente)
        elif elemento == "manzanas":
            gdf = dl.descargar_manzanas(boundary)
        elif elemento == "parcelas":
            gdf = dl.descargar_parcelas(boundary, fuente=fuente)
        else:
            raise ValueError(f"Elemento desconocido: {elemento}")

        if len(gdf) == 0:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = f"No se encontraron {elemento} en este municipio."
            return

        _jobs[job_id]["status"] = "processing"
        result = caron.run(gdf, params)

        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = result
        _jobs[job_id]["gdf"] = gdf
        _jobs[job_id]["boundary"] = boundary
        _jobs[job_id]["fuente_real"] = gdf.attrs.get("fuente_real", "desconocida")

    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(e)


def _run_worker(job_id: str, params: CaronParams):
    """Caron sobre los datos que el trabajo ya tiene (importados de tsuchi, o de
    una generación anterior): sin volver a descargar nada."""
    job = _jobs[job_id]
    try:
        job["status"] = "processing"
        job["result"] = caron.run(job["gdf"], params)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


# ── Models ────────────────────────────────────────────────────────────────────

class LayoutRequest(BaseModel):
    """Parámetros del inventario (lo que se ajusta antes de pulsar Generar)."""
    sort_mode: Literal["area", "perimeter", "width", "height"] = "area"
    sheet_width: float = Field(400.0, gt=0)
    col_spacing: float = Field(4.0, ge=0)
    row_spacing: float = Field(8.0, ge=0)
    scale_factor: float = Field(1.0, gt=0)
    rotate_to_fit: bool = True
    min_area: float = Field(0.0, ge=0)
    max_area: float = Field(0.0, ge=0)  # 0 = sin limite
    simplify_tolerance: float = Field(0.0, ge=0)

    def caron_params(self) -> CaronParams:
        return CaronParams(
            sort_mode=self.sort_mode,
            sheet_width=self.sheet_width,
            col_spacing=self.col_spacing,
            row_spacing=self.row_spacing,
            scale_factor=self.scale_factor,
            rotate_to_fit=self.rotate_to_fit,
            min_area=self.min_area,
            max_area=self.max_area if self.max_area > 0 else float("inf"),
            simplify_tolerance=self.simplify_tolerance,
        )


class GenerateRequest(LayoutRequest):
    osm_type: str
    osm_id: int
    elemento: Literal["edificios", "manzanas", "parcelas"] = "edificios"
    fuente: Literal["catastro", "osm", "auto"] = "catastro"


class ExportRequest(BaseModel):
    job_id: str
    municipio: str = ""
    # SVG / DXF / PDF style overrides
    stroke: str = "#1a1a1a"
    stroke_width: float = 0.4
    fill: str = "#e8e8e8"
    fill_opacity: float = 0.6
    background: str = "#ffffff"
    show_stats: bool = True
    padding: float = 10.0
    # PDF only
    page_width_mm: float = 420.0
    page_height_mm: float = 297.0
    margin_mm: float = 15.0
    stroke_color_r: float = 0.1
    stroke_color_g: float = 0.1
    stroke_color_b: float = 0.1
    fill_color_r: float = 0.91
    fill_color_g: float = 0.91
    fill_color_b: float = 0.91
    fill_alpha: float = 0.7
    accent_r: float = 0.8
    accent_g: float = 0.0
    accent_b: float = 0.0


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>Caron Inventario</h1><p>Frontend not found</p>")


# Las rutas que esperan a la red o calculan (búsqueda, estado, exportaciones) son `def`, no
# `async def`: FastAPI las corre en hilos. Como `async def` bloqueaban el bucle de eventos y,
# mientras Nominatim o matplotlib tardaban, el servidor entero dejaba de responder.
@app.get("/api/search")
def search(q: str = Query(..., min_length=2)):
    """Busca municipios por nombre."""
    try:
        return dl.buscar_municipio(q)
    except Exception as e:
        raise HTTPException(502, f"La búsqueda de municipios (Nominatim) no responde: {e}")


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    """Inicia la descarga y procesado. Devuelve job_id para consultar estado."""
    params = req.caron_params()
    job_id = _new_job({
        "status": "queued",
        "created": datetime.utcnow().isoformat(),
        "elemento": req.elemento,
        "result": None,
        "error": None,
    })
    t = threading.Thread(
        target=_job_worker,
        args=(job_id, req.osm_type, req.osm_id, req.elemento, params, req.fuente),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/run")
async def run_job(job_id: str, req: LayoutRequest):
    """Calcula el inventario sobre los datos que el job ya tiene (importados de
    tsuchi o de una generación anterior) con otro layout, sin descargar."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado")
    if job.get("gdf") is None or job["status"] not in ("staged", "done", "error"):
        raise HTTPException(409, f"El job está {job['status']}: no tiene datos listos")
    job["error"] = None
    job["_payload"] = None
    job["status"] = "queued"
    threading.Thread(target=_run_worker, args=(job_id, req.caron_params()), daemon=True).start()
    return {"job_id": job_id}


# ── tsuchi ────────────────────────────────────────────────────────────────────

@app.get("/api/tsuchi/info")
async def tsuchi_info(x_tsuchi_key: Optional[str] = Header(default=None)):
    if not tsuchi.check_key(x_tsuchi_key):
        raise HTTPException(401, "Falta X-Tsuchi-Key o no es la correcta")
    return tsuchi.info()


@app.post("/api/tsuchi/import", status_code=201)
def tsuchi_import(body: dict, x_tsuchi_key: Optional[str] = Header(default=None)):
    """Recibe un activo de tsuchi y lo deja preparado. Síncrona (hilo del pool):
    baja el GeoPackage antes de responder, para que la UI lo encuentre ya."""
    if not tsuchi.check_key(x_tsuchi_key):
        raise HTTPException(401, "Falta X-Tsuchi-Key o no es la correcta")
    try:
        data = tsuchi.load(body)
    except tsuchi.TsuchiImportError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(502, f"No se pudo leer el activo de tsuchi: {e}")
    if len(data["gdf"]) == 0:
        raise HTTPException(422, f"El activo no tiene {data['elemento']}")
    job_id = _new_job({
        "status": "staged",
        "created": datetime.utcnow().isoformat(),
        "result": None,
        "error": None,
        **data,
    })
    return {"job_id": job_id, "open": f"/?job={job_id}"}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    """Devuelve el estado del job y, si esta done, los datos del inventario."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado")

    if job["status"] == "staged":
        gdf = job["gdf"]
        b = gdf.to_crs("EPSG:4326").total_bounds
        return {
            "status": "staged",
            "elemento": job["elemento"],
            "fuente_real": job.get("fuente_real"),
            "label": job.get("label", ""),
            "n_input": int(len(gdf)),
            "map_bounds": [[round(b[1], 5), round(b[0], 5)], [round(b[3], 5), round(b[2], 5)]],
        }

    if job["status"] != "done":
        return {
            "status": job["status"],
            "error": job.get("error"),
        }

    # la respuesta de un job terminado no cambia hasta que se recalcula: se guarda
    if job.get("_payload") is not None:
        return job["_payload"]

    result: caron.CaronResult = job["result"]
    p = result.params
    gdf = job.get("gdf")
    boundary = job.get("boundary")

    # Datos geográficos para el mapa Leaflet (WGS84 simplificado)
    geo_features = []
    map_bounds = None
    if gdf is not None and len(gdf) > 0:
        import geopandas as gpd
        gdf_wgs = gdf.to_crs("EPSG:4326")
        gdf_wgs.geometry = gdf_wgs.geometry.simplify(0.00005, preserve_topology=True)
        b = gdf_wgs.total_bounds  # minx miny maxx maxy = lon_min lat_min lon_max lat_max
        map_bounds = [[round(b[1], 5), round(b[0], 5)], [round(b[3], 5), round(b[2], 5)]]
        from shapely.geometry import mapping as shp_mapping
        for idx, row in gdf_wgs.iterrows():
            g = row.geometry
            if g is None or g.is_empty:
                continue
            geo_features.append(shp_mapping(g))

    job["_payload"] = {
        "status": "done",
        "elemento": job["elemento"],
        "fuente_real": job.get("fuente_real", "desconocida"),
        "n": result.n,
        "total_area": result.total_area,
        "mean_area": result.mean_area,
        "max_area": result.max_area,
        "min_area": result.min_area,
        "sheet_width": result.sheet_width,
        "sheet_height": result.sheet_height,
        "params": {
            "sort_mode": p.sort_mode,
            "sheet_width": p.sheet_width,
            "col_spacing": p.col_spacing,
            "row_spacing": p.row_spacing,
            "scale_factor": p.scale_factor,
            "rotate_to_fit": p.rotate_to_fit,
        },
        "polygons": [
            {
                "rank": item.rank,
                "area": item.area,
                "perimeter": item.perimeter,
                "width": item.width,
                "height": item.height,
                "exterior": item.exterior,
                "holes": item.holes,
            }
            for item in result.items
        ],
        "geo_features": geo_features,
        "map_bounds": map_bounds,
    }
    return job["_payload"]


@app.post("/api/export/svg")
def export_svg(req: ExportRequest):
    job = _jobs.get(req.job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(404, "Job no listo")

    style = SvgStyle(
        stroke=req.stroke,
        stroke_width=req.stroke_width,
        fill=req.fill,
        fill_opacity=req.fill_opacity,
        background=req.background,
        show_stats=req.show_stats,
        padding=req.padding,
    )
    svg = exporter.export_svg(job["result"], style, req.municipio, job["elemento"])
    return Response(content=svg, media_type="image/svg+xml")


@app.post("/api/export/dxf")
def export_dxf(req: ExportRequest):
    job = _jobs.get(req.job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(404, "Job no listo")

    data = exporter.export_dxf(job["result"], req.municipio, job["elemento"])
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="caron_{req.municipio}_{job["elemento"]}.dxf"'},
    )


@app.post("/api/export/pdf")
def export_pdf(req: ExportRequest):
    job = _jobs.get(req.job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(404, "Job no listo")

    pdf_params = PdfParams(
        page_width_mm=req.page_width_mm,
        page_height_mm=req.page_height_mm,
        margin_mm=req.margin_mm,
        stroke_color=(req.stroke_color_r, req.stroke_color_g, req.stroke_color_b),
        fill_color=(req.fill_color_r, req.fill_color_g, req.fill_color_b),
        fill_alpha=req.fill_alpha,
        accent_color=(req.accent_r, req.accent_g, req.accent_b),
        show_stats=req.show_stats,
    )
    data = exporter.export_pdf(job["result"], pdf_params, req.municipio, job["elemento"])
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="caron_{req.municipio}_{job["elemento"]}.pdf"'},
    )


class MapExportRequest(BaseModel):
    job_id: str
    municipio: str = ""
    with_basemap: bool = True
    basemap_source: str = "CartoDB.Positron"
    fill_r: float = 0.8
    fill_g: float = 0.0
    fill_b: float = 0.0
    fill_alpha: float = 0.45
    stroke_r: float = 0.1
    stroke_g: float = 0.1
    stroke_b: float = 0.1
    stroke_width: float = 0.4
    bg_r: float = 1.0
    bg_g: float = 1.0
    bg_b: float = 1.0
    dpi: int = 150
    show_boundary: bool = True
    show_scale: bool = True


@app.post("/api/export/map-png")
def export_map_png(req: MapExportRequest):
    job = _jobs.get(req.job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(404, "Job no listo")

    gdf = job.get("gdf")
    boundary = job.get("boundary")
    if gdf is None or boundary is None:
        raise HTTPException(500, "Datos geograficos no disponibles")

    style = MapStyle(
        fill_color=(req.fill_r, req.fill_g, req.fill_b),
        fill_alpha=req.fill_alpha,
        stroke_color=(req.stroke_r, req.stroke_g, req.stroke_b),
        stroke_width=req.stroke_width,
        bg_color=(req.bg_r, req.bg_g, req.bg_b),
        basemap_source=req.basemap_source,
        dpi=req.dpi,
        show_boundary=req.show_boundary,
        show_scale=req.show_scale,
    )
    data = exporter.export_map_png(gdf, boundary, style, req.municipio, job["elemento"], req.with_basemap)
    suffix = "con_osm" if req.with_basemap else "sin_fondo"
    return Response(
        content=data,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="mapa_{req.municipio}_{job["elemento"]}_{suffix}.png"'},
    )


@app.post("/api/export/map-svg")
def export_map_svg(req: MapExportRequest):
    job = _jobs.get(req.job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(404, "Job no listo")

    gdf = job.get("gdf")
    boundary = job.get("boundary")
    if gdf is None or boundary is None:
        raise HTTPException(500, "Datos geograficos no disponibles")

    style = MapStyle(
        fill_color=(req.fill_r, req.fill_g, req.fill_b),
        fill_alpha=req.fill_alpha,
        stroke_color=(req.stroke_r, req.stroke_g, req.stroke_b),
        stroke_width=req.stroke_width,
        bg_color=(req.bg_r, req.bg_g, req.bg_b),
        show_boundary=req.show_boundary,
        show_scale=req.show_scale,
    )
    svg = exporter.export_map_svg(gdf, boundary, style, req.municipio, job["elemento"])
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Content-Disposition": f'attachment; filename="mapa_{req.municipio}_{job["elemento"]}.svg"'},
    )


@app.post("/api/export/map-dxf")
def export_map_dxf(req: MapExportRequest):
    job = _jobs.get(req.job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(404, "Job no listo")

    gdf = job.get("gdf")
    boundary = job.get("boundary")
    if gdf is None or boundary is None:
        raise HTTPException(500, "Datos geograficos no disponibles")

    style = MapStyle(
        fill_color=(req.fill_r, req.fill_g, req.fill_b),
        fill_alpha=req.fill_alpha,
        stroke_color=(req.stroke_r, req.stroke_g, req.stroke_b),
        stroke_width=req.stroke_width,
        bg_color=(req.bg_r, req.bg_g, req.bg_b),
        show_boundary=req.show_boundary,
        show_scale=req.show_scale,
    )
    data = exporter.export_map_dxf(gdf, boundary, style, req.municipio, job["elemento"])
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="mapa_{req.municipio}_{job["elemento"]}.dxf"'},
    )


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    _jobs.pop(job_id, None)
    return {"ok": True}

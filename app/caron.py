"""
Algoritmo de inventario estilo Armelle Caron — "les villes rangees".
Clasifica geometrias por area/perimetro/anchura y las dispone en cuadricula.
100% Python puro con shapely. No requiere Rhino ni Grasshopper.
"""

import math
from dataclasses import dataclass, field
from typing import Literal
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.affinity import translate, rotate, scale
from shapely.ops import unary_union
import geopandas as gpd


SortMode = Literal["area", "perimeter", "width", "height"]


@dataclass
class CaronParams:
    sort_mode: SortMode = "area"
    sheet_width: float = 400.0
    col_spacing: float = 4.0
    row_spacing: float = 8.0
    scale_factor: float = 1.0
    rotate_to_fit: bool = True
    min_area: float = 0.0
    max_area: float = float("inf")
    simplify_tolerance: float = 0.0


@dataclass
class CaronItem:
    rank: int
    area: float
    perimeter: float
    width: float
    height: float
    polygon: Polygon
    # serializable rings para frontend SVG
    exterior: list[list[float]] = field(default_factory=list)
    holes: list[list[list[float]]] = field(default_factory=list)


@dataclass
class CaronResult:
    items: list[CaronItem]
    sheet_width: float
    sheet_height: float
    total_area: float
    mean_area: float
    max_area: float
    min_area: float
    n: int
    params: CaronParams


def _bounding_box(poly: Polygon) -> tuple[float, float]:
    """Devuelve (width, height) del bounding box alineado con XY."""
    b = poly.bounds  # minx, miny, maxx, maxy
    return b[2] - b[0], b[3] - b[1]


def _center_at_origin(poly: Polygon) -> Polygon:
    """Traslada el centroide del bounding box al origen."""
    b = poly.bounds
    cx = (b[0] + b[2]) / 2.0
    cy = (b[1] + b[3]) / 2.0
    return translate(poly, -cx, -cy)


def _rotate_to_min_width(poly: Polygon, step_deg: int = 3) -> Polygon:
    """Rota el poligono para minimizar su anchura (OBB aproximado)."""
    best_w = float("inf")
    best_deg = 0.0
    for deg in range(0, 90, step_deg):
        rotated = rotate(poly, deg, origin=(0, 0), use_radians=False)
        w, _ = _bounding_box(rotated)
        if w < best_w:
            best_w = w
            best_deg = deg
    return rotate(poly, best_deg, origin=(0, 0), use_radians=False)


def _as_polygon(geom) -> Polygon | None:
    """Convierte GeometryBase a Polygon (toma el mas grande si es MultiPolygon)."""
    if isinstance(geom, Polygon):
        return geom if geom.is_valid and not geom.is_empty else None
    if isinstance(geom, MultiPolygon):
        parts = [g for g in geom.geoms if g.is_valid and not g.is_empty]
        return max(parts, key=lambda g: g.area) if parts else None
    return None


def _poly_to_rings(poly: Polygon) -> tuple[list, list]:
    """Convierte un Polygon a listas de coordenadas para JSON."""
    ext = [[round(x, 3), round(y, 3)] for x, y in poly.exterior.coords]
    holes = [
        [[round(x, 3), round(y, 3)] for x, y in ring.coords]
        for ring in poly.interiors
    ]
    return ext, holes


def run(gdf: gpd.GeoDataFrame, params: CaronParams) -> CaronResult:
    """
    Ejecuta el inventario Caron sobre un GeoDataFrame.
    El GDF debe estar en coordenadas proyectadas (metros, ej. EPSG:25830).
    """
    # 1. Extraer y filtrar poligonos
    polys: list[Polygon] = []
    for geom in gdf.geometry:
        p = _as_polygon(geom)
        if p is None:
            continue
        if params.simplify_tolerance > 0:
            p = p.simplify(params.simplify_tolerance, preserve_topology=True)
        if params.scale_factor != 1.0:
            p = scale(p, params.scale_factor, params.scale_factor, origin=(0, 0))
        area = p.area
        if area < params.min_area or area > params.max_area:
            continue
        polys.append(p)

    if not polys:
        return CaronResult(
            items=[], sheet_width=params.sheet_width, sheet_height=0.0,
            total_area=0.0, mean_area=0.0, max_area=0.0, min_area=0.0,
            n=0, params=params,
        )

    # 2. Calcular metricas
    areas   = [p.area for p in polys]
    perims  = [p.length for p in polys]
    widths  = [_bounding_box(p)[0] for p in polys]

    # 3. Ordenar
    if params.sort_mode == "perimeter":
        idx = sorted(range(len(polys)), key=lambda i: perims[i], reverse=True)
    elif params.sort_mode == "width":
        idx = sorted(range(len(polys)), key=lambda i: widths[i], reverse=True)
    elif params.sort_mode == "height":
        idx = sorted(range(len(polys)), key=lambda i: _bounding_box(polys[i])[1], reverse=True)
    else:  # area (default)
        idx = sorted(range(len(polys)), key=lambda i: areas[i], reverse=True)

    # 4. Centrar y rotar
    centered: list[Polygon] = []
    ws: list[float] = []
    hs: list[float] = []
    for i in idx:
        p = _center_at_origin(polys[i])
        if params.rotate_to_fit:
            p = _center_at_origin(_rotate_to_min_width(p))
        w, h = _bounding_box(p)
        centered.append(p)
        ws.append(w)
        hs.append(h)

    # 5. Disponer en filas
    placed: list[Polygon] = []
    cx, cy, row_h = 0.0, 0.0, 0.0
    for i, p in enumerate(centered):
        w, h = ws[i], hs[i]
        if cx > 0 and (cx + w + params.col_spacing) > params.sheet_width:
            cx = 0.0
            cy -= row_h + params.row_spacing
            row_h = 0.0
        tx = cx + w / 2.0
        ty = cy - h / 2.0
        placed.append(translate(p, tx, ty))
        cx += w + params.col_spacing
        row_h = max(row_h, h)

    # 6. Calcular altura total del panel
    all_bounds = [p.bounds for p in placed]
    min_y = min(b[1] for b in all_bounds)
    max_y = max(b[3] for b in all_bounds)
    sheet_height = max_y - min_y

    # 7. Empaquetar resultados
    ordered_areas = [areas[i] for i in idx]
    items: list[CaronItem] = []
    for rank, (i, p) in enumerate(zip(idx, placed), start=1):
        ext, holes = _poly_to_rings(p)
        w, h = _bounding_box(p)
        items.append(CaronItem(
            rank=rank,
            area=round(areas[i], 2),
            perimeter=round(perims[i], 2),
            width=round(w, 2),
            height=round(h, 2),
            polygon=p,
            exterior=ext,
            holes=holes,
        ))

    total = sum(ordered_areas)
    return CaronResult(
        items=items,
        sheet_width=params.sheet_width,
        sheet_height=round(sheet_height, 2),
        total_area=round(total, 2),
        mean_area=round(total / len(items), 2),
        max_area=round(max(ordered_areas), 2),
        min_area=round(min(ordered_areas), 2),
        n=len(items),
        params=params,
    )

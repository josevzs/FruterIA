"""
Exportadores: DXF (ezdxf), SVG (string), PDF (matplotlib), mapa geográfico PNG/SVG.
"""

import io
import math
from dataclasses import dataclass
from typing import Optional
import ezdxf
from ezdxf import units
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import PathPatch
from matplotlib.path import Path
import numpy as np
from shapely.geometry import Polygon

from .caron import CaronResult, CaronItem


# ── DXF ───────────────────────────────────────────────────────────────────────

def export_dxf(result: CaronResult, municipio: str = "", elemento: str = "") -> bytes:
    """Exporta el inventario Caron a DXF (R2018, unidades metros)."""
    doc = ezdxf.new("R2018")
    doc.units = units.M
    msp = doc.modelspace()

    layer_name = f"CARON_{elemento.upper()}"
    doc.layers.add(layer_name, color=7)

    # Anadir texto de estadisticas
    p = result.params
    stats = (
        f"{municipio.upper()} — {elemento.upper()}\n"
        f"n={result.n}  S_total={result.total_area:,.1f}m2  "
        f"S_media={result.mean_area:,.1f}m2\n"
        f"S_max={result.max_area:,.1f}  S_min={result.min_area:,.1f}\n"
        f"Orden:{p.sort_mode}  panel:{p.sheet_width}  sep:{p.col_spacing}/{p.row_spacing}\n"
        f"d'apres Armelle Caron — les villes rangees"
    )
    msp.add_mtext(stats, dxfattribs={
        "layer": "ANOTACIONES",
        "char_height": result.sheet_width * 0.008,
        "insert": (0, result.sheet_height * 0.03),
    })

    # Anadir poligonos
    for item in result.items:
        coords = [(x, y) for x, y in item.exterior]
        if len(coords) >= 3:
            msp.add_lwpolyline(
                coords,
                format="xy",
                close=True,
                dxfattribs={"layer": layer_name},
            )
        for hole in item.holes:
            hcoords = [(x, y) for x, y in hole]
            if len(hcoords) >= 3:
                msp.add_lwpolyline(
                    hcoords,
                    format="xy",
                    close=True,
                    dxfattribs={"layer": layer_name + "_HUECOS"},
                )

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


# ── SVG ───────────────────────────────────────────────────────────────────────

@dataclass
class SvgStyle:
    stroke: str = "#1a1a1a"
    stroke_width: float = 0.4
    fill: str = "#e8e8e8"
    fill_opacity: float = 0.6
    background: str = "#ffffff"
    highlight: str = "#cc0000"
    font_family: str = "Helvetica Neue, Helvetica, Arial, sans-serif"
    show_stats: bool = True
    padding: float = 10.0


def export_svg(
    result: CaronResult,
    style: SvgStyle = None,
    municipio: str = "",
    elemento: str = "",
    width_px: int = 1200,
) -> str:
    """Genera un SVG del inventario Caron."""
    if style is None:
        style = SvgStyle()

    if result.n == 0:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"><text x="20" y="100" font-size="14">Sin datos</text></svg>'

    # Calcular viewBox desde las geometrias
    all_x = [x for item in result.items for x, y in item.exterior]
    all_y = [y for item in result.items for x, y in item.exterior]
    if not all_x:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200"></svg>'

    pad = style.padding
    vx0 = min(all_x) - pad
    vy0 = min(all_y) - pad
    vw  = max(all_x) - min(all_x) + pad * 2
    vh  = max(all_y) - min(all_y) + pad * 2

    # SVG coordenadas Y invertidas
    def tx(x): return x - vx0
    def ty(y): return vh - (y - vy0)

    def ring_to_d(ring):
        pts = [(tx(x), ty(y)) for x, y in ring]
        parts = [f"M {pts[0][0]:.2f} {pts[0][1]:.2f}"]
        for px, py in pts[1:]:
            parts.append(f"L {px:.2f} {py:.2f}")
        parts.append("Z")
        return " ".join(parts)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{vx0:.2f} 0 {vw:.2f} {vh:.2f}" '
        f'width="{width_px}" height="{int(width_px * vh / vw)}" '
        f'font-family="{style.font_family}">',
        f'<rect x="{vx0:.2f}" y="0" width="{vw:.2f}" height="{vh:.2f}" fill="{style.background}"/>',
        '<g id="inventario">',
    ]

    sw = style.stroke_width
    for item in result.items:
        d = ring_to_d(item.exterior)
        for hole in item.holes:
            d += " " + ring_to_d(hole)
        fill_rule = "evenodd" if item.holes else "nonzero"
        lines.append(
            f'<path d="{d}" fill="{style.fill}" fill-opacity="{style.fill_opacity}" '
            f'stroke="{style.stroke}" stroke-width="{sw}" fill-rule="{fill_rule}" '
            f'data-rank="{item.rank}" data-area="{item.area:.1f}"/>'
        )

    lines.append("</g>")

    if style.show_stats and municipio:
        p = result.params
        fs = max(2.5, vw * 0.012)
        ty_stats = vh - pad * 0.4
        lines += [
            f'<text x="{vx0 + pad:.2f}" y="{ty_stats:.2f}" '
            f'font-size="{fs:.2f}" fill="{style.highlight}" font-weight="600">'
            f'{municipio.upper()}</text>',
            f'<text x="{vx0 + pad:.2f}" y="{ty_stats + fs * 1.4:.2f}" '
            f'font-size="{fs * 0.75:.2f}" fill="{style.stroke}">'
            f'n={result.n}  S={result.total_area:,.0f}m²  '
            f'ø={result.mean_area:,.0f}m²  '
            f'orden:{p.sort_mode}</text>',
            f'<text x="{vx0 + pad:.2f}" y="{ty_stats + fs * 2.6:.2f}" '
            f'font-size="{fs * 0.65:.2f}" fill="#999999">'
            f"d’après Armelle Caron — les villes rangées</text>",
        ]

    lines.append("</svg>")
    return "\n".join(lines)


# ── PDF ───────────────────────────────────────────────────────────────────────

@dataclass
class PdfParams:
    page_width_mm: float = 420.0   # A3 landscape width
    page_height_mm: float = 297.0  # A3 landscape height
    margin_mm: float = 15.0
    title: str = ""
    subtitle: str = ""
    stroke_color: tuple = (0.1, 0.1, 0.1)
    fill_color: tuple = (0.91, 0.91, 0.91)
    fill_alpha: float = 0.7
    stroke_width: float = 0.3
    bg_color: tuple = (1.0, 1.0, 1.0)
    accent_color: tuple = (0.8, 0.0, 0.0)
    show_stats: bool = True
    dpi: int = 150


def _poly_to_mpl_path(item: CaronItem) -> Path:
    """Convierte un CaronItem a un matplotlib Path (con huecos evenodd)."""
    verts = []
    codes = []

    def add_ring(ring):
        pts = [(x, y) for x, y in ring]
        if len(pts) < 2:
            return
        verts.append(pts[0])
        codes.append(Path.MOVETO)
        for pt in pts[1:]:
            verts.append(pt)
            codes.append(Path.LINETO)
        verts.append(pts[0])
        codes.append(Path.CLOSEPOLY)

    add_ring(item.exterior)
    for hole in item.holes:
        add_ring(hole)

    return Path(verts, codes)


def export_pdf(
    result: CaronResult,
    params: PdfParams = None,
    municipio: str = "",
    elemento: str = "",
) -> bytes:
    """Exporta el inventario Caron a PDF usando matplotlib."""
    if params is None:
        params = PdfParams()

    pw = params.page_width_mm / 25.4
    ph = params.page_height_mm / 25.4
    mg = params.margin_mm / 25.4

    fig, ax = plt.subplots(figsize=(pw, ph), dpi=params.dpi)
    fig.patch.set_facecolor(params.bg_color)
    ax.set_facecolor(params.bg_color)

    if result.n > 0:
        all_x = [x for item in result.items for x, y in item.exterior]
        all_y = [y for item in result.items for x, y in item.exterior]
        pad = result.params.col_spacing * 2
        ax.set_xlim(min(all_x) - pad, max(all_x) + pad)
        ax.set_ylim(min(all_y) - pad, max(all_y) + pad)

        for item in result.items:
            path = _poly_to_mpl_path(item)
            patch = PathPatch(
                path,
                facecolor=(*params.fill_color, params.fill_alpha),
                edgecolor=params.stroke_color,
                linewidth=params.stroke_width,
            )
            ax.add_patch(patch)

    ax.set_aspect("equal")
    ax.axis("off")

    title = params.title or municipio.upper()
    sub = params.subtitle or elemento.upper()

    if result.n > 0 and params.show_stats:
        p = result.params
        stats_str = (
            f"n={result.n}   S={result.total_area:,.0f} m²   "
            f"Ø={result.mean_area:,.0f} m²   "
            f"orden: {p.sort_mode}"
        )
        fig.text(
            mg / pw, 1 - mg / ph * 0.5,
            title,
            ha="left", va="top",
            fontsize=14, fontweight="bold",
            color=params.accent_color,
        )
        fig.text(
            mg / pw, 1 - mg / ph * 0.5 - 0.04,
            sub + "   " + stats_str,
            ha="left", va="top",
            fontsize=7, color=(0.4, 0.4, 0.4),
        )
        fig.text(
            mg / pw, mg / ph * 0.4,
            "d’après Armelle Caron — les villes rangées",
            ha="left", va="bottom",
            fontsize=6, style="italic", color=(0.6, 0.6, 0.6),
        )

    buf = io.BytesIO()
    fig.savefig(buf, format="pdf", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


# ── Mapa geográfico ────────────────────────────────────────────────────────────

def _nice_scale_m(map_width_m: float) -> float:
    """Devuelve una longitud de barra de escala 'redonda' para un ancho de mapa dado."""
    target = map_width_m / 5
    mag = 10 ** int(math.log10(max(target, 1)))
    for f in (1, 2, 5, 10):
        if mag * f >= target:
            return mag * f
    return mag * 10


@dataclass
class MapStyle:
    stroke_color: tuple = (0.1, 0.1, 0.1)
    fill_color: tuple = (0.8, 0.0, 0.0)
    fill_alpha: float = 0.45
    stroke_width: float = 0.4
    boundary_color: tuple = (0.2, 0.2, 0.2)
    boundary_width: float = 1.5
    bg_color: tuple = (1.0, 1.0, 1.0)
    dpi: int = 150
    figsize: tuple = (14, 10)
    basemap_source: str = "CartoDB.Positron"
    show_boundary: bool = True
    show_scale: bool = True


def export_map_png(
    gdf,           # GeoDataFrame EPSG:25830
    boundary,      # GeoDataFrame EPSG:25830
    style: MapStyle = None,
    municipio: str = "",
    elemento: str = "",
    with_basemap: bool = True,
) -> bytes:
    """PNG del mapa geográfico. Con fondo OSM (contextily) o sin fondo (plano)."""
    import geopandas as gpd

    if style is None:
        style = MapStyle()

    gdf_m = gdf.to_crs("EPSG:3857")
    bnd_m = boundary.to_crs("EPSG:3857")

    fig, ax = plt.subplots(figsize=style.figsize, dpi=style.dpi)
    fig.patch.set_facecolor(style.bg_color)
    ax.set_facecolor(style.bg_color)

    # Fondo OSM primero
    if with_basemap:
        try:
            import contextily as ctx
            sources = {
                "CartoDB.Positron":      ctx.providers.CartoDB.Positron,
                "OpenStreetMap.Mapnik":  ctx.providers.OpenStreetMap.Mapnik,
                "CartoDB.DarkMatter":    ctx.providers.CartoDB.DarkMatter,
            }
            src = sources.get(style.basemap_source, ctx.providers.CartoDB.Positron)
            ctx.add_basemap(ax, source=src, crs="EPSG:3857", zorder=1)
        except Exception as e:
            print(f"[basemap] {e}")

    # Limite municipal
    if style.show_boundary:
        try:
            bnd_m.boundary.plot(
                ax=ax,
                color=style.boundary_color,
                linewidth=style.boundary_width,
                linestyle="--",
                zorder=3,
            )
        except Exception as e:
            print(f"[boundary plot] {e}")

    # Elementos — usar color+alpha por compatibilidad con todas las versiones de geopandas
    gdf_m.plot(
        ax=ax,
        color=style.fill_color,
        alpha=style.fill_alpha,
        edgecolor=style.stroke_color,
        linewidth=style.stroke_width,
        zorder=4,
    )

    ax.set_aspect("equal")
    ax.axis("off")

    # Escala gráfica
    if style.show_scale:
        utm_b = gdf.total_bounds  # EPSG:25830 (metros reales)
        utm_w = utm_b[2] - utm_b[0]
        scale_m = _nice_scale_m(utm_w)
        m3857_b = gdf_m.total_bounds
        m3857_w = m3857_b[2] - m3857_b[0]
        scale_3857 = scale_m * (m3857_w / utm_w) if utm_w > 0 else scale_m
        margin_x = m3857_w * 0.04
        margin_y = (m3857_b[3] - m3857_b[1]) * 0.04
        bx0 = m3857_b[0] + margin_x
        by0 = m3857_b[1] + margin_y
        tick = m3857_w * 0.007
        lbl = f"{int(scale_m)} m" if scale_m < 1000 else f"{scale_m / 1000:.0f} km"
        ax.plot([bx0, bx0 + scale_3857], [by0, by0], color="black", lw=2,
                solid_capstyle="butt", zorder=10)
        ax.plot([bx0, bx0], [by0 - tick, by0 + tick], color="black", lw=1.5, zorder=10)
        ax.plot([bx0 + scale_3857, bx0 + scale_3857], [by0 - tick, by0 + tick],
                color="black", lw=1.5, zorder=10)
        ax.text(bx0 + scale_3857 / 2, by0 + tick * 1.8, lbl, ha="center", va="bottom",
                fontsize=8, fontweight="bold", zorder=10,
                bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.7, edgecolor="none"))

    # Título
    label = f"{municipio.upper()} — {elemento.upper()}   n={len(gdf)}"
    if not with_basemap:
        label += "   (sin fondo)"
    fig.text(0.02, 0.98, label, va="top", ha="left",
             fontsize=11, fontweight="bold", color=(0.8, 0.0, 0.0))
    fig.text(0.02, 0.01, "d'après Armelle Caron — les villes rangées",
             va="bottom", ha="left", fontsize=7, style="italic", color=(0.5, 0.5, 0.5))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=style.dpi,
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def export_map_svg(
    gdf,
    boundary,
    style: MapStyle = None,
    municipio: str = "",
    elemento: str = "",
) -> str:
    """SVG vectorial del mapa geográfico (sin fondo, coordenadas UTM)."""
    if style is None:
        style = MapStyle()

    # Trabajar en UTM para mantener proporción
    from shapely.geometry import MultiPolygon, Polygon as ShPoly

    pad_rel = 0.02  # 2% de margen
    bnd_bounds = boundary.total_bounds   # minx, miny, maxx, maxy
    elem_bounds = gdf.total_bounds
    minx = min(bnd_bounds[0], elem_bounds[0])
    miny = min(bnd_bounds[1], elem_bounds[1])
    maxx = max(bnd_bounds[2], elem_bounds[2])
    maxy = max(bnd_bounds[3], elem_bounds[3])
    W = maxx - minx
    H = maxy - miny
    pad = max(W, H) * pad_rel
    minx -= pad; miny -= pad; maxx += pad; maxy += pad
    W = maxx - minx; H = maxy - miny

    def tx(x): return round(x - minx, 1)
    def ty(y): return round(H - (y - miny), 1)  # Y invertida

    def geom_to_paths(geom) -> list[str]:
        paths = []
        polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
        for poly in polys:
            if poly.is_empty:
                continue
            coords = poly.exterior.coords
            d = f"M {tx(coords[0][0])} {ty(coords[0][1])}"
            for c in list(coords)[1:]:
                d += f" L {tx(c[0])} {ty(c[1])}"
            d += " Z"
            for ring in poly.interiors:
                rc = ring.coords
                d += f" M {tx(rc[0][0])} {ty(rc[0][1])}"
                for c in list(rc)[1:]:
                    d += f" L {tx(c[0])} {ty(c[1])}"
                d += " Z"
            paths.append(d)
        return paths

    sc = f"rgb({int(style.stroke_color[0]*255)},{int(style.stroke_color[1]*255)},{int(style.stroke_color[2]*255)})"
    fc = f"rgba({int(style.fill_color[0]*255)},{int(style.fill_color[1]*255)},{int(style.fill_color[2]*255)},{style.fill_alpha})"
    bc = f"rgb({int(style.boundary_color[0]*255)},{int(style.boundary_color[1]*255)},{int(style.boundary_color[2]*255)})"

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {round(W,1)} {round(H,1)}" '
        f'width="{round(W,0)}" height="{round(H,0)}">',
        f'<rect width="{round(W,1)}" height="{round(H,1)}" '
        f'fill="rgb({int(style.bg_color[0]*255)},{int(style.bg_color[1]*255)},{int(style.bg_color[2]*255)})"/>',
        '<g id="elements">',
    ]

    # Elementos
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        for d in geom_to_paths(geom):
            lines.append(
                f'<path d="{d}" fill="{fc}" stroke="{sc}" '
                f'stroke-width="{style.stroke_width}" fill-rule="evenodd"/>'
            )

    lines.append("</g>")

    # Límite municipal
    if style.show_boundary:
        lines.append('<g id="boundary">')
        for geom in boundary.geometry:
            if geom is None or geom.is_empty:
                continue
            for d in geom_to_paths(geom.boundary if hasattr(geom, "boundary") else geom):
                lines.append(
                    f'<path d="{d}" fill="none" stroke="{bc}" '
                    f'stroke-width="{style.boundary_width * 2}" stroke-dasharray="8 4"/>'
                )
        lines.append("</g>")

    # Escala gráfica
    if style.show_scale:
        scale_m = _nice_scale_m(W)
        lbl = f"{int(scale_m)} m" if scale_m < 1000 else f"{scale_m / 1000:.0f} km"
        tick = H * 0.006
        bx0 = pad * 2
        by0 = H - pad * 2.5  # SVG y crece hacia abajo, near bottom
        lbl_fs = max(H * 0.013, 7)
        lines += [
            '<g id="scale">',
            f'<line x1="{bx0:.1f}" y1="{by0:.1f}" x2="{bx0 + scale_m:.1f}" y2="{by0:.1f}" '
            f'stroke="#000" stroke-width="2" stroke-linecap="butt"/>',
            f'<line x1="{bx0:.1f}" y1="{by0 - tick:.1f}" x2="{bx0:.1f}" y2="{by0 + tick:.1f}" '
            f'stroke="#000" stroke-width="1.5"/>',
            f'<line x1="{bx0 + scale_m:.1f}" y1="{by0 - tick:.1f}" '
            f'x2="{bx0 + scale_m:.1f}" y2="{by0 + tick:.1f}" stroke="#000" stroke-width="1.5"/>',
            f'<text x="{bx0 + scale_m / 2:.1f}" y="{by0 - tick * 1.8:.1f}" '
            f'text-anchor="middle" font-family="Courier New,monospace" '
            f'font-size="{lbl_fs:.1f}" fill="#000">{lbl}</text>',
            "</g>",
        ]

    # Texto
    fs = max(H * 0.018, 8)
    lines += [
        f'<text x="{pad}" y="{pad * 0.8}" font-family="Bebas Neue,Impact,sans-serif" '
        f'font-size="{fs:.1f}" fill="#cc0000" letter-spacing="0.08em">'
        f'{municipio.upper()}</text>',
        f'<text x="{pad}" y="{H - pad * 0.4}" font-family="Courier New,monospace" '
        f'font-size="{fs * 0.6:.1f}" fill="#666">'
        f"{elemento}  ·  n={len(gdf)}  ·  d'après Armelle Caron</text>",
        "</svg>",
    ]
    return "\n".join(lines)


def export_map_dxf(
    gdf,
    boundary,
    style: MapStyle = None,
    municipio: str = "",
    elemento: str = "",
) -> bytes:
    """DXF vectorial del mapa geográfico (coordenadas UTM EPSG:25830).

    Capas:
      ELEMENTOS        — polígonos de edificios/manzanas/parcelas
      LIMITE_MUNICIPAL — contorno del término municipal (si show_boundary)
      ESCALA           — barra de escala gráfica (si show_scale)
      ANOTACIONES      — textos de título e info
    """
    from shapely.geometry import MultiPolygon

    if style is None:
        style = MapStyle()

    doc = ezdxf.new("R2018")
    doc.units = units.M
    msp = doc.modelspace()

    doc.layers.add("ELEMENTOS",        dxfattribs={"color": 7})
    doc.layers.add("LIMITE_MUNICIPAL", dxfattribs={"color": 3})
    doc.layers.add("ESCALA",           dxfattribs={"color": 1})
    doc.layers.add("ANOTACIONES",      dxfattribs={"color": 2})

    def _draw_poly(poly, layer: str):
        if poly is None or poly.is_empty:
            return
        ext = list(poly.exterior.coords)
        if len(ext) >= 2:
            msp.add_lwpolyline(ext, format="xy", close=True, dxfattribs={"layer": layer})
        for ring in poly.interiors:
            rc = list(ring.coords)
            if len(rc) >= 2:
                msp.add_lwpolyline(rc, format="xy", close=True, dxfattribs={"layer": layer})

    def _draw_geom(geom, layer: str):
        if geom is None or geom.is_empty:
            return
        polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
        for p in polys:
            _draw_poly(p, layer)

    # Elementos
    for geom in gdf.geometry:
        _draw_geom(geom, "ELEMENTOS")

    # Límite municipal
    if style.show_boundary:
        for geom in boundary.geometry:
            _draw_geom(geom, "LIMITE_MUNICIPAL")

    bounds = gdf.total_bounds  # minx miny maxx maxy (UTM metros)
    map_w = bounds[2] - bounds[0]
    pad = map_w * 0.04

    # Escala gráfica
    if style.show_scale:
        scale_m = _nice_scale_m(map_w)
        lbl = f"{int(scale_m)} m" if scale_m < 1000 else f"{scale_m / 1000:.0f} km"
        bx0 = bounds[0] + pad
        by0 = bounds[1] + pad
        tick = map_w * 0.006
        txt_h = map_w * 0.010
        msp.add_line((bx0, by0), (bx0 + scale_m, by0), dxfattribs={"layer": "ESCALA"})
        msp.add_line((bx0, by0 - tick), (bx0, by0 + tick), dxfattribs={"layer": "ESCALA"})
        msp.add_line((bx0 + scale_m, by0 - tick), (bx0 + scale_m, by0 + tick),
                     dxfattribs={"layer": "ESCALA"})
        txt = msp.add_text(lbl, dxfattribs={"layer": "ESCALA", "height": txt_h})
        txt.set_placement(
            (bx0 + scale_m / 2, by0 + tick * 2),
            align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
        )

    # Anotaciones
    if municipio:
        title_h = map_w * 0.018
        info_h = map_w * 0.010
        tx0 = bounds[0] + pad
        msp.add_text(
            f"{municipio.upper()} — {elemento}",
            dxfattribs={"layer": "ANOTACIONES", "height": title_h,
                        "insert": (tx0, bounds[3] - pad)},
        )
        msp.add_text(
            f"n={len(gdf)}  ·  d'apres Armelle Caron",
            dxfattribs={"layer": "ANOTACIONES", "height": info_h,
                        "insert": (tx0, bounds[3] - pad - title_h * 1.5)},
        )

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")

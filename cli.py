"""
CLI para generar inventarios Caron sin la webapp.

Uso:
  python cli.py --municipio "Moraleja de Enmedio" --elemento edificios
  python cli.py --municipio "Getafe" --elemento manzanas --formato dxf pdf svg
  python cli.py --help
"""

import argparse
import sys
from pathlib import Path

# Asegurar imports desde el directorio del proyecto
sys.path.insert(0, str(Path(__file__).parent))

from app import downloader as dl
from app import caron
from app.caron import CaronParams
from app import exporter
from app.exporter import SvgStyle, PdfParams


def main():
    p = argparse.ArgumentParser(
        description="Inventario urbano estilo Armelle Caron para municipios espanoles"
    )
    p.add_argument("--municipio", required=True, help="Nombre del municipio (ej: 'Moraleja de Enmedio')")
    p.add_argument("--elemento", choices=["edificios","manzanas","parcelas"], default="edificios")
    p.add_argument("--formato", nargs="+", choices=["dxf","svg","pdf"], default=["dxf"])
    p.add_argument("--output", default="output/", help="Directorio de salida")
    p.add_argument("--sheet-width", type=float, default=400.0)
    p.add_argument("--col-spacing", type=float, default=4.0)
    p.add_argument("--row-spacing", type=float, default=8.0)
    p.add_argument("--sort-mode", choices=["area","perimeter","width","height"], default="area")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--no-rotate", action="store_true")
    p.add_argument("--min-area", type=float, default=0.0)
    p.add_argument("--max-area", type=float, default=0.0)
    p.add_argument("--simplify", type=float, default=0.0)
    args = p.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    # 1. Buscar municipio
    print(f"Buscando '{args.municipio}'...")
    results = dl.buscar_municipio(args.municipio)
    if not results:
        print("Municipio no encontrado.")
        sys.exit(1)
    r = results[0]
    print(f"Encontrado: {r['municipio']} ({r['provincia']}) — OSM {r['osm_type']}/{r['osm_id']}")

    # 2. Descargar limite
    print("Descargando limite municipal...")
    boundary = dl.obtener_limite(r["osm_type"], r["osm_id"])

    # 3. Descargar geometria
    print(f"Descargando {args.elemento}...")
    if args.elemento == "edificios":
        gdf = dl.descargar_edificios(boundary)
    elif args.elemento == "manzanas":
        gdf = dl.descargar_manzanas(boundary)
    else:
        gdf = dl.descargar_parcelas(boundary)
    print(f"  {len(gdf)} elementos descargados")

    if len(gdf) == 0:
        print("No se encontraron elementos.")
        sys.exit(1)

    # 4. Calcular inventario
    print("Calculando inventario Caron...")
    params = CaronParams(
        sort_mode=args.sort_mode,
        sheet_width=args.sheet_width,
        col_spacing=args.col_spacing,
        row_spacing=args.row_spacing,
        scale_factor=args.scale,
        rotate_to_fit=not args.no_rotate,
        min_area=args.min_area,
        max_area=args.max_area if args.max_area > 0 else float("inf"),
        simplify_tolerance=args.simplify,
    )
    result = caron.run(gdf, params)
    print(f"  n={result.n}  S_total={result.total_area:,.1f}m2  S_media={result.mean_area:,.1f}m2")

    municipio = r["municipio"]
    base = out / f"caron_{municipio.replace(' ','_')}_{args.elemento}"

    # 5. Exportar
    if "dxf" in args.formato:
        path = base.with_suffix(".dxf")
        data = exporter.export_dxf(result, municipio, args.elemento)
        path.write_bytes(data)
        print(f"DXF -> {path}")

    if "svg" in args.formato:
        path = base.with_suffix(".svg")
        svg = exporter.export_svg(result, SvgStyle(), municipio, args.elemento)
        path.write_text(svg, encoding="utf-8")
        print(f"SVG -> {path}")

    if "pdf" in args.formato:
        path = base.with_suffix(".pdf")
        data = exporter.export_pdf(result, PdfParams(), municipio, args.elemento)
        path.write_bytes(data)
        print(f"PDF -> {path}")

    print("Listo.")


if __name__ == "__main__":
    main()

# CARON — inventario urbano. Las ruedas de geopandas/pyogrio/pyproj traen GDAL y PROJ,
# así que basta la imagen slim, sin compilar nada.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt \
 && useradd --create-home --uid 1000 caron

COPY app ./app
COPY static ./static
COPY cli.py README.md ./

USER caron
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/tsuchi/info')"
# Un solo proceso: los trabajos viven en memoria (_jobs), no se pueden repartir entre workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

@echo off
echo Iniciando CARON inventario urbano...
echo Abre http://localhost:8000 en el navegador
echo.
C:\Users\josev\anaconda3\python.exe -m uvicorn app.main:app --reload --reload-dir app --port 8000

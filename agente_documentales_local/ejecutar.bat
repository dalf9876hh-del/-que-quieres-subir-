@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat (call .venv\Scripts\activate.bat) else (
  echo [AVISO] No existe .venv: ejecute primero instalar.bat. Se usara el Python del sistema.
)
set PYTHONIOENCODING=utf-8
python main.py %*
echo.
echo Codigo de salida: %errorlevel%
pause

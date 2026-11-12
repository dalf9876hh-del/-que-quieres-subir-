@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Instalacion del Agente Documental Local ===
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if "%PY%"=="" (python --version >nul 2>&1 && set PY=python)
if "%PY%"=="" (
  echo [ERROR] No se encontro Python 3.9 o superior. Instalelo desde https://www.python.org/downloads/ marcando "Add python.exe to PATH".
  pause
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" || (
  echo [ERROR] Se necesita Python 3.9 o superior.
  pause
  exit /b 1
)
if not exist .venv (
  echo Creando entorno virtual .venv ...
  %PY% -m venv .venv || (echo [ERROR] No se pudo crear el entorno virtual. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo [ERROR] Fallo la instalacion de dependencias. & pause & exit /b 1)
echo.
echo --- Comprobando FFmpeg ---
where ffmpeg >nul 2>&1 && (ffmpeg -version | findstr /b "ffmpeg") || echo [AVISO] FFmpeg no esta en el PATH. Vea el apartado 5 del README.txt.
echo.
echo --- Comprobando Ollama ---
where ollama >nul 2>&1 && (ollama list) || echo [AVISO] No se encontro el comando ollama. Inicie la aplicacion Ollama.
echo.
echo Instalacion terminada. Edite config.json (ruta_biblioteca) y ejecute ejecutar.bat
pause

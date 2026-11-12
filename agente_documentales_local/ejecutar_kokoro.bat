@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ============================================================================
rem  Ejecuta el agente de documentales narrando con KOKORO (voz ef_dora, lang es).
rem  Modelo y voces EXACTOS (tambien estan en agente\voz_kokoro.py):
rem     C:\Users\USER\Desktop\kokoro_test\kokoro-v1.0.onnx
rem     C:\Users\USER\Desktop\kokoro_test\voices-v1.0.bin
rem  Busca por si solo un Python que tenga kokoro-onnx y soundfile instalados.
rem  Para forzar uno:  set PYTHON_KOKORO=C:\ruta\python.exe   (antes de ejecutar este archivo)
rem ============================================================================
set KOKORO_MODEL=C:\Users\USER\Desktop\kokoro_test\kokoro-v1.0.onnx
set KOKORO_VOICES=C:\Users\USER\Desktop\kokoro_test\voices-v1.0.bin
set KOKORO_VOZ=ef_dora
set KOKORO_SPEED=1.0
set AGENTE_MOTOR_VOZ=kokoro
set PYTHONIOENCODING=utf-8
set "PY="
for %%P in ("%PYTHON_KOKORO%" "%~dp0.venv\Scripts\python.exe" "C:\Users\USER\kokoro-llama\.venv\Scripts\python.exe" "C:\Users\USER\Desktop\kokoro_test\.venv\Scripts\python.exe" "C:\Users\USER\Desktop\kokoro_test\venv\Scripts\python.exe") do (
  if not defined PY if exist %%P (
    %%P -c "import kokoro_onnx, soundfile" >nul 2>&1 && set "PY=%%~P"
  )
)
if not defined PY (
  python -c "import kokoro_onnx, soundfile" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] Ningun Python de esta lista tiene kokoro-onnx y soundfile:
  echo         el venv de este programa, kokoro-llama\.venv, kokoro_test\.venv y el Python del sistema.
  echo         Active SU venv de Kokoro en PowerShell y ejecute:  python main.py --tema "Nombre"
  echo         o fije la ruta:  set PYTHON_KOKORO=C:\ruta\a\su\python.exe
  pause
  exit /b 1
)
echo Usando Python: %PY%
"%PY%" main.py %*
echo.
echo Codigo de salida: %errorlevel%
pause

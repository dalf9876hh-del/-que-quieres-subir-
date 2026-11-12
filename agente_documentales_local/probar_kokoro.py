# -*- coding: utf-8 -*-
"""Prueba mínima de Kokoro con EXACTAMENTE las llamadas indicadas. Si esto suena bien, el agente también funcionará.
Ejecute con el Python que tiene kokoro-onnx instalado:
    C:\\Users\\USER\\kokoro-llama\\.venv\\Scripts\\python.exe probar_kokoro.py
"""
import sys

try:
    from kokoro_onnx import Kokoro
    import soundfile as sf
except ImportError as e:
    sys.exit(f"[ERROR] Este Python no tiene '{e.name}'. Use el Python del venv donde instaló kokoro-onnx y soundfile "
             f"(vea ejecutar_kokoro.bat). Python en uso: {sys.executable}")

RUTA_MODELO = "C:\\Users\\USER\\Desktop\\kokoro_test\\kokoro-v1.0.onnx"
RUTA_VOCES = "C:\\Users\\USER\\Desktop\\kokoro_test\\voices-v1.0.bin"
texto = "Hola. Esta es una prueba de la voz Kokoro en español, que narrará tus documentales."

kokoro = Kokoro(RUTA_MODELO, RUTA_VOCES)
samples, sample_rate = kokoro.create(texto, voice="ef_dora", speed=1.0, lang="es")
sf.write("audio_generado.wav", samples, sample_rate)
print(f"[OK] audio_generado.wav creado ({len(samples) / sample_rate:.1f} s a {sample_rate} Hz) con {sys.executable}")

try:
    import winsound
    winsound.PlaySound("audio_generado.wav", winsound.SND_FILENAME)
except ImportError:
    print("(winsound solo existe en Windows: abra audio_generado.wav manualmente)")

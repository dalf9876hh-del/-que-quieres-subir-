# -*- coding: utf-8 -*-
"""Conversación por turnos: usted escribe, Llama (Ollama) responde y Kokoro lee la respuesta en voz alta.
Termina cuando escribe "salir".

Ajustes (de mayor a menor prioridad): argumentos de la línea de comandos > variables de entorno > config.json >
valores por defecto.
  Variables: OLLAMA_MODEL, KOKORO_VOZ (ef_dora | em_alex | em_santa), KOKORO_SPEED, KOKORO_LANG,
             KOKORO_MODEL (ruta de kokoro-v1.0.onnx), KOKORO_VOICES (ruta de voices-v1.0.bin)
"""
import argparse
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from agente import ollama_cli, voz_kokoro                       # noqa: E402
from agente.utils import cargar_config                           # noqa: E402

SISTEMA = ("Eres un asistente conversacional que habla en español. Responde de forma concisa y natural, en dos o tres "
           "frases, como si hablaras en voz alta. No uses emojis, listas, tablas, markdown ni símbolos extraños. Si no "
           "sabes algo, dilo con honestidad.")
MAX_MENSAJES = 16        # historial que se envía al modelo (además del mensaje de sistema)


def _config():
    try:
        return cargar_config(RAIZ / "config.json")
    except (OSError, ValueError):
        return {}


def parsear(argv=None):
    cfg = _config()
    ap = argparse.ArgumentParser(description="Conversación por voz con Llama (Ollama) y Kokoro.")
    ap.add_argument("--modelo", default=os.environ.get("OLLAMA_MODEL") or cfg.get("modelo_ollama") or "llama3.2:3b",
                    help="modelo de Ollama (por defecto el de config.json)")
    ap.add_argument("--voz", default=os.environ.get("KOKORO_VOZ") or cfg.get("kokoro_voz") or "ef_dora",
                    help="voz de Kokoro: ef_dora, em_alex o em_santa")
    ap.add_argument("--velocidad", default=os.environ.get("KOKORO_SPEED") or cfg.get("kokoro_velocidad") or 1.0,
                    help="velocidad de la voz, de 0.5 a 2.0 (por defecto 1.0)")
    ap.add_argument("--lang", default=os.environ.get("KOKORO_LANG") or "es", help="idioma de Kokoro (por defecto es)")
    ap.add_argument("--elegir", action="store_true", help="elegir voz y velocidad en pantalla antes de empezar")
    ap.add_argument("--sin-voz", action="store_true", help="conversar solo con texto (sin Kokoro)")
    ap.add_argument("--sin-reproducir", action="store_true", help="guardar los .wav pero no reproducirlos")
    ap.add_argument("--carpeta-audio", default="audio_chat", help="carpeta donde se guardan los .wav")
    ap.add_argument("--kokoro-modelo", default=None, help="ruta de kokoro-v1.0.onnx")
    ap.add_argument("--kokoro-voces", default=None, help="ruta de voices-v1.0.bin")
    return ap.parse_args(argv)


def elegir_interactivo(voz, velocidad):
    print("\nVoces disponibles en español:")
    for i, v in enumerate(voz_kokoro.VOCES_ES, 1):
        print(f"  {i}) {v}{'  (actual)' if v == voz else ''}")
    r = input(f"Elija una voz [Enter = {voz}]: ").strip()
    if r.isdigit() and 1 <= int(r) <= len(voz_kokoro.VOCES_ES):
        voz = voz_kokoro.VOCES_ES[int(r) - 1]
    elif r in voz_kokoro.VOCES_ES:
        voz = r
    r = input(f"Velocidad de 0.5 a 2.0 [Enter = {velocidad}]: ").strip().replace(",", ".")
    try:
        velocidad = float(r) if r else velocidad
    except ValueError:
        print("Velocidad no válida: se mantiene la anterior.")
    return voz, velocidad


def _limpiar_respuesta(t):
    return re.sub(r"<think>.*?</think>", "", t or "", flags=re.S).strip()


def conversar(args, tts):
    historial = [{"role": "system", "content": SISTEMA}]
    carpeta = Path(args.carpeta_audio)
    n = 0
    while True:
        try:
            texto = input("\nTú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nConversación terminada.")
            return
        if not texto:
            continue
        if texto.lower() == "salir":
            print("Hasta luego.")
            return
        historial.append({"role": "user", "content": texto})
        try:
            enviado = [historial[0]] + historial[1:][-MAX_MENSAJES:]
            respuesta = _limpiar_respuesta(ollama_cli.chat(args.modelo, enviado, options={"temperature": 0.6}))
        except ollama_cli.OllamaCliError as e:
            print(f"\n[ERROR] {e}")
            historial.pop()                        # el turno fallido no queda en el historial
            continue
        if not respuesta:
            print("\n[AVISO] El modelo devolvió una respuesta vacía. Intente reformular la pregunta.")
            historial.pop()
            continue
        historial.append({"role": "assistant", "content": respuesta})
        print(f"\nAsistente: {respuesta}")
        if tts is None:
            continue
        n += 1
        ruta = carpeta / f"respuesta_{n:04d}.wav"
        try:
            tts.guardar(respuesta, ruta)
            print(f"[audio guardado en {ruta}]")
            if not args.sin_reproducir and not voz_kokoro.reproducir_wav(ruta):
                print("[AVISO] No se pudo reproducir el audio automáticamente; ábralo desde la carpeta indicada.")
        except voz_kokoro.KokoroError as e:
            print(f"[AVISO] {e}")
        except Exception as e:  # noqa: BLE001 - un fallo de audio no debe cortar la conversación
            print(f"[AVISO] No se pudo generar o guardar el audio: {e}")


def main(argv=None):
    args = parsear(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    if args.elegir:
        args.voz, args.velocidad = elegir_interactivo(args.voz, args.velocidad)
    try:
        ollama_cli.verificar(args.modelo)
    except ollama_cli.OllamaCliError as e:
        print(f"[ERROR] {e}")
        return 1
    tts = None
    if not args.sin_voz:
        try:
            tts = voz_kokoro.KokoroTTS(args.kokoro_modelo, args.kokoro_voces, args.voz, args.velocidad, args.lang)
        except voz_kokoro.KokoroError as e:
            print(f"[ERROR] {e}\nPuede conversar solo con texto usando la opción --sin-voz.")
            return 2
    print("=" * 60)
    print(f"Modelo: {args.modelo}" + ("" if tts is None else f" | Voz: {tts.voz} | Velocidad: {tts.velocidad}"))
    print('Escriba su mensaje y pulse Enter. Escriba "salir" para terminar.')
    print("=" * 60)
    conversar(args, tts)
    return 0


if __name__ == "__main__":
    sys.exit(main())

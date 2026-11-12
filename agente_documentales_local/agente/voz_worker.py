# -*- coding: utf-8 -*-
"""Proceso auxiliar: sintetiza UN texto con pyttsx3. Se ejecuta aparte para que un cuelgue de SAPI
no bloquee el agente (el proceso principal lo mata al vencer el tiempo máximo).
Uso: python -m agente.voz_worker <info|sintetizar> <velocidad> <voz_preferida> <texto.txt> <salida.wav>"""
import sys

from agente.voz import _RE_ES


def main():
    modo, rate, pref, txt, salida = sys.argv[1:6]
    import pyttsx3
    eng = pyttsx3.init()
    voces = eng.getProperty("voices") or []
    pref = pref.lower()
    elegida = None
    if pref:
        elegida = next((v for v in voces if pref in f"{v.name} {v.id}".lower()), None)
    if elegida is None:
        elegida = next((v for v in voces if _RE_ES.search(f"{v.name} {v.id} {getattr(v, 'languages', '')}")), None)
    if elegida is not None:
        eng.setProperty("voice", elegida.id)
    nombre = elegida.name if elegida is not None else "predeterminada de Windows"
    print(f"VOZ:{nombre}|ES:{1 if elegida is not None else 0}", flush=True)
    if modo == "info":
        return 0
    eng.setProperty("rate", int(rate))
    with open(txt, "r", encoding="utf-8") as f:
        texto = f.read()
    eng.save_to_file(texto, salida)
    eng.runAndWait()
    eng.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

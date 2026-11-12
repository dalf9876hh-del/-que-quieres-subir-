# -*- coding: utf-8 -*-
"""Motor de voz neuronal con edge-tts (servicio en línea de Microsoft Edge, gratuito pero NO oficial: puede
cambiar o dejar de funcionar sin aviso, y necesita Internet). Misma interfaz que MotorVoz (voz.py)."""
import asyncio
import os
import subprocess
import tempfile
import time
from pathlib import Path

from .utils import hash_texto, leer_json
from .voz import VozError


class MotorEdge:
    tipo = "edge-tts"

    def __init__(self, cfg, log, ffmpeg):
        self.cfg, self.log, self.ffmpeg = cfg, log, ffmpeg
        self.voz = cfg.get("edge_voz", "es-MX-JorgeNeural")
        self.voz_es = self.voz.lower().startswith("es-")
        self.advertencias = []
        self._limites = {}
        if not ffmpeg:
            raise VozError("edge-tts produce MP3 y necesita FFmpeg para convertirlo a WAV.")
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            raise VozError("El paquete edge-tts no está instalado (pip install edge-tts).")
        self._sonda()
        self.advertencias.append("Voz edge-tts: servicio en línea no oficial de Microsoft; revise sus condiciones "
                                 "de uso si va a monetizar y tenga un plan B (voz local).")

    def _sonda(self):
        with tempfile.TemporaryDirectory() as d:
            try:
                self._sintetizar_una("Hola.", Path(d) / "sonda.wav", intentos=1, timeout=25)
            except Exception as e:  # noqa: BLE001
                raise VozError(f"edge-tts no responde (¿sin Internet?): {e}")

    # ------------------------------------------------------------------ síntesis
    async def _flujo(self, texto, mp3):
        import edge_tts
        kw = dict(rate=self.cfg.get("edge_rate", "+0%"), pitch=self.cfg.get("edge_pitch", "+0Hz"),
                  volume=self.cfg.get("edge_volumen", "+0%"))
        try:
            com = edge_tts.Communicate(texto, self.voz, boundary="WordBoundary", **kw)
        except TypeError:                       # versiones antiguas no tienen el parámetro boundary
            com = edge_tts.Communicate(texto, self.voz, **kw)
        limites, nbytes = [], 0
        with open(mp3, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                    nbytes += len(ch["data"])
                elif ch["type"] in ("WordBoundary", "SentenceBoundary"):
                    limites.append({"tipo": ch["type"], "t": ch.get("text", ""),
                                    "ini": ch["offset"] / 1e7, "fin": (ch["offset"] + ch["duration"]) / 1e7})
        if nbytes == 0:
            raise VozError("edge-tts no devolvió audio")
        return limites

    def _sintetizar_una(self, texto, destino_wav, intentos=3, timeout=90):
        ultimo = None
        for k in range(1, intentos + 1):
            mp3 = Path(tempfile.gettempdir()) / f"edge_{os.getpid()}_{hash_texto(texto)[:8]}.mp3"
            try:
                limites = asyncio.run(asyncio.wait_for(self._flujo(texto, mp3), timeout))
                r = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3),
                                    "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destino_wav)],
                                   capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    raise VozError(f"FFmpeg no pudo convertir el MP3: {(r.stderr or '')[-200:]}")
                return limites
            except Exception as e:  # noqa: BLE001
                ultimo = e
                self.log.warning(f"edge-tts intento {k}/{intentos}: {e}")
                time.sleep(2 * k)
            finally:
                try:
                    mp3.unlink()
                except OSError:
                    pass
        raise VozError(f"edge-tts falló: {ultimo}")

    def sintetizar(self, texto, destino):
        self._limites[hash_texto(texto)] = self._sintetizar_una(texto, destino)
        time.sleep(float(self.cfg.get("edge_pausa_seg", 0.15)))   # cortesía con el servicio

    def limites_de(self, texto):
        return self._limites.get(hash_texto(texto))

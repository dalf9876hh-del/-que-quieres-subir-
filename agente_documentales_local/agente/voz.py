# -*- coding: utf-8 -*-
"""Narración local y gratuita: pyttsx3 (SAPI5) con alternativa PowerShell/System.Speech.
Cada trozo se sintetiza por separado (reanudable) y luego se unen en un único WAV."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from .utils import escribir_json, hash_texto, leer_json, escribir_texto

PAUSA_SEG = 0.15
RAIZ = Path(__file__).resolve().parent.parent
_RE_ES = re.compile(r"spanish|espa[ñn]ol|_es[a-z]{2}_|\bes[-_][a-z]{2}\b|\(es[-_]", re.I)

PS_SCRIPT = r"""param([string]$TextoRuta,[string]$Salida,[int]$Tasa)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'es*' } | Select-Object -First 1
if ($v) { $s.SelectVoice($v.VoiceInfo.Name); Write-Output ("VOZ:" + $v.VoiceInfo.Name) } else { Write-Output "VOZ:predeterminada" }
$s.Rate = $Tasa
$t = [System.IO.File]::ReadAllText($TextoRuta, [System.Text.Encoding]::UTF8)
$s.SetOutputToWaveFile($Salida)
$s.Speak($t)
$s.Dispose()
"""


class VozError(Exception):
    pass


def _frases_de(parrafo, max_chars):
    frases = []
    for f in re.split(r"(?<=[.!?…])\s+", parrafo.replace("\n", " ").strip()):
        f = f.strip()
        while len(f) > max_chars:  # frase larguísima: cortar por comas/espacios
            corte = max(f.rfind(",", 0, max_chars), f.rfind(";", 0, max_chars))
            if corte < max_chars * 0.4:
                corte = f.rfind(" ", 0, max_chars)
            if corte <= 0:
                corte = max_chars
            frases.append(f[:corte + 1].strip())
            f = f[corte + 1:].strip()
        if f:
            frases.append(f)
    return frases


def _agrupar(frases, max_chars):
    trozos, actual = [], ""
    for f in frases:
        if actual and len(actual) + 1 + len(f) > max_chars:
            trozos.append(actual)
            actual = f
        else:
            actual = (actual + " " + f).strip()
    if actual:
        trozos.append(actual)
    return trozos


def dividir_en_trozos(texto, max_chars=350, por_parrafo=False):
    """Agrupa frases completas en trozos de hasta max_chars caracteres. Con por_parrafo=True ningún trozo cruza
    un salto de párrafo (así cada escena del video empieza donde empieza una idea)."""
    parrafos = [p for p in re.split(r"\n\s*\n", texto.strip()) if p.strip()]
    if por_parrafo:
        res = []
        for p in parrafos:
            res += _agrupar(_frases_de(p, max_chars), max_chars)
        return res
    return _agrupar([f for p in parrafos for f in _frases_de(p, max_chars)], max_chars)


def _dir_ascii():
    """Carpeta temporal solo ASCII (SAPI puede fallar con rutas con acentos)."""
    candidatos = [tempfile.gettempdir(), os.environ.get("PUBLIC", ""), os.environ.get("ProgramData", ""), "C:\\"]
    for c in candidatos:
        if c and c.isascii() and os.path.isdir(c):
            d = Path(c) / "agente_doc_tts"
            d.mkdir(parents=True, exist_ok=True)
            return d
    d = Path(tempfile.mkdtemp(prefix="tts_"))
    return d


def wav_valido(ruta):
    try:
        with wave.open(str(ruta), "rb") as w:
            return w.getnframes() > 0
    except (wave.Error, EOFError, OSError):
        return False


def duracion_wav(ruta):
    with wave.open(str(ruta), "rb") as w:
        return w.getnframes() / float(w.getframerate())


class MotorVoz:
    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log
        self.tipo = None
        self.voz = None
        self.voz_es = None
        self.advertencias = []
        if os.environ.get("AGENTE_TEST_TTS"):
            self.tipo, self.voz, self.voz_es = "prueba", "SILENCIO (solo pruebas)", True
            self.advertencias.append("MOTOR DE PRUEBA: el audio es silencio (AGENTE_TEST_TTS activo).")
            return
        self.timeout = int(cfg.get("voz_timeout_seg", 120))
        try:
            salida = self._worker("info", "", None, timeout=60)
            self._leer_voz(salida)
            self.tipo = "pyttsx3"
        except Exception as e:  # noqa: BLE001 - cualquier fallo del motor debe registrarse
            log.warning(f"pyttsx3 no disponible o colgado ({e}). Se intentará PowerShell/System.Speech.")
            self.advertencias.append(f"pyttsx3 no funcionó: {e}")
            if os.name == "nt" and shutil.which("powershell"):
                self.tipo = "powershell"
            else:
                raise VozError("No hay motor de voz local disponible (pyttsx3 falló y PowerShell no existe).")

    def _worker(self, modo, texto, destino, timeout):
        """Ejecuta pyttsx3 en un proceso aparte con tiempo máximo (SAPI a veces se cuelga)."""
        d = _dir_ascii()
        txt = d / f"w_{os.getpid()}.txt"
        with open(txt, "w", encoding="utf-8") as f:
            f.write(texto)
        cmd = [sys.executable, "-m", "agente.voz_worker", modo, str(int(self.cfg["velocidad_voz"])),
               self.cfg.get("voz_preferida", ""), str(txt), str(destino or "")]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=str(RAIZ))
        except subprocess.TimeoutExpired:
            raise VozError(f"pyttsx3 no respondió en {timeout} s (proceso terminado)")
        finally:
            try:
                txt.unlink()
            except OSError:
                pass
        if r.returncode != 0:
            raise VozError(f"pyttsx3 terminó con código {r.returncode}: {(r.stderr or '').strip()[-300:]}")
        return r.stdout or ""

    def _leer_voz(self, salida):
        m = re.search(r"VOZ:(.+?)\|ES:([01])", salida)
        if not m:
            raise VozError("pyttsx3 no informó la voz elegida")
        self.voz, self.voz_es = m.group(1).strip(), m.group(2) == "1"
        if not self.voz_es and not any("voz en español" in a for a in self.advertencias):
            self.advertencias.append("No hay voz en español instalada; se usa la voz predeterminada. "
                                     "Instale una voz española en Configuración > Hora e idioma > Voz.")

    def sintetizar(self, texto, destino):
        """Escribe un WAV en destino. Reintenta con el motor alternativo si falla."""
        tmpdir = _dir_ascii()
        tmp = tmpdir / f"t_{os.getpid()}_{hash_texto(texto)[:10]}.wav"
        if tmp.exists():
            tmp.unlink()
        metodos = [self.tipo] + (["powershell"] if self.tipo == "pyttsx3" and os.name == "nt" else [])
        ultimo = None
        for m in metodos:
            try:
                if m == "prueba":
                    self._silencio(texto, tmp)
                elif m == "pyttsx3":
                    self._leer_voz(self._worker("sintetizar", texto, tmp, self.timeout))
                else:
                    self._powershell(texto, tmp)
                if not wav_valido(tmp):
                    raise VozError("el WAV generado está vacío o es inválido")
                shutil.move(str(tmp), str(destino))
                return
            except Exception as e:  # noqa: BLE001
                ultimo = e
                self.log.warning(f"Motor de voz '{m}' falló: {e}")
                if m == "pyttsx3":
                    self.advertencias.append(f"pyttsx3 falló durante la síntesis ({e}); se usó PowerShell.")
                    self.tipo = "powershell"
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        raise VozError(f"No se pudo sintetizar el trozo: {ultimo}")

    @staticmethod
    def _silencio(texto, ruta):
        n = max(1, int(max(0.5, len(texto.split()) * 0.38) * 22050))
        with wave.open(str(ruta), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b"\x00\x00" * n)

    def _powershell(self, texto, ruta):
        d = _dir_ascii()
        ps1, txt = d / "voz.ps1", d / f"txt_{os.getpid()}.txt"
        with open(ps1, "w", encoding="utf-8-sig") as f:
            f.write(PS_SCRIPT)
        with open(txt, "w", encoding="utf-8") as f:
            f.write(texto)
        tasa = max(-10, min(10, round((int(self.cfg["velocidad_voz"]) - 170) / 10)))
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                            "-TextoRuta", str(txt), "-Salida", str(ruta), "-Tasa", str(tasa)],
                           capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise VozError(f"PowerShell devolvió {r.returncode}: {r.stderr.strip()[:300]}")
        m = re.search(r"VOZ:(.+)", r.stdout or "")
        if m:
            self.voz = m.group(1).strip()
            self.voz_es = self.voz != "predeterminada"
            if not self.voz_es and not any("voz en español" in a for a in self.advertencias):
                self.advertencias.append("No hay voz en español instalada; se usa la voz predeterminada.")


def _unir(wavs, pausa, destino):
    """Une WAVs con una pausa breve. Devuelve la duración de cada trozo (pausa incluida)."""
    duraciones = []
    with wave.open(str(wavs[0]), "rb") as w0:
        params = w0.getparams()
    tmp = Path(str(destino) + ".tmp")
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        for i, p in enumerate(wavs):
            with wave.open(str(p), "rb") as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != \
                        (params.nchannels, params.sampwidth, params.framerate):
                    raise VozError(f"Formato de WAV inconsistente en {p.name}")
                frames = w.readframes(w.getnframes())
                n = w.getnframes()
            out.writeframes(frames)
            if i < len(wavs) - 1:
                sil = int(pausa * params.framerate)
                out.writeframes(b"\x00" * sil * params.nchannels * params.sampwidth)
                n += sil
            duraciones.append(n / float(params.framerate))
    os.replace(tmp, destino)
    return duraciones


def sintetizar_texto(motor, texto, destino, log, etiqueta, max_chars=350, por_parrafo=False):
    """Genera destino (WAV) y destino.meta.json con los trozos (escenas) y sus duraciones. Reanudable."""
    destino = Path(destino)
    meta_ruta = destino.with_suffix(".meta.json")
    h = hash_texto(f"{max_chars}|{por_parrafo}|{motor.tipo}|{motor.voz}|{texto}")
    meta = leer_json(meta_ruta)
    if meta and meta.get("hash") == h and destino.exists() and wav_valido(destino):
        log.info(f"Narración '{etiqueta}' ya generada y vigente: se reutiliza.")
        return meta
    trozos = dividir_en_trozos(texto, max_chars, por_parrafo)
    if not trozos:
        raise VozError("No hay texto para narrar.")
    tmpdir = destino.parent / f"_tts_{destino.stem}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    wavs, limites = [], []
    for i, t in enumerate(trozos, 1):
        w = tmpdir / f"trozo_{i:04d}_{hash_texto(motor.tipo + '|' + str(motor.voz) + '|' + t)[:10]}.wav"   # incluye la voz
        jl = w.with_suffix(".json")
        if not (w.exists() and wav_valido(w)):
            motor.sintetizar(t, w)
            lim = getattr(motor, "limites_de", lambda _t: None)(t)
            if lim:
                escribir_json(jl, lim)
        wavs.append(w)
        limites.append(leer_json(jl) if jl.exists() else None)
        if i % 5 == 0 or i == len(trozos):
            log.info(f"Narración '{etiqueta}': {i}/{len(trozos)} trozos.")
    duraciones = _unir(wavs, PAUSA_SEG, destino)
    if not wav_valido(destino):
        raise VozError(f"El WAV final de '{etiqueta}' no es válido.")
    meta = {"hash": h, "duracion": duracion_wav(destino), "motor": motor.tipo, "voz": motor.voz,
            "voz_espanola": motor.voz_es,
            "trozos": [{"texto": t, "duracion": d, "limites": lim}
                       for t, d, lim in zip(trozos, duraciones, limites)]}
    escribir_json(meta_ruta, meta)
    shutil.rmtree(tmpdir, ignore_errors=True)
    return meta

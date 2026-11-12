# -*- coding: utf-8 -*-
"""Voz local con Kokoro-82M (paquete kokoro-onnx). Sin Internet: usa los archivos kokoro-v1.0.onnx y
voices-v1.0.bin que ya tiene en su PC. Lo usan chat_voz.py, ollama_pptx.py y el agente de documentales."""
import os
import re
import tempfile
from pathlib import Path

from .texto import _RE_EMOJI
from .voz import VozError, dividir_en_trozos

NOMBRE_MODELO = "kokoro-v1.0.onnx"
NOMBRE_VOCES = "voices-v1.0.bin"
VOCES_ES = ("ef_dora", "em_alex", "em_santa")        # voces en español de Kokoro v1.0

# --- Valores EXACTOS indicados por el usuario (se usan siempre que config.json / variables no digan otra cosa) ---
RUTA_MODELO = "C:\\Users\\USER\\Desktop\\kokoro_test\\kokoro-v1.0.onnx"
RUTA_VOCES = "C:\\Users\\USER\\Desktop\\kokoro_test\\voices-v1.0.bin"
VOZ = "ef_dora"            # voz femenina en español
LANG = "es"                # idioma obligatorio para español
VELOCIDAD = 1.0
RAIZ = Path(__file__).resolve().parent.parent


class KokoroError(Exception):
    """Error con mensaje ya redactado en español, listo para mostrar al usuario."""


# ----------------------------------------------------------------------------- archivos
def _buscar(valor, nombre):
    """Devuelve (ruta o None, lista de rutas probadas)."""
    if valor:
        p = Path(valor).expanduser()
        if p.is_dir():
            p = p / nombre
        return (p if p.is_file() else None), [p]
    candidatos = [Path.cwd() / nombre, RAIZ / nombre, RAIZ / "kokoro" / nombre, RAIZ / "assets" / "kokoro" / nombre]
    for c in candidatos:
        if c.is_file():
            return c, candidatos
    return None, candidatos


def _resolver(explicito, nombre, defecto):
    """Ruta indicada (argumento, config o variable) > ruta exacta por defecto > búsqueda en las carpetas habituales."""
    if explicito:
        return _buscar(explicito, nombre)
    p, probadas = _buscar(defecto, nombre)
    if p:
        return p, probadas
    p2, probadas2 = _buscar(None, nombre)
    return p2, probadas + probadas2


def localizar_archivos(modelo=None, voces=None):
    """Ruta de kokoro-v1.0.onnx y voices-v1.0.bin. Orden: argumento > variables KOKORO_MODEL / KOKORO_VOICES >
    carpeta actual > carpeta del programa > kokoro\\ > assets\\kokoro\\."""
    m, probadas_m = _resolver(modelo or os.environ.get("KOKORO_MODEL"), NOMBRE_MODELO, RUTA_MODELO)
    v, probadas_v = _resolver(voces or os.environ.get("KOKORO_VOICES"), NOMBRE_VOCES, RUTA_VOCES)
    faltan = []
    if m is None:
        faltan.append((NOMBRE_MODELO, probadas_m))
    if v is None:
        faltan.append((NOMBRE_VOCES, probadas_v))
    if faltan:
        lineas = ["No se encontraron los archivos de Kokoro:"]
        for nombre, rutas in faltan:
            lineas.append(f"  - {nombre}  (buscado en: {'; '.join(str(r) for r in rutas[:4])})")
        lineas.append("Coloque ambos archivos en la carpeta del programa (o en kokoro\\) o indique su ubicación con las "
                      "variables de entorno KOKORO_MODEL y KOKORO_VOICES, por ejemplo en PowerShell:")
        lineas.append('  $env:KOKORO_MODEL  = "C:\\ruta\\kokoro-v1.0.onnx"')
        lineas.append('  $env:KOKORO_VOICES = "C:\\ruta\\voices-v1.0.bin"')
        raise KokoroError("\n".join(lineas))
    return m, v


# ----------------------------------------------------------------------------- texto
_RE_URL = re.compile(r"https?://\S+|www\.\S+")


def limpiar_para_voz(texto):
    """Quita emojis, markdown, enlaces y símbolos que Kokoro leería mal."""
    t = _RE_URL.sub("", texto or "")
    t = _RE_EMOJI.sub("", t)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"^\s*(#{1,6}|[-*•]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"[*_`~#>|]+", "", t)
    t = re.sub(r"\s*\n\s*", ". ", t)
    t = re.sub(r"\.\s*\.", ".", t)
    return re.sub(r"\s+", " ", t).strip()


# ----------------------------------------------------------------------------- motor
class KokoroTTS:
    def __init__(self, modelo=None, voces=None, voz=VOZ, velocidad=VELOCIDAD, lang=LANG):
        ruta_m, ruta_v = localizar_archivos(modelo, voces)
        try:
            from kokoro_onnx import Kokoro                 # exacto, como se pidió
            import numpy  # noqa: F401
            import soundfile as sf  # noqa: F401  (exacto, como se pidió)
        except ImportError as e:
            raise KokoroError(f"Falta un paquete de Python para Kokoro ({e.name or e}). Instálelo en el entorno virtual: "
                              "pip install kokoro-onnx soundfile")
        try:
            self.kokoro = Kokoro(str(ruta_m), str(ruta_v))   # = Kokoro("...\\kokoro-v1.0.onnx", "...\\voices-v1.0.bin")
        except Exception as e:  # noqa: BLE001
            raise KokoroError(f"No se pudo cargar Kokoro con {ruta_m.name} y {ruta_v.name}: {e}")
        try:
            disponibles = list(self.kokoro.get_voices())
        except Exception:  # noqa: BLE001 - versiones antiguas sin get_voices
            disponibles = list(VOCES_ES)
        if voz not in disponibles:
            es = [v for v in disponibles if v in VOCES_ES] or list(VOCES_ES)
            raise KokoroError(f"La voz '{voz}' no existe en voices-v1.0.bin. Voces en español: {', '.join(es)}.")
        self.voz, self.lang = voz, lang
        try:
            self.velocidad = float(velocidad)
        except (TypeError, ValueError):
            raise KokoroError(f"La velocidad '{velocidad}' no es un número (use por ejemplo 1.0).")
        self.velocidad = min(2.0, max(0.5, self.velocidad))
        self.modelo_ruta, self.voces_ruta = ruta_m, ruta_v

    def audio(self, texto):
        """(muestras float32, frecuencia). Divide el texto en trozos de frases para no pasar el límite de Kokoro."""
        import numpy as np
        trozos = dividir_en_trozos(limpiar_para_voz(texto), 300)
        if not trozos:
            raise KokoroError("No hay texto para convertir en voz.")
        partes, sr = [], 24000
        for t in trozos:
            try:
                # samples, sample_rate = kokoro.create(texto, voice="ef_dora", speed=1.0, lang="es")
                muestras, sr = self.kokoro.create(t, voice=self.voz, speed=self.velocidad, lang=self.lang)
            except Exception as e:  # noqa: BLE001
                raise KokoroError(f"Kokoro falló al generar la voz: {e}")
            partes.append(np.asarray(muestras, dtype="float32"))
            partes.append(np.zeros(int(0.12 * sr), dtype="float32"))     # pausa breve entre trozos
        return np.concatenate(partes[:-1]), sr

    def guardar(self, texto, ruta):
        """Genera el audio y lo guarda como .wav con soundfile. Devuelve la ruta."""
        import soundfile as sf
        muestras, sr = self.audio(texto)
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(ruta), muestras, sr)                 # = sf.write("audio_generado.wav", samples, sample_rate)
        return ruta

    sintetizar = guardar        # misma interfaz que los demás motores del agente de documentales


def reproducir_wav(ruta):
    """Reproduce el .wav y espera a que termine. winsound (incluido en Windows); si no, sounddevice si existe.
    Devuelve True si sonó, False si no se pudo (sin lanzar excepciones)."""
    try:
        import winsound
        winsound.PlaySound(str(ruta), winsound.SND_FILENAME)
        return True
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return False
    try:
        import sounddevice as sd
        import soundfile as sf
        datos, sr = sf.read(str(ruta))
        sd.play(datos, sr)
        sd.wait()
        return True
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------------- integración con el agente de documentales
class MotorKokoro:
    """Mismo contrato que MotorVoz/MotorEdge: .tipo, .voz, .voz_es, .advertencias, .sintetizar(texto, destino)."""
    tipo = "kokoro"

    def __init__(self, cfg, log):
        self.log, self.advertencias = log, []
        try:
            self._tts = KokoroTTS(cfg.get("kokoro_modelo") or None, cfg.get("kokoro_voces") or None,
                                  os.environ.get("KOKORO_VOZ") or cfg.get("kokoro_voz") or VOZ,
                                  os.environ.get("KOKORO_SPEED") or cfg.get("kokoro_velocidad") or VELOCIDAD,
                                  cfg.get("kokoro_lang") or LANG)
        except KokoroError as e:
            raise VozError(str(e))
        self.voz, self.voz_es = self._tts.voz, self._tts.voz.startswith(("ef_", "em_"))
        self._tts.guardar("Hola.", Path(tempfile.gettempdir()) / "kokoro_sonda.wav")   # comprobación inicial

    def sintetizar(self, texto, destino):
        try:
            self._tts.guardar(texto, destino)
        except KokoroError as e:
            raise VozError(str(e))

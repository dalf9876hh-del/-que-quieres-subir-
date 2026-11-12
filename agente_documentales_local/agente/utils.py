# -*- coding: utf-8 -*-
"""Utilidades comunes: configuración, escritura segura, recuento de palabras, nombres seguros."""
import copy
import hashlib
import json
import logging
import os
import re
import struct
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

EXT_IMAGEN = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
EXT_VIDEO = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

CONFIG_DEFECTO = {
    "ruta_biblioteca": "",
    "carpeta_salida": "_documentales_generados",
    "ollama_url": "http://localhost:11434",
    "modelo_ollama": "llama3.2:3b",
    "modelo_respaldo": "llama3.2:3b",   # se usa solo si el modelo principal devuelve razonamiento en vez de texto
    "ollama_timeout_seg": 900,
    "ollama_num_ctx": 4096,
    "palabras_objetivo": 1450,
    "duracion_objetivo_min": 10,         # el documental largo NO pasará de estos minutos (0 = usar palabras_objetivo)
    "palabras_por_minuto": 150,          # ritmo de la voz; se corrige solo con la duración real medida
    "palabras_minimas": 1000,
    "margen_seguridad": 60,
    "max_ampliaciones": 6,
    "reintentos_seccion": 3,
    "palabras_short_min": 90,
    "palabras_short_max": 130,
    "max_imagenes": 40,
    "max_videos": 12,
    "duracion_escena_seg": 6.0,
    "duracion_escena_short_seg": 3.5,
    "max_duracion_clip_seg": 12.0,
    "resolucion_horizontal": [1280, 720],
    "resolucion_vertical": [720, 1280],
    "fps": 24,
    "velocidad_voz": 165,
    "voz_preferida": "",
    "investigacion_web": True,
    "investigacion_timeout_seg": 15,
    "fondo_desenfocado": True,
    "quemar_subtitulos": False,
    "carpeta_lista_videos": "_lista_videos",
    "lista_enlazar_videos": True,
    "descripcion_aviso": "Narración con voz sintética (IA). Guion y estructura elaborados con ayuda de inteligencia "
                         "artificial a partir de fuentes públicas; puede contener imprecisiones.",
    "modo_video": "escenas",            # "escenas" (zooms, SFX, música, subtítulos ASS) o "clasico"
    "motor_voz": "auto",                # auto: Kokoro (si están sus archivos) -> edge-tts -> voz local | kokoro | edge | local
    "kokoro_modelo": "",                # ruta de kokoro-v1.0.onnx (vacío = buscar o usar KOKORO_MODEL)
    "kokoro_voces": "",                 # ruta de voices-v1.0.bin   (vacío = buscar o usar KOKORO_VOICES)
    "kokoro_voz": "ef_dora",            # ef_dora | em_alex | em_santa
    "kokoro_velocidad": 1.0,            # 0.5 a 2.0
    "kokoro_lang": "es",
    "edge_voz": "es-MX-JorgeNeural",    # también: es-ES-AlvaroNeural, es-AR-TomasNeural...
    "edge_rate": "+0%",
    "edge_pitch": "+0Hz",
    "escena_max_caracteres": 170,       # tamaño de cada escena del documental (aprox. 10 s de voz)
    "escena_max_caracteres_short": 110, # escenas más cortas en el Short (cambio visual más rápido)
    "escenas_con_llm": False,           # True: el modelo propone VISUAL_PROMPT y SFX por escena (más lento)
    "zoom_intensidad": 0.30,
    "paneo_factor": 0.55,
    "transicion": "xfade",              # corte | fundido (dip a negro, rápido) | xfade (fundido cruzado real)
    "transicion_seg": 0.45,
    "xfade_bloque": 8,
    "assets_carpeta": "assets",         # assets/musica (tus pistas libres de derechos) y assets/sfx (whoosh.mp3, pop.mp3)
    "musica_volumen": 0.12,
    "sfx_activar": True,
    "sfx_ganancia": 1.0,
    "sfx_volumen": 1.0,
    "normalizar_audio": True,
    "subtitulos_estilo_short": "palabra",
    "subtitulos_estilo_largo": "bloques",
    "quemar_subtitulos_short": True,
    "quemar_subtitulos_largo": True,    # subtítulos incrustados también en el documental largo
    "whisper": "short",                 # no | short | todo (necesita pip install faster-whisper)
    "whisper_modelo": "base",
    "filtrar_imagenes_basura": True,
    "min_lado_render_px": 480,
    "estructura_variable": True,
    "comfyui": {"activar": False, "url": "http://127.0.0.1:8188", "workflow": "assets/comfy/workflow_api.json",
                "nodo_texto": "", "campo_texto": "text", "max_por_tema": 6, "timeout_imagen_seg": 1200},
    "ruta_ffmpeg": "",
    "ruta_ffprobe": "",
    "ffmpeg": {"preset": "veryfast", "crf": 26, "audio_bitrate": "128k", "threads": 2},
}


def ahora():
    return datetime.now().isoformat(timespec="seconds")


def cargar_config(ruta):
    with open(ruta, "r", encoding="utf-8-sig") as f:
        datos = json.load(f)
    cfg = copy.deepcopy(CONFIG_DEFECTO)
    for k, v in datos.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def validar_config(cfg):
    errores = []
    for clave in ("resolucion_horizontal", "resolucion_vertical"):
        r = cfg.get(clave)
        if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) and x > 0 for x in r)):
            errores.append(f"'{clave}' debe ser una lista [ancho, alto] con enteros positivos.")
    for clave in ("palabras_objetivo", "palabras_minimas", "fps", "velocidad_voz"):
        if not isinstance(cfg.get(clave), (int, float)) or cfg[clave] <= 0:
            errores.append(f"'{clave}' debe ser un número positivo.")
    if isinstance(cfg.get("palabras_objetivo"), (int, float)) and isinstance(cfg.get("palabras_minimas"), (int, float)):
        if cfg["palabras_objetivo"] <= cfg["palabras_minimas"]:
            errores.append("'palabras_objetivo' debe ser mayor que 'palabras_minimas'.")
    if not str(cfg.get("modelo_ollama", "")).strip():
        errores.append("'modelo_ollama' no puede estar vacío.")
    return errores


def escribir_texto(ruta, texto):
    """Escritura segura: archivo temporal + reemplazo atómico."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def escribir_json(ruta, datos):
    escribir_texto(ruta, json.dumps(datos, ensure_ascii=False, indent=2))


def leer_texto(ruta):
    try:
        with open(ruta, "r", encoding="utf-8-sig") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def leer_json(ruta, defecto=None):
    try:
        with open(ruta, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


_RE_PALABRA = re.compile(r"\d+(?:[.,]\d+)+|\w+(?:[-'’]\w+)*", re.UNICODE)


def contar_palabras(texto):
    """Recuento real de palabras (letras con acentos, ñ, números y palabras con guion)."""
    return len(_RE_PALABRA.findall(texto or ""))


def nombre_seguro(nombre):
    n = unicodedata.normalize("NFKD", nombre)
    n = "".join(c for c in n if not unicodedata.combining(c)).lower()
    n = re.sub(r"[^a-z0-9]+", "_", n).strip("_")[:60].strip("_")
    return n or "tema"


def hash_texto(texto):
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()


def tam_archivo(ruta):
    try:
        return Path(ruta).stat().st_size
    except OSError:
        return 0


def configurar_log(ruta=None):
    log = logging.getLogger("agente")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if ruta:
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(ruta, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


def _dims_tiff(f, c):
    e = "<" if c[:2] == b"II" else ">"
    f.seek(struct.unpack(e + "I", c[4:8])[0])
    n = struct.unpack(e + "H", f.read(2))[0]
    entradas = f.read(12 * min(n, 200))
    ancho = alto = None
    for i in range(len(entradas) // 12):
        tag, tipo, _ = struct.unpack(e + "HHI", entradas[i * 12:i * 12 + 8])
        val = entradas[i * 12 + 8:i * 12 + 12]
        v = struct.unpack(e + "H", val[:2])[0] if tipo == 3 else struct.unpack(e + "I", val)[0]
        if tag == 256:
            ancho = v
        elif tag == 257:
            alto = v
    return (ancho, alto) if ancho and alto else None


def _dims_isobmff(c):
    """AVIF/HEIC: mayor área entre las cajas 'ispe' de la cabecera."""
    mejor, i = None, c.find(b"ispe")
    while i != -1:
        try:
            a, h = struct.unpack(">II", c[i + 8:i + 16])
            if a and h and (mejor is None or a * h > mejor[0] * mejor[1]):
                mejor = (a, h)
        except struct.error:
            pass
        i = c.find(b"ispe", i + 4)
    return mejor


def _dims_svg(c):
    t = c[:4096].decode("utf-8", "ignore")
    m = re.search(r"<svg\b[^>]*>", t, re.I | re.S)
    if not m:
        return None
    etiqueta = m.group(0)

    def num(nombre):
        r = re.search(r"\b" + nombre + r"\s*=\s*[\"']\s*([0-9.]+)\s*(px)?\s*[\"']", etiqueta, re.I)
        return float(r.group(1)) if r else None
    ancho, alto = num("width"), num("height")
    if not (ancho and alto):
        vb = re.search(r"viewBox\s*=\s*[\"']\s*[-0-9.]+[ ,]+[-0-9.]+[ ,]+([0-9.]+)[ ,]+([0-9.]+)", etiqueta, re.I)
        if vb:
            ancho, alto = float(vb.group(1)), float(vb.group(2))
    return (int(ancho), int(alto)) if ancho and alto else None


def dimensiones_imagen(ruta):
    """(ancho, alto) leyendo la cabecera de PNG, JPEG, GIF, BMP, WEBP, TIFF, AVIF/HEIC o SVG;
    None si no se puede saber."""
    try:
        with open(ruta, "rb") as f:
            c = f.read(65536)
            if c[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", c[16:24])
            if c[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", c[6:10])
            if c[:2] == b"BM":
                a, h = struct.unpack("<ii", c[18:26])
                return a, abs(h)
            if c[:4] == b"RIFF" and c[8:12] == b"WEBP":
                tipo = c[12:16]
                if tipo == b"VP8X":
                    return (1 + int.from_bytes(c[24:27], "little"),
                            1 + int.from_bytes(c[27:30], "little"))
                if tipo == b"VP8 " and c[23:26] == b"\x9d\x01\x2a":
                    return (struct.unpack("<H", c[26:28])[0] & 0x3FFF,
                            struct.unpack("<H", c[28:30])[0] & 0x3FFF)
                if tipo == b"VP8L" and c[20:21] == b"\x2f":
                    bits = int.from_bytes(c[21:25], "little")
                    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
                return None
            if c[:4] in (b"II*\x00", b"MM\x00*"):
                return _dims_tiff(f, c)
            if c[4:8] == b"ftyp" and c[8:12] in (b"avif", b"avis", b"heic", b"heix", b"mif1", b"msf1"):
                return _dims_isobmff(c)
            if b"<svg" in c[:4096].lower():
                return _dims_svg(c)
            if c[:2] == b"\xff\xd8":
                f.seek(2)
                for _ in range(2000):
                    b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        return None
                    m = b[0]
                    if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                        continue
                    largo = struct.unpack(">H", f.read(2))[0]
                    if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                        _, alto, ancho = struct.unpack(">BHH", f.read(5))
                        return ancho, alto
                    f.seek(largo - 2, 1)
    except (OSError, struct.error, ValueError):
        pass
    return None

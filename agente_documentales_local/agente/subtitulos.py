# -*- coding: utf-8 -*-
"""Subtítulos SRT. La sincronización está anclada a la duración REAL de cada trozo de audio
y se reparte dentro de cada trozo en proporción a los caracteres (aproximada a nivel de frase)."""
import re

from .utils import escribir_texto

MAX_LINEA = 42
MAX_FRAGMENTO = 84


def _fmt(ms):
    ms = max(0, int(round(ms)))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, r = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{r:03d}"


def _romper_fragmentos(texto):
    """Divide en fragmentos legibles (<= 84 caracteres) respetando frases y comas."""
    frases = [f.strip() for f in re.split(r"(?<=[.!?…])\s+", texto) if f.strip()]
    frags = []
    for f in frases:
        if len(f) <= MAX_FRAGMENTO:
            frags.append(f)
            continue
        actual = ""
        for palabra in f.split():
            if actual and len(actual) + 1 + len(palabra) > MAX_FRAGMENTO:
                frags.append(actual)
                actual = palabra
            else:
                actual = (actual + " " + palabra).strip()
            if actual.endswith((",", ";", ":")) and len(actual) > MAX_FRAGMENTO * 0.6:
                frags.append(actual)
                actual = ""
        if actual:
            frags.append(actual)
    return frags


def _dos_lineas(frag):
    if len(frag) <= MAX_LINEA:
        return frag
    mitad = len(frag) // 2
    corte = frag.rfind(" ", 0, mitad + 8)
    if corte < mitad - 15:
        corte = frag.find(" ", mitad)
    if corte <= 0:
        return frag
    return frag[:corte].strip() + "\n" + frag[corte:].strip()


def generar_srt(trozos, ruta):
    """trozos: lista de {'texto', 'duracion'} en segundos. Devuelve nº de bloques."""
    bloques = []
    t0 = 0.0
    for tr in trozos:
        dur = float(tr["duracion"])
        frags = _romper_fragmentos(tr["texto"])
        total = sum(len(f) for f in frags) or 1
        ini = t0
        for k, f in enumerate(frags):
            fin = t0 + dur if k == len(frags) - 1 else ini + dur * len(f) / total
            bloques.append((ini * 1000, fin * 1000, _dos_lineas(f)))
            ini = fin
        t0 += dur
    salida = []
    for i, (a, b, txt) in enumerate(bloques, 1):
        a, b = int(round(a)), int(round(b))
        if b <= a:
            b = a + 1
        salida.append(f"{i}\n{_fmt(a)} --> {_fmt(b)}\n{txt}\n")
    escribir_texto(ruta, "\n".join(salida))
    return len(bloques)


_RE_T = re.compile(r"(\d+):(\d\d):(\d\d),(\d{3})")


def _ms(s):
    m = _RE_T.fullmatch(s.strip())
    if not m:
        raise ValueError(f"marca de tiempo inválida: {s!r}")
    h, mi, se, ms = map(int, m.groups())
    return ((h * 60 + mi) * 60 + se) * 1000 + ms


def validar_srt(ruta, duracion_audio):
    """Devuelve la lista de problemas (vacía si el SRT es válido)."""
    problemas = []
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            contenido = f.read().strip()
    except OSError as e:
        return [f"No se puede leer el SRT: {e}"]
    if not contenido:
        return ["SRT vacío"]
    previo_fin, esperado, ultimo_fin = 0, 1, 0
    for bloque in re.split(r"\n\s*\n", contenido):
        lineas = bloque.strip().split("\n")
        if len(lineas) < 3 or not lineas[0].isdigit() or int(lineas[0]) != esperado:
            problemas.append(f"índice no consecutivo cerca del bloque {esperado}")
            break
        try:
            a, b = (_ms(x) for x in lineas[1].split("-->"))
        except ValueError as e:
            problemas.append(str(e))
            break
        if a < 0 or b <= a:
            problemas.append(f"tiempos inválidos en el bloque {esperado}")
        if a < previo_fin - 1:
            problemas.append(f"solapamiento en el bloque {esperado}")
        previo_fin, ultimo_fin = b, b
        esperado += 1
    if abs(ultimo_fin / 1000.0 - duracion_audio) > 1.0:
        problemas.append(f"el SRT termina en {ultimo_fin / 1000:.1f}s y el audio dura {duracion_audio:.1f}s")
    return problemas

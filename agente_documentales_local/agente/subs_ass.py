# -*- coding: utf-8 -*-
"""Subtítulos ASS de alta legibilidad: tipografía gruesa con contorno y sombra, fuera de las zonas que tapa la
interfaz de YouTube/TikTok. Estilo 'palabra' (Shorts: 2-3 palabras con la palabra actual resaltada) o 'bloques'
(documental largo: frases de hasta 2 líneas)."""
import re

from .utils import escribir_texto

AMARILLO = "&H0000FFFF&"     # ASS usa orden BGR


def _t(seg):
    seg = max(0.0, seg)
    cs = int(round(seg * 100))
    h, r = divmod(cs, 360000)
    m, r = divmod(r, 6000)
    s, c = divmod(r, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _esc(t):
    return t.replace("\\", "/").replace("{", "(").replace("}", ")")


def _palabras_globales(escenas):
    """escenas: [(inicio_global, [{'t','ini','fin'}...])] -> lista plana con tiempos globales."""
    out = []
    for off, palabras in escenas:
        for p in palabras:
            out.append({"t": p["t"], "ini": off + p["ini"], "fin": off + p["fin"]})
    return out


def _grupos_palabra(pals, max_pal=3, max_car=22):
    grupos, g = [], []
    for p in pals:
        g.append(p)
        largo = len(" ".join(x["t"] for x in g))
        if len(g) >= max_pal or largo >= max_car or p["t"][-1:] in ".!?…,;:":
            grupos.append(g)
            g = []
    if g:
        grupos.append(g)
    return grupos


def _grupos_bloque(pals, max_linea=38, max_total=76):
    grupos, g = [], []
    for p in pals:
        g.append(p)
        largo = len(" ".join(x["t"] for x in g))
        if largo >= max_total or (p["t"][-1:] in ".!?…" and largo >= 20) or (p["t"][-1:] in ",;:" and largo >= max_linea):
            grupos.append(g)
            g = []
    if g:
        grupos.append(g)
    return grupos


def _dos_lineas(texto, max_linea=38):
    if len(texto) <= max_linea:
        return texto
    mitad = len(texto) // 2
    corte = texto.rfind(" ", 0, mitad + 6)
    if corte < mitad - 14:
        corte = texto.find(" ", mitad)
    if corte <= 0:
        return texto
    return texto[:corte] + r"\N" + texto[corte + 1:]


def cabecera(w, h, estilo):
    corto = estilo == "palabra"
    size = round(h * (0.052 if corto else 0.062))
    outline = max(2, round(h * (0.0042 if corto else 0.004)))
    margen_v = round(h * (0.24 if corto else 0.07))
    margen_h = round(w * 0.07)
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nScaledBorderAndShadow: yes\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,Arial,{size},&H00FFFFFF,&H0000FFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,"
            f"{outline},2,2,{margen_h},{margen_h},{margen_v},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def generar_ass(escenas, ruta, formato, estilo="bloques", mayusculas=False):
    """Devuelve el número de eventos escritos."""
    w, h = formato
    pals = _palabras_globales(escenas)
    lineas, n = [], 0
    if estilo == "palabra":
        grupos = _grupos_palabra(pals)
        for gi, g in enumerate(grupos):
            fin_grupo = g[-1]["fin"]
            if gi + 1 < len(grupos):
                fin_grupo = min(max(fin_grupo, g[-1]["ini"] + 0.15), grupos[gi + 1][0]["ini"])
            for k, p in enumerate(g):
                ini = p["ini"]
                fin = g[k + 1]["ini"] if k + 1 < len(g) else fin_grupo
                if fin <= ini:
                    fin = ini + 0.08
                partes = []
                for j, q in enumerate(g):
                    txt = _esc(q["t"].upper() if mayusculas else q["t"])
                    partes.append(r"{\c" + AMARILLO + "}" + txt + r"{\r}" if j == k else txt)
                lineas.append(f"Dialogue: 0,{_t(ini)},{_t(fin)},Default,,0,0,0,,{' '.join(partes)}")
                n += 1
    else:
        grupos = _grupos_bloque(pals)
        for gi, g in enumerate(grupos):
            ini, fin = g[0]["ini"], g[-1]["fin"]
            if gi + 1 < len(grupos):
                fin = min(max(fin, ini + 0.4), grupos[gi + 1][0]["ini"])
            texto = " ".join(_esc(q["t"].upper() if mayusculas else q["t"]) for q in g)
            lineas.append(f"Dialogue: 0,{_t(ini)},{_t(max(fin, ini + 0.1))},Default,,0,0,0,,{_dos_lineas(texto)}")
            n += 1
    escribir_texto(ruta, cabecera(w, h, estilo) + "\n".join(lineas) + "\n")
    return n


_RE_DIALOGO = re.compile(r"Dialogue: \d+,(\d+):(\d\d):(\d\d)\.(\d\d),(\d+):(\d\d):(\d\d)\.(\d\d),")


def validar_ass(ruta, duracion):
    """Lista de problemas (vacía = válido): eventos con fin<=inicio, desorden o fuera de la duración."""
    try:
        t = open(ruta, encoding="utf-8").read()
    except OSError as e:
        return [str(e)]
    prob, previo, ultimo, cuenta = [], -1.0, 0.0, 0
    for m in _RE_DIALOGO.finditer(t):
        a = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 100
        b = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 100
        cuenta += 1
        if b <= a:
            prob.append(f"evento {cuenta}: fin <= inicio")
        if a < previo - 0.01:
            prob.append(f"evento {cuenta}: desordenado")
        previo, ultimo = a, max(ultimo, b)
    if cuenta == 0:
        prob.append("sin eventos")
    if ultimo > duracion + 1.0:
        prob.append(f"termina en {ultimo:.1f}s, después del audio ({duracion:.1f}s)")
    return prob[:5]

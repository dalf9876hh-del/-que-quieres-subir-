# -*- coding: utf-8 -*-
"""Limpieza y comprobaciones de texto narrable en español."""
import re

_STOP_ES = {
    "de", "la", "el", "que", "en", "y", "los", "las", "un", "una", "se", "por", "con", "para",
    "es", "del", "al", "lo", "su", "como", "más", "pero", "sus", "le", "ya", "o", "este", "esta",
    "entre", "cuando", "muy", "sin", "sobre", "también", "hasta", "hay", "donde", "desde", "todo",
    "nos", "durante", "son", "fue", "ser", "han", "tiene", "puede", "uno", "otros", "estos",
}

_RE_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\uFE0F]")
_RE_CJK = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def limpiar_narracion(t):
    """Quita razonamientos, markdown, títulos, viñetas y acotaciones; deja párrafos narrables."""
    t = re.sub(r"<think>.*?</think>", "", t or "", flags=re.S | re.I)
    t = re.sub(r"\[[^\]]{0,80}\]", "", t).replace("\r", "")
    lineas = []
    for linea in t.split("\n"):
        l = linea.strip()
        if not l:
            lineas.append("")
            continue
        if l.startswith("#") or re.fullmatch(r"[-=_*]{3,}", l):
            continue
        l = re.sub(r"^\s*([-*•–]|\d+[.)])\s+", "", l)
        l = re.sub(r"[*_`~]+", "", l)
        l = _RE_EMOJI.sub("", l)
        l = re.sub(r"\s+", " ", l).strip()
        if not l:
            continue
        if re.match(r"^(parte|sección|seccion|capítulo|capitulo|escena|narrador|locutor|voz en off)\b", l, re.I) \
                and len(l.split()) <= 9 and not l.endswith("."):
            continue
        if l.endswith(":") and len(l.split()) <= 8:
            continue
        lineas.append(l)
    parrafos, actual = [], []
    for l in lineas:
        if l == "":
            if actual:
                parrafos.append(" ".join(actual))
                actual = []
        else:
            actual.append(l)
    if actual:
        parrafos.append(" ".join(actual))
    return "\n\n".join(parrafos).strip()


def recortar_a_frase(t):
    """Si el texto quedó cortado a mitad de frase, lo recorta a la última frase completa."""
    t = t.rstrip()
    if not t or t[-1] in ".!?…\"”»)":
        return t
    m = max(t.rfind(c) for c in ".!?…")
    if m > len(t) * 0.5:
        return t[: m + 1]
    return t + "."


def dividir_frases(t):
    return [f.strip() for f in re.split(r"(?<=[.!?…])\s+", t.replace("\n", " ")) if f.strip()]


def primeras_frases(t, n=2, max_chars=300):
    return " ".join(dividir_frases(t)[:n])[:max_chars]


def parece_espanol(t):
    palabras = re.findall(r"[^\W\d_]+", t.lower())
    if len(palabras) < 15:
        return False
    if len(_RE_CJK.findall(t)) > 0.02 * max(1, len(t)):
        return False
    prop = sum(1 for p in palabras if p in _STOP_ES) / len(palabras)
    return prop >= 0.20


def _ngramas(t, n):
    p = re.findall(r"\w+", t.lower())
    return {tuple(p[i:i + n]) for i in range(max(0, len(p) - n + 1))}


def solapamiento(nuevo, previo, n=5):
    """Proporción de n-gramas del texto nuevo que ya aparecen en el previo (0 a 1)."""
    a = _ngramas(nuevo, n)
    if not a or not previo:
        return 0.0
    return len(a & _ngramas(previo, n)) / len(a)


_RE_RAZONA = re.compile(r"^\s*(okay|ok[,.]|alright|hmm|let me|let's|first,? i|the user|i need to|i should|i'll|wait[,.])",
                        re.I)


def es_razonamiento(t):
    """True si el texto parece el razonamiento interno del modelo (en inglés) y no un guion."""
    return bool(_RE_RAZONA.match(t or ""))

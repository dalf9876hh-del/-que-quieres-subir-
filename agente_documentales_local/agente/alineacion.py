# -*- coding: utf-8 -*-
"""Tiempos por palabra para los subtítulos dinámicos. Tres niveles, de mayor a menor precisión:
  1) Whisper / faster-whisper (opcional): transcribe el audio y se alinea con el guion real.
  2) Marcas del propio edge-tts (WordBoundary/SentenceBoundary), si el servicio las envía.
  3) Reparto proporcional por escena: cada escena tiene su audio y su duración REALES; solo el reparto interno
     entre palabras es aproximado (error típico de décimas de segundo)."""
import difflib
import re
import unicodedata

_MODELOS = {}


def _norm(p):
    p = unicodedata.normalize("NFKD", p)
    p = "".join(c for c in p if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9ñ]", "", p)


def tokens(texto):
    return re.findall(r"\S+", texto)


def proporcional(texto, dur_voz):
    """Reparte dur_voz entre las palabras según su longitud y las pausas de puntuación."""
    toks = tokens(texto)
    if not toks:
        return []
    pesos = []
    for t in toks:
        p = len(_norm(t)) + 2
        if t[-1:] in ",;:":
            p += 3
        if t[-1:] in ".!?…":
            p += 6
        pesos.append(p)
    total, t0, res = float(sum(pesos)), 0.0, []
    for tok, p in zip(toks, pesos):
        d = dur_voz * p / total
        res.append({"t": tok, "ini": t0, "fin": t0 + d})
        t0 += d
    return res


def _interpolar(res, dur_voz):
    """Rellena las palabras sin tiempo repartiéndolas entre sus vecinas con tiempo."""
    n, i = len(res), 0
    while i < n:
        if res[i]["ini"] is not None:
            i += 1
            continue
        j = i
        while j < n and res[j]["ini"] is None:
            j += 1
        a = res[i - 1]["fin"] if i > 0 else 0.0
        b = res[j]["ini"] if j < n else dur_voz
        b = max(b, a)
        paso = (b - a) / (j - i)
        for k in range(i, j):
            res[k]["ini"], res[k]["fin"] = a + paso * (k - i), a + paso * (k - i + 1)
        i = j
    for k in range(n):                      # monotonía
        if k and res[k]["ini"] < res[k - 1]["fin"]:
            res[k]["ini"] = res[k - 1]["fin"]
        res[k]["fin"] = max(res[k]["fin"], res[k]["ini"] + 0.01)
    return res


def alinear_con_guion(texto, palabras_asr, dur_voz):
    """palabras_asr: [{'t','ini','fin'}] (Whisper o WordBoundary). Se conserva el TEXTO del guion y se toman
    los tiempos de las palabras coincidentes; las demás se interpolan."""
    toks = tokens(texto)
    a = [_norm(t) for t in toks]
    b = [_norm(p["t"]) for p in palabras_asr]
    res = [{"t": t, "ini": None, "fin": None} for t in toks]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for bloque in sm.get_matching_blocks():
        for k in range(bloque.size):
            res[bloque.a + k]["ini"] = palabras_asr[bloque.b + k]["ini"]
            res[bloque.a + k]["fin"] = palabras_asr[bloque.b + k]["fin"]
    if not any(r["ini"] is not None for r in res):
        return None
    return _interpolar(res, dur_voz)


def desde_limites(texto, limites, dur_voz):
    """Usa las marcas de edge-tts. WordBoundary -> alineación por palabras; SentenceBoundary -> reparto
    proporcional dentro de cada frase."""
    if not limites:
        return None
    palabras = [{"t": l["t"], "ini": l["ini"], "fin": l["fin"]} for l in limites if l.get("tipo") == "WordBoundary"]
    if palabras:
        return alinear_con_guion(texto, palabras, dur_voz)
    frases = [l for l in limites if l.get("tipo") == "SentenceBoundary"]
    if not frases:
        return None
    res = []
    toks = tokens(texto)
    pesos = [max(1, len(_norm(f["t"]))) for f in frases]
    total_tok, usados = len(toks), 0
    for f, p in zip(frases, pesos):
        n = max(1, round(total_tok * p / float(sum(pesos))))
        parte = toks[usados:usados + n]
        usados += n
        if not parte:
            continue
        for w in proporcional(" ".join(parte), max(0.05, f["fin"] - f["ini"])):
            res.append({"t": w["t"], "ini": f["ini"] + w["ini"], "fin": f["ini"] + w["fin"]})
    if usados < total_tok:                                   # sobrantes por redondeo
        resto = proporcional(" ".join(toks[usados:]), max(0.1, dur_voz - (res[-1]["fin"] if res else 0)))
        base = res[-1]["fin"] if res else 0.0
        res += [{"t": w["t"], "ini": base + w["ini"], "fin": base + w["fin"]} for w in resto]
    return res[:total_tok] if len(res) >= total_tok else None


# ----------------------------------------------------------------------------- Whisper (opcional)
def whisper_disponible():
    for nombre in ("faster_whisper", "whisper"):
        try:
            __import__(nombre)
            return nombre
        except ImportError:
            continue
    return None


def transcribir_palabras(wav, modelo="base", idioma="es"):
    """Devuelve [{'t','ini','fin'}] con faster-whisper (preferido, ligero en CPU) u openai-whisper."""
    motor = whisper_disponible()
    if motor is None:
        raise RuntimeError("No hay Whisper instalado (pip install faster-whisper  u  openai-whisper).")
    palabras = []
    if motor == "faster_whisper":
        from faster_whisper import WhisperModel
        m = _MODELOS.setdefault(("fw", modelo), WhisperModel(modelo, device="cpu", compute_type="int8"))
        segs, _ = m.transcribe(str(wav), language=idioma, word_timestamps=True)
        for s in segs:
            for w in (s.words or []):
                palabras.append({"t": w.word.strip(), "ini": float(w.start), "fin": float(w.end)})
    else:
        import whisper
        m = _MODELOS.setdefault(("ow", modelo), whisper.load_model(modelo))
        r = m.transcribe(str(wav), language=idioma, word_timestamps=True, fp16=False)
        for s in r.get("segments", []):
            for w in s.get("words", []):
                palabras.append({"t": w["word"].strip(), "ini": float(w["start"]), "fin": float(w["end"])})
    return palabras


def palabras_de_escena(texto, dur_voz, limites=None, wav=None, usar_whisper=False, modelo="base"):
    """Mejor estimación disponible. Devuelve (palabras, metodo)."""
    if usar_whisper and wav is not None:
        try:
            r = alinear_con_guion(texto, transcribir_palabras(wav, modelo), dur_voz)
            if r:
                return r, "whisper"
        except Exception:  # noqa: BLE001 - cualquier fallo de Whisper cae al siguiente método
            pass
    r = desde_limites(texto, limites, dur_voz)
    if r:
        return r, "edge-tts"
    return proporcional(texto, dur_voz), "proporcional"

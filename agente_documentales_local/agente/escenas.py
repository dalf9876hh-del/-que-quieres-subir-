# -*- coding: utf-8 -*-
"""Escenas del video: parser de JSON tolerante a fallos del modelo, división de la narración en escenas y
planificación visual (VISUAL_PROMPT) y sonora (SFX).

La NARRACIÓN (VOICEOVER) nunca se confía al modelo: el guion ya escrito se divide con Python, de modo que el
modelo solo puede aportar el texto de los prompts visuales y los efectos de sonido."""
import json
import re
import unicodedata

from . import ollama, texto as tx
from .utils import contar_palabras

SFX_VALIDOS = ("whoosh", "pop", "none")

_SINONIMOS = {
    "voiceover": {"voiceover", "voz", "vozenoff", "narracion", "narration", "locucion", "texto", "dialogo", "voice"},
    "visual_prompt": {"visualprompt", "visual", "prompt", "imagen", "imageprompt", "descripcionvisual", "escenavisual"},
    "sfx": {"sfx", "efecto", "efectos", "efectosonido", "sonido", "fx"},
}
_CLAVES_LISTA = {"scenes", "escenas", "scene", "escena", "guion", "script", "items", "data"}


def _norm_clave(k):
    k = unicodedata.normalize("NFKD", str(k))
    k = "".join(c for c in k if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", k)


# ----------------------------------------------------------------------------- reparación de JSON
def _reparar(s):
    """Recorre el texto como un tokenizador: arregla comillas, comas finales, claves sin comillas,
    saltos de línea dentro de cadenas, comentarios y estructuras sin cerrar."""
    s = (s.replace("“", '"').replace("”", '"').replace("„", '"').replace("«", '"').replace("»", '"')
         .replace("‘", "'").replace("’", "'"))
    out, pila, i, n = [], [], 0, len(s)
    ultimo_sig = ""
    while i < n:
        c = s[i]
        if c == "/" and s[i:i + 2] == "//":
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c == "/" and s[i:i + 2] == "/*":
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'":
            comilla, i = c, i + 1
            buf = []
            while i < n:
                ch = s[i]
                if ch == "\\" and i + 1 < n:
                    sig = s[i + 1]
                    buf.append("\\" + sig if sig in '"\\/bfnrtu' else sig)
                    i += 2
                    continue
                if ch == comilla:
                    # una comilla simple interior (apóstrofo) entre letras no cierra la cadena
                    if comilla == "'" and i + 1 < n and s[i + 1].isalpha() and buf and buf[-1][-1:].isalpha():
                        buf.append("'")
                        i += 1
                        continue
                    break
                if ch == '"':
                    buf.append('\\"')
                elif ch == "\n":
                    buf.append("\\n")
                elif ch == "\r" or ch == "\t":
                    buf.append(" ")
                elif ord(ch) < 32:
                    buf.append(" ")
                else:
                    buf.append(ch)
                i += 1
            i += 1  # cierra la comilla (o fin de texto con cadena sin cerrar)
            out.append('"' + "".join(buf) + '"')
            ultimo_sig = '"'
            continue
        if c in "{[":
            pila.append("}" if c == "{" else "]")
            out.append(c)
            ultimo_sig = c
        elif c in "}]":
            while pila and pila[-1] != c:      # cierra lo que quedó abierto dentro
                out.append(pila.pop())
            if pila:
                pila.pop()
                # coma final antes del cierre
                k = len(out) - 1
                while k >= 0 and out[k].isspace():
                    k -= 1
                if k >= 0 and out[k] == ",":
                    out[k] = ""
                out.append(c)
            ultimo_sig = c
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (s[j].isalnum() or s[j] in "_"):
                j += 1
            palabra = s[i:j]
            k = j
            while k < n and s[k] in " \t":
                k += 1
            if k < n and s[k] == ":" and ultimo_sig in ("{", ","):
                out.append('"' + palabra + '"')      # clave sin comillas
            elif palabra.lower() in ("true", "false", "null"):
                out.append(palabra.lower())
            else:
                out.append('"' + palabra + '"')      # palabra suelta -> cadena
            ultimo_sig = "a"
            i = j
            continue
        else:
            out.append(c)
            if not c.isspace():
                ultimo_sig = c
        i += 1
    while pila:
        k = len(out) - 1
        while k >= 0 and (not out[k] or out[k].isspace()):
            k -= 1
        if k >= 0 and out[k] == ",":
            out[k] = ""
        out.append(pila.pop())
    r = "".join(out)
    return re.sub(r",\s*([}\]])", r"\1", r)


def _bloque_json(t):
    """Del primer { o [ al cierre equivalente (o al final si el texto quedó cortado)."""
    ini = min([p for p in (t.find("{"), t.find("[")) if p >= 0] or [-1])
    if ini < 0:
        return None
    prof, en_cad, esc = 0, False, False
    for j in range(ini, len(t)):
        c = t[j]
        if en_cad:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                en_cad = False
            continue
        if c == '"':
            en_cad = True
        elif c in "{[":
            prof += 1
        elif c in "}]":
            prof -= 1
            if prof == 0:
                return t[ini:j + 1]
    return t[ini:]


def _por_regex(t):
    """Último recurso: extrae pares clave:valor de VOICEOVER / VISUAL_PROMPT / SFX aunque el JSON esté roto."""
    patron = re.compile(
        r"""["']?\b(voiceover|voice_over|voz_en_off|voz|narracion|narración|visual_prompt|visualprompt|visual|prompt|sfx|efecto)\b["']?\s*[:=]\s*"""
        r"""(?:"(.*?)"|'(.*?)'|([^\n,}\]]+))\s*(?=,|\}|\]|\n|$)""", re.I | re.S)
    escenas, actual = [], {}
    for m in patron.finditer(t):
        clave = _clave_canonica(m.group(1))
        valor = next((g for g in m.groups()[1:] if g is not None), "").strip()
        if clave == "voiceover" and "voiceover" in actual:
            escenas.append(actual)
            actual = {}
        if clave:
            actual[clave] = valor
    if actual:
        escenas.append(actual)
    return escenas


def _clave_canonica(k):
    nk = _norm_clave(k)
    for canon, sin in _SINONIMOS.items():
        if nk in sin:
            return canon
    return None


def _normalizar_sfx(v):
    v = _norm_clave(v or "")
    if any(x in v for x in ("whoosh", "swish", "swoosh")):
        return "whoosh"
    if any(x in v for x in ("pop", "click", "ping")):
        return "pop"
    return "none"


def _lista_de_escenas(obj):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _norm_clave(k) in _CLAVES_LISTA and isinstance(v, list):
                return v
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], (dict, str)):
                return v
        if any(_clave_canonica(k) for k in obj):
            return [obj]
    return []


def normalizar_escenas(obj, exigir_voz=True):
    """Convierte lo que haya devuelto el modelo en [{'voiceover','visual_prompt','sfx'}], con claves tolerantes."""
    res = []
    for el in _lista_de_escenas(obj):
        d = {"voiceover": "", "visual_prompt": "", "sfx": "none"}
        if isinstance(el, str):
            d["voiceover"] = el
        elif isinstance(el, dict):
            for k, v in el.items():
                canon = _clave_canonica(k)
                if canon and isinstance(v, (str, int, float)):
                    d[canon] = str(v)
                elif canon and isinstance(v, list):
                    d[canon] = " ".join(str(x) for x in v)
        else:
            continue
        d["voiceover"] = tx.limpiar_narracion(d["voiceover"]).replace("\n\n", " ")
        d["visual_prompt"] = re.sub(r"\s+", " ", d["visual_prompt"]).strip()[:300]
        d["sfx"] = _normalizar_sfx(d["sfx"])
        if exigir_voz and not d["voiceover"]:
            continue
        res.append(d)
    return res


def parsear_guion_json(texto, exigir_voz=True):
    """Devuelve (escenas, avisos). Nunca lanza excepción: si no puede, devuelve ([], [motivo])."""
    avisos = []
    t = re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S | re.I)
    candidatos = [m.group(1) for m in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", t, re.S)] + [t]
    for cand in candidatos:
        bloque = _bloque_json(cand)
        if not bloque:
            continue
        for intento, s in (("directo", bloque), ("reparado", _reparar(bloque))):
            try:
                obj = json.loads(s, strict=False)
            except ValueError:
                continue
            escenas = normalizar_escenas(obj, exigir_voz)
            if escenas:
                if intento == "reparado":
                    avisos.append("JSON con errores de sintaxis reparado automáticamente.")
                return escenas, avisos
    escenas = normalizar_escenas(_por_regex(t), exigir_voz)
    if escenas:
        avisos.append("JSON inválido: escenas recuperadas campo a campo con expresiones regulares.")
        return escenas, avisos
    return [], ["No se pudo interpretar la respuesta del modelo como JSON de escenas."]


# ----------------------------------------------------------------------------- planificación
_STOP = {"de", "la", "el", "que", "en", "y", "los", "las", "un", "una", "se", "por", "con", "para", "es", "del", "al",
         "lo", "su", "como", "más", "pero", "sus", "ya", "este", "esta", "entre", "cuando", "muy", "sin", "sobre",
         "también", "hasta", "hay", "donde", "desde", "todo", "son", "fue", "ser", "han", "tiene", "puede", "esto"}


def palabras_clave(texto, n=4):
    pal = [p for p in re.findall(r"[^\W\d_]{5,}", texto.lower()) if p not in _STOP]
    vistas, res = set(), []
    for p in sorted(pal, key=len, reverse=True):
        if p not in vistas:
            vistas.add(p)
            res.append(p)
        if len(res) >= n:
            break
    return res


def plan_deterministico(trozos, tema, inicios_seccion=(), cfg=None, ritmo=None):
    """Plan sin modelo: SFX por reglas (whoosh al empezar un párrafo/sección, o cada `ritmo` escenas; pop en datos y
    preguntas) y prompt visual básico. Los whoosh se separan al menos 3 escenas para no saturar."""
    plan, desde_pop, desde_whoosh = [], 99, 99
    for i, t in enumerate(trozos):
        sfx = "none"
        es_inicio = i == 0 or any(t.startswith(f) for f in inicios_seccion if f)
        toca = bool(ritmo) and i > 0 and i % ritmo == ritmo - 1
        if (es_inicio or toca) and i > 0 and desde_whoosh >= 3:
            sfx, desde_whoosh = "whoosh", 0
        elif desde_pop >= 4 and (re.search(r"\d", t) or "?" in t or "¿" in t):
            sfx, desde_pop = "pop", 0
        desde_pop += 1
        desde_whoosh += 1
        kw = ", ".join(palabras_clave(t, 3))
        plan.append({"voiceover": t, "sfx": sfx,
                     "visual_prompt": f"documentary photo, {tema[:60]}, {kw}, natural light, highly detailed, no text"})
    return plan


def planificar_con_llm(cfg, tema, trozos, log, llm=ollama.generar, lote=8):
    """Pide al modelo, por lotes, VISUAL_PROMPT y SFX de cada escena (JSON). Los fallos de formato se reparan o
    se sustituyen por el plan por defecto; la narración nunca cambia."""
    base = plan_deterministico(trozos, tema, cfg=cfg)
    avisos = []
    for ini in range(0, len(trozos), lote):
        grupo = trozos[ini:ini + lote]
        numeradas = "\n".join(f"{k + 1}. {t}" for k, t in enumerate(grupo))
        prompt = (f"Tema del video: {tema}.\nFragmentos de narración, en orden:\n{numeradas}\n\n"
                  "Para CADA fragmento devuelve un objeto JSON con: \"VISUAL_PROMPT\" (descripción de una imagen en "
                  "INGLÉS, concreta y cinematográfica, sin personas reales identificables, sin texto, sin logotipos), "
                  "y \"SFX\" (uno de: whoosh, pop, none; usa whoosh solo en cambios de tema y pop en datos "
                  "sorprendentes; la mayoría none). Responde ÚNICAMENTE con una lista JSON de "
                  f"{len(grupo)} objetos, sin explicaciones ni markdown.")
        try:
            crudo = llm(cfg, "Respondes solo con JSON válido.", prompt, 120 * len(grupo), 0.4)
        except ollama.OllamaError as e:
            avisos.append(f"Plan visual (lote {ini // lote + 1}): {e}; se usa el plan por defecto.")
            continue
        escenas, av = parsear_guion_json(crudo, exigir_voz=False)
        avisos += av
        if len(escenas) != len(grupo):
            avisos.append(f"Plan visual (lote {ini // lote + 1}): el modelo devolvió {len(escenas)} de {len(grupo)} "
                          "escenas; se completan con el plan por defecto.")
        for k, e in enumerate(escenas[:len(grupo)]):
            if e["visual_prompt"] and contar_palabras(e["visual_prompt"]) >= 3:
                base[ini + k]["visual_prompt"] = e["visual_prompt"]
            if e["sfx"] in SFX_VALIDOS:
                base[ini + k]["sfx"] = e["sfx"]
    # el modelo no puede llenar el video de efectos: máximo 1 de cada 3 escenas
    ultimo = -3
    for k, e in enumerate(base):
        if e["sfx"] != "none":
            if k - ultimo < 3:
                e["sfx"] = "none"
            else:
                ultimo = k
    return base, avisos

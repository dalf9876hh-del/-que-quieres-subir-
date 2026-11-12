# -*- coding: utf-8 -*-
"""Generación del guion largo por secciones (reanudable) y del guion Short."""
import re
import time
from pathlib import Path

from . import ollama, texto as tx
from .utils import contar_palabras, escribir_json, escribir_texto, hash_texto, leer_json, leer_texto

SECCIONES = [
    ("Introducción y pregunta central", 0.13,
     "Abre el documental con un gancho narrativo y plantea la pregunta central del tema. Presenta por qué importa."),
    ("Antecedentes y contexto", 0.15,
     "Explica los antecedentes y el contexto histórico o conceptual necesarios para entender el tema."),
    ("Explicación principal", 0.17,
     "Explica con claridad cómo funciona el tema o qué es lo esencial, con ejemplos y analogías comprensibles."),
    ("Evidencias, ejemplos y descubrimientos", 0.17,
     "Describe evidencias, casos, ejemplos y descubrimientos relevantes, distinguiendo lo establecido de lo supuesto."),
    ("Impacto, consecuencias y controversias", 0.15,
     "Analiza el impacto, las consecuencias y las controversias o debates que rodean al tema."),
    ("Preguntas abiertas y límites del conocimiento", 0.12,
     "Expón lo que aún no se sabe, las preguntas abiertas y los límites actuales del conocimiento."),
    ("Conclusión", 0.11,
     "Cierra el documental con una conclusión coherente que retome la pregunta central y deje una reflexión final."),
]

_ESTRUCTURAS_ALT = [
    ("mito_realidad", [
        ("Lo que creemos saber", "Abre con una creencia popular sobre el tema y plantea por qué podría ser incompleta o errónea."),
        ("Qué se sabe con certeza", "Expón los hechos bien establecidos, separando con claridad lo comprobado de lo supuesto."),
        ("Los mitos más comunes", "Analiza ideas erróneas o exageraciones habituales y por qué circulan."),
        ("Lo que dicen los datos", "Describe evidencias, casos y ejemplos concretos, y sus límites."),
        ("Por qué importa", "Explica las consecuencias prácticas para las personas, la sociedad o el futuro."),
        ("Lo que todavía se discute", "Señala los puntos abiertos y los desacuerdos entre especialistas."),
        ("Cómo pensar sobre esto", "Cierra dando criterio al espectador para valorar información parecida y retoma el gancho inicial.")]),
    ("cronologia", [
        ("Un momento clave", "Abre con una escena o momento concreto que resuma el tema, sin inventar detalles."),
        ("Cómo empezó todo", "Cuenta los orígenes y el contexto previo."),
        ("Los giros decisivos", "Relata los cambios y acontecimientos que marcaron la historia del tema."),
        ("El presente: dónde estamos", "Describe la situación actual con prudencia y apoyándote en las fuentes."),
        ("Las personas y fuerzas implicadas", "Explica quiénes o qué intereses y factores influyen, de forma equilibrada."),
        ("Escenarios posibles", "Plantea hacia dónde podría evolucionar, dejando claro que son posibilidades y no predicciones."),
        ("Lo que esta historia nos enseña", "Cierra con una reflexión que conecte el origen con el presente.")]),
    ("causas_efectos", [
        ("El problema en una pregunta", "Abre formulando con precisión la pregunta o el problema que se va a analizar."),
        ("Las causas de fondo", "Explica las causas principales y cómo se relacionan entre sí."),
        ("El mecanismo paso a paso", "Describe cómo ocurre el fenómeno, en orden y con analogías sencillas."),
        ("Los efectos visibles", "Presenta las consecuencias más evidentes con ejemplos."),
        ("Los efectos menos obvios", "Explica consecuencias indirectas o poco comentadas, con cautela."),
        ("Soluciones y dilemas", "Expón respuestas posibles y los dilemas o costes que implican."),
        ("Qué conviene recordar", "Cierra resumiendo las ideas esenciales y dejando una reflexión.")]),
    ("tres_claves", [
        ("La pregunta que mueve este tema", "Abre planteando la pregunta central y anuncia que hay tres claves para entenderla."),
        ("Primera clave", "Desarrolla la primera idea esencial con ejemplos."),
        ("Segunda clave", "Desarrolla la segunda idea esencial, enlazándola con la anterior."),
        ("Tercera clave", "Desarrolla la tercera idea esencial y su relación con las otras dos."),
        ("Objeciones y contraargumentos", "Presenta críticas o puntos de vista contrarios con equilibrio."),
        ("Lo que no sabemos", "Admite los límites del conocimiento y las incógnitas abiertas."),
        ("La idea final", "Cierra con una síntesis memorable que retome la pregunta inicial.")]),
]
ESTRUCTURAS = [("clasica", SECCIONES)] + [
    (n, [(t, [0.13, 0.15, 0.17, 0.17, 0.15, 0.12, 0.11][i], ins) for i, (t, ins) in enumerate(items)])
    for n, items in _ESTRUCTURAS_ALT]

GANCHOS = [
    "Empieza con una pregunta inesperada y concreta.",
    "Empieza describiendo una escena concreta, sin inventar hechos.",
    "Empieza con una paradoja o una contradicción aparente.",
    "Empieza con una afirmación que el espectador daría por cierta y después cuestiónala.",
    "Empieza con un dato que aparezca en las fuentes recuperadas (solo si aparece en ellas).",
]


def elegir_estructura(tema, d_partes, cfg):
    """Estructura narrativa distinta según el tema (y estable al reanudar): evita que los 40 videos tengan
    exactamente el mismo esqueleto, una señal típica de contenido producido en masa."""
    ruta = d_partes / "estructura.json"
    guardada = leer_json(ruta, {}) or {}
    nombres = [n for n, _ in ESTRUCTURAS]
    if guardada.get("nombre") in nombres:
        nombre, g = guardada["nombre"], int(guardada.get("gancho", 0)) % len(GANCHOS)
    else:
        if cfg.get("estructura_variable", True):
            h = int(hash_texto(tema)[:8], 16)
            nombre, g = nombres[h % len(nombres)], (h // len(nombres)) % len(GANCHOS)
        else:
            nombre, g = "clasica", 0
        escribir_json(ruta, {"nombre": nombre, "gancho": g, "n": len(SECCIONES)})
    return dict(ESTRUCTURAS)[nombre], g, nombre


ANGULOS_AMPLIACION = [
    "un ejemplo concreto o una analogía que ayude a comprender mejor el tema",
    "una perspectiva histórica o cultural adicional",
    "un debate o matiz sobre cómo se interpreta el tema",
    "una aplicación práctica o consecuencia en la vida cotidiana",
    "un detalle técnico o conceptual explicado de forma sencilla",
    "una reflexión prudente sobre los límites de lo que sabemos",
]

SISTEMA = (
    "RESPONDE SIEMPRE EN ESPAÑOL, aunque el tema, el título o las fuentes estén en inglés u otro idioma. "
    "Eres un guionista de documentales divulgativos en español de España neutro. Escribes SOLO texto para ser "
    "narrado en voz alta: párrafos continuos, sin títulos, sin viñetas, sin markdown, sin acotaciones, sin "
    "indicaciones técnicas y sin emojis. No inventes cifras, fechas, nombres, citas, estudios ni enlaces. Si no "
    "estás seguro de algo, usa fórmulas prudentes ('se cree', 'algunos investigadores sostienen') y distingue "
    "claramente hechos de hipótesis. Escribe siempre en español."
)


def _ruta_parte(d, i):
    return d / f"parte_{i:02d}.txt"


def _resumen_previo(partes):
    if not partes:
        return ""
    return "\n".join(f"- {t}: {tx.primeras_frases(x, 1, 220)}" for t, x in partes)


def _texto_previo(partes):
    return "\n\n".join(x for _, x in partes)


def _pedir(cfg, prompt, n_obj, log, llm, previo, minimo, extra=""):
    """Un intento. Devuelve (texto_limpio, motivo_rechazo_o_None)."""
    max_tokens = int(n_obj * 3.2) + 120
    try:
        crudo = llm(cfg, SISTEMA, prompt + extra, max_tokens)
    except ollama.ModeloRazonaError as e:
        return "", f"razonamiento del modelo: {e}"
    except ollama.OllamaError as e:
        return "", f"error de Ollama: {e}"
    t = tx.recortar_a_frase(tx.limpiar_narracion(crudo))
    if not t:
        return "", "respuesta vacía tras la limpieza"
    if not tx.parece_espanol(t):
        return t, "el texto no parece estar en español"
    if previo and tx.solapamiento(t, previo) > 0.40:
        return t, "repite contenido ya escrito"
    return t, None


def _generar_seccion(cfg, tema, notas, idx, titulo, instr, n_obj, previas, log, llm, total=None):
    total = total or len(SECCIONES)
    minimo = int(n_obj * 0.6)
    mejor, mejor_n = None, 0
    repetido, repetido_n = None, 0     # respaldo: texto válido en español pero algo repetitivo
    extra = ""
    razonamientos = 0
    for intento in range(1, int(cfg["reintentos_seccion"]) + 1):
        partes = [f"Tema del documental (el título puede estar en inglés; tú escribe en español): {tema}.", f"Sección {idx} de {total}: {titulo}.", instr]
        partes.append(f"Longitud: unas {n_obj} palabras (mínimo {minimo}), en varios párrafos de prosa narrativa.")
        if notas:
            partes.append("Contexto recuperado de fuentes, que puede estar en inglés (tradúcelo y úsalo solo como apoyo "
                          "y con cautela; no cites cifras que no aparezcan aquí):\n" + notas)
        if previas:
            partes.append("Ya se ha narrado lo siguiente (NO lo repitas, continúa con naturalidad):\n"
                          + _resumen_previo(previas))
        if idx > 1:
            partes.append("No saludes de nuevo ni empieces el documental otra vez.")
        if idx < total:
            partes.append("No concluyas el documental en esta sección.")
        partes.append("Aporta análisis y contexto propios; NO resumas una noticia frase por frase. Los hechos y las "
                      "cifras solo pueden salir de las fuentes recuperadas: si un dato no aparece en ellas, no lo "
                      "afirmes y explica el contexto general con honestidad.")
        prompt = ("\n\n".join(partes) + "\n\nEscribe ahora únicamente el texto de narración de esta sección. "
                  "La respuesta debe estar ÍNTEGRAMENTE EN ESPAÑOL (castellano).")
        t, motivo = _pedir(cfg, prompt, n_obj, log, llm, _texto_previo(previas), minimo, extra)
        n = contar_palabras(t)
        if motivo is None and n >= minimo:
            return t, None
        if motivo and motivo.startswith("razonamiento"):
            razonamientos += 1
            if razonamientos >= 2:
                raise ollama.OllamaError(
                    f"El modelo '{cfg['modelo_ollama']}' devuelve su razonamiento en inglés en lugar del guion "
                    "y no se puede desactivar. Pruebe otro modelo en config.json (p. ej. \"llama3.2:3b\") "
                    "o actualice Ollama. Se aborta para no perder tiempo.")
        log.warning(f"Sección {idx}, intento {intento}: {motivo or f'solo {n} palabras (mínimo {minimo})'}")
        if motivo and "español" in motivo:
            log.warning("  Texto rechazado (inicio): " + (t[:140].replace("\n", " ") or "(vacío)"))
        if motivo is None and n > mejor_n:
            mejor, mejor_n = t, n
        elif motivo and motivo.startswith("repite") and n > repetido_n and tx.solapamiento(t, _texto_previo(previas)) <= 0.65:
            repetido, repetido_n = t, n
        if motivo and motivo.startswith("error de Ollama"):
            time.sleep(min(10, 3 * intento))
        if motivo and "español" in motivo:
            extra = ("\n\nIMPORTANTE: tu respuesta anterior NO estaba en español. Escribe TODO en español "
                     "(castellano), sin una sola frase en inglés. Empieza directamente en español.")
            continue
        extra = (f"\n\nIMPORTANTE: tu intento anterior fue insuficiente ({motivo or str(n) + ' palabras'}). "
                 f"Escribe al menos {minimo} palabras en español, con contenido nuevo.")
    if mejor and mejor_n >= int(n_obj * 0.35):
        return mejor, f"Sección {idx} ('{titulo}') quedó corta: {mejor_n} palabras (objetivo {n_obj})."
    if repetido and repetido_n >= int(n_obj * 0.5):
        return repetido, f"Sección {idx} ('{titulo}') repite en parte contenido anterior; revísela."
    return None, f"No se pudo generar la sección {idx} ('{titulo}')."


def _recortar_a_limite(t, pmax):
    """Recorta a frases completas (las del propio modelo) hasta no pasar de pmax palabras."""
    acum, total = [], 0
    for f in tx.dividir_frases(t):
        nf = contar_palabras(f)
        if total + nf > pmax:
            break
        acum.append(f)
        total += nf
    return " ".join(acum)


def _numero(nombre):
    return int(re.search(r"(\d+)", nombre).group(1))


def ensamblar(d_partes):
    """Orden final: partes 1..N-1, ampliaciones, y la conclusión (parte N) al final."""
    n_final = int((leer_json(d_partes / "estructura.json", {}) or {}).get("n", len(SECCIONES)))
    partes = sorted(d_partes.glob("parte_*.txt"), key=lambda p: _numero(p.name))
    amps = sorted(d_partes.glob("ampliacion_*.txt"), key=lambda p: _numero(p.name))
    aporte = [d_partes / "aporte.txt"] if (d_partes / "aporte.txt").exists() else []
    orden = [p for p in partes if _numero(p.name) < n_final] + aporte + amps + \
        [p for p in partes if _numero(p.name) == n_final]
    return "\n\n".join((leer_texto(p) or "").strip() for p in orden if (leer_texto(p) or "").strip())


# ----------------------------------------------------------------------------- duración objetivo
def wpm_efectivo(cfg, dir_tema):
    """Palabras por minuto reales de la voz: la última medición de este tema o el valor de config.json."""
    r = leer_json(Path(dir_tema) / "partes_guion" / "ritmo.json", {}) or {}
    return float(r.get("wpm") or cfg.get("palabras_por_minuto", 150))


def guardar_ritmo(dir_tema, wpm):
    escribir_json(Path(dir_tema) / "partes_guion" / "ritmo.json", {"wpm": round(float(wpm), 1)})


def limite_palabras(cfg, dir_tema):
    """(objetivo, máximo) de palabras para la duración pedida. Sin duracion_objetivo_min: (palabras_objetivo, None).
    Nunca baja del mínimo exigido al guion (palabras_minimas + margen)."""
    dur = float(cfg.get("duracion_objetivo_min") or 0)
    if dur <= 0:
        return int(cfg["palabras_objetivo"]), None
    piso = int(cfg["palabras_minimas"]) + int(cfg["margen_seguridad"])
    maximo = max(int(dur * wpm_efectivo(cfg, dir_tema)), piso)
    return max(int(maximo * 0.97), piso), maximo


def _bloques(d_partes):
    n_final = int((leer_json(d_partes / "estructura.json", {}) or {}).get("n", len(SECCIONES)))
    partes = sorted(d_partes.glob("parte_*.txt"), key=lambda p: _numero(p.name))
    amps = sorted(d_partes.glob("ampliacion_*.txt"), key=lambda p: _numero(p.name))
    aporte = [d_partes / "aporte.txt"] if (d_partes / "aporte.txt").exists() else []
    orden = [p for p in partes if _numero(p.name) < n_final] + aporte + amps + \
        [p for p in partes if _numero(p.name) == n_final]
    res = []
    for p in orden:
        t = (leer_texto(p) or "").strip()
        if not t:
            continue
        if p.name.startswith("ampliacion"):
            tipo = "amp"
        elif p.name == "aporte.txt":
            tipo = "aporte"
        else:
            num = _numero(p.name)
            tipo = "intro" if num == 1 else ("conclusion" if num == n_final else "medio")
        res.append({"tipo": tipo, "texto": t, "orig": contar_palabras(t)})
    return res


def _recortar_parrafos(texto, objetivo, min_frases=3):
    """Quita frases completas del final del bloque hasta no pasar de `objetivo` palabras (conserva los párrafos)."""
    parrafos = [tx.dividir_frases(p) for p in texto.split("\n\n") if p.strip()]
    total = sum(len(p) for p in parrafos)
    while total > min_frases and contar_palabras("\n\n".join(" ".join(p) for p in parrafos)) > objetivo:
        while parrafos and not parrafos[-1]:
            parrafos.pop()
        parrafos[-1].pop()
        total -= 1
        if not parrafos[-1]:
            parrafos.pop()
    return "\n\n".join(" ".join(p) for p in parrafos if p)


def ensamblar_con_limite(d_partes, maximo, piso=0):
    """Guion final ajustado a `maximo` palabras SIN tocar las partes guardadas: primero se descartan las ampliaciones,
    luego se recortan frases del final de las secciones centrales (se conserva la introducción, el aporte propio y la
    conclusión). Devuelve (texto, {'quitadas': n})."""
    bloques = _bloques(d_partes)
    pal = lambda: sum(contar_palabras(b["texto"]) for b in bloques)  # noqa: E731
    inicial = pal()
    if maximo and inicial > maximo:
        for b in [b for b in bloques if b["tipo"] == "amp"][::-1]:
            if pal() <= maximo:
                break
            if pal() - contar_palabras(b["texto"]) >= piso:
                bloques.remove(b)
        for conserva in (0.55, 0.40):
            if pal() <= maximo:
                break
            medios = [b for b in bloques if b["tipo"] == "medio"]
            base = sum(contar_palabras(b["texto"]) for b in medios)
            exceso = min(pal() - maximo, pal() - piso)
            if not medios or exceso <= 0:
                break
            for b in medios:
                w = contar_palabras(b["texto"])
                objetivo_b = max(int(w - exceso * w / float(base)), int(b["orig"] * conserva))
                b["texto"] = _recortar_parrafos(b["texto"], objetivo_b)
    texto = "\n\n".join(b["texto"] for b in bloques)
    return texto, {"quitadas": max(0, inicial - contar_palabras(texto))}


def reescribir_guion_largo(dir_tema, cfg, maximo):
    """Vuelve a escribir guion_largo.txt ajustado a `maximo` palabras (para corregir la duración real medida)."""
    dir_tema = Path(dir_tema)
    piso = int(cfg["palabras_minimas"]) + int(cfg["margen_seguridad"])
    texto, info = ensamblar_con_limite(dir_tema / "partes_guion", maximo, piso)
    escribir_texto(dir_tema / "guion_largo.txt", texto + "\n")
    return texto, info


def _progreso(dir_tema, d_partes, log):
    t = ensamblar(d_partes)
    escribir_texto(dir_tema / "guion_largo_en_progreso.txt", t)
    log.info(f"Guion en progreso: {contar_palabras(t)} palabras acumuladas.")
    return contar_palabras(t)


def generar_guion_largo(cfg, tema, dir_tema, notas, log, llm=ollama.generar, aporte=""):
    d_partes = dir_tema / "partes_guion"
    d_partes.mkdir(parents=True, exist_ok=True)
    res = {"completo": False, "palabras": 0, "advertencias": [], "secciones_faltantes": [], "ampliaciones": 0}
    objetivo, maximo = limite_palabras(cfg, dir_tema)
    if maximo:
        log.info(f"Duración objetivo: {cfg['duracion_objetivo_min']} min -> ~{objetivo} palabras (máximo {maximo}).")
    secciones, g_idx, nombre_est = elegir_estructura(tema, d_partes, cfg)
    res["estructura"] = nombre_est
    log.info(f"Estructura narrativa: {nombre_est}")
    aporte = tx.limpiar_narracion(aporte or "")
    if aporte:
        escribir_texto(d_partes / "aporte.txt", aporte)           # comentario original del autor, narrado tal cual
        res["aporte_palabras"] = contar_palabras(aporte)
        log.info(f"Aporte propio incluido en el guion ({contar_palabras(aporte)} palabras).")
    previas = []
    acum_obj = acum_pal = 0                    # el modelo suele pasarse de largo: se corrige sección a sección
    for i, (titulo, peso, instr) in enumerate(secciones, 1):
        if i == 1:
            instr = instr + " " + GANCHOS[g_idx]
        ruta = _ruta_parte(d_partes, i)
        n_obj = max(80, round(objetivo * peso))
        if maximo and acum_pal and acum_obj:
            n_obj = max(80, int(n_obj * min(1.0, max(0.55, acum_obj / float(acum_pal)))))
        existente = (leer_texto(ruta) or "").strip()
        if existente and contar_palabras(existente) >= int(n_obj * 0.6):
            log.info(f"Sección {i} ya guardada ({contar_palabras(existente)} palabras): se reutiliza.")
            previas.append((titulo, existente))
            acum_obj += n_obj
            acum_pal += contar_palabras(existente)
            continue
        log.info(f"Generando sección {i}/{len(secciones)}: {titulo} (objetivo ~{n_obj} palabras)")
        t, aviso = _generar_seccion(cfg, tema, notas, i, titulo, instr, n_obj, previas, log, llm, len(secciones))
        if t and contar_palabras(t) > contar_palabras(existente):
            escribir_texto(ruta, t)
        elif existente and contar_palabras(existente) >= int(n_obj * 0.35):
            t = existente
        if aviso:
            res["advertencias"].append(aviso)
            log.warning(aviso)
        if not t:
            res["secciones_faltantes"].append(i)
            continue
        previas.append((titulo, t))
        acum_obj += n_obj
        acum_pal += contar_palabras(t)
        _progreso(dir_tema, d_partes, log)

    # Ampliaciones: las ya guardadas cuentan siempre; se generan más solo si hace falta.
    umbral = int(cfg["palabras_minimas"]) + int(cfg["margen_seguridad"])
    total = contar_palabras(ensamblar(d_partes))
    existentes = sorted(d_partes.glob("ampliacion_*.txt"), key=lambda p: _numero(p.name))
    n_amp = len(existentes)
    while total < umbral and n_amp < int(cfg["max_ampliaciones"]) and not res["secciones_faltantes"]:
        n_amp += 1
        angulo = ANGULOS_AMPLIACION[(n_amp - 1) % len(ANGULOS_AMPLIACION)]
        faltan = umbral - total
        n_obj = min(260, max(120, faltan + 40))
        indice = "\n".join(f"- {t}" for t, _ in previas)
        previo = ensamblar(d_partes)
        log.info(f"Total {total} < {umbral}: generando ampliación {n_amp} (~{n_obj} palabras, {angulo}).")
        prompt = (f"Tema del documental: {tema}.\n\nEl guion ya cubre estas secciones:\n{indice}\n\n"
                  f"Resumen de lo ya narrado:\n{_resumen_previo(previas)}\n\n"
                  f"Escribe una ampliación nueva de unas {n_obj} palabras sobre {angulo}. Debe aportar "
                  f"información que NO esté ya en el guion, enlazar con naturalidad con lo anterior y no "
                  f"concluir el documental.\n\nEscribe únicamente el texto de narración, ÍNTEGRAMENTE EN ESPAÑOL.")
        mejor = None
        for intento in range(1, int(cfg["reintentos_seccion"]) + 1):
            t, motivo = _pedir(cfg, prompt, n_obj, log, llm, previo, int(n_obj * 0.5))
            if motivo is None and contar_palabras(t) >= int(n_obj * 0.5):
                mejor = t
                break
            log.warning(f"Ampliación {n_amp}, intento {intento}: {motivo or 'demasiado corta'}")
        if not mejor:
            res["advertencias"].append(f"No se pudo generar la ampliación {n_amp}.")
            break
        escribir_texto(d_partes / f"ampliacion_{n_amp:02d}.txt", mejor)
        res["ampliaciones"] = n_amp
        total = _progreso(dir_tema, d_partes, log)
    res["ampliaciones"] = len(list(d_partes.glob("ampliacion_*.txt")))

    piso = int(cfg["palabras_minimas"]) + int(cfg["margen_seguridad"])
    texto, info_rec = ensamblar_con_limite(d_partes, maximo, piso)
    res["recortado"] = info_rec["quitadas"]
    if info_rec["quitadas"]:
        log.info(f"Guion ajustado a la duración pedida: se quitaron {info_rec['quitadas']} palabras "
                 "(las partes originales siguen guardadas).")
    res["palabras"] = contar_palabras(texto)
    if res["secciones_faltantes"]:
        res["advertencias"].append(f"Faltan secciones: {res['secciones_faltantes']}. Se conserva el borrador.")
        return res
    if res["palabras"] <= int(cfg["palabras_minimas"]):
        res["advertencias"].append(
            f"El guion tiene {res['palabras']} palabras y no supera las {cfg['palabras_minimas']}. "
            "Se conserva el borrador; no se marca como completo.")
        return res
    escribir_texto(dir_tema / "guion_largo.txt", texto + "\n")
    en_disco = contar_palabras(leer_texto(dir_tema / "guion_largo.txt") or "")  # comprobación sobre el archivo guardado
    res["palabras"] = en_disco
    res["completo"] = en_disco > int(cfg["palabras_minimas"])
    if not res["completo"]:
        res["advertencias"].append(f"El archivo guardado tiene {en_disco} palabras: insuficiente.")
    return res


def generar_short(cfg, tema, dir_tema, guion_largo, log, llm=ollama.generar):
    """Guion Short independiente (90-130 palabras por defecto). Devuelve dict con texto y estado."""
    pmin, pmax = int(cfg["palabras_short_min"]), int(cfg["palabras_short_max"])
    previo = (leer_texto(dir_tema / "guion_short.txt") or "").strip()
    if previo and 60 <= contar_palabras(previo) <= 160:     # reanudar: no se regenera un Short ya guardado
        log.info(f"Guion Short ya guardado ({contar_palabras(previo)} palabras): se reutiliza.")
        return {"completo": True, "texto": previo, "palabras": contar_palabras(previo), "advertencias": []}
    d_partes = dir_tema / "partes_guion"
    digest = []
    for p in sorted(d_partes.glob("parte_*.txt"), key=lambda p: _numero(p.name)):
        digest.append(tx.primeras_frases(leer_texto(p) or "", 2, 320))
    material = "\n".join(f"- {d}" for d in digest if d) or tx.primeras_frases(guion_largo, 8, 1500)
    inicio_largo = " ".join(guion_largo.split()[:100])
    res = {"completo": False, "texto": "", "palabras": 0, "advertencias": []}
    mejor, mejor_dist = None, 10 ** 6
    extra = ""
    for intento in range(1, int(cfg["reintentos_seccion"]) + 2):
        prompt = (f"Tema (puede estar en inglés; escribe en español): {tema}.\n\nMaterial del documental largo (úsalo como única base):\n{material}\n\n"
                  f"Escribe un guion para YouTube Shorts de entre {pmin} y {pmax} palabras (apunta a "
                  f"{(pmin + pmax) // 2}). Estructura: una primera frase que atrape (pregunta o idea "
                  f"sorprendente basada en el material), una idea central clara y un cierre interesante. "
                  f"Reformula con palabras nuevas, no copies el inicio del documental. No inventes datos. Sin "
                  f"hashtags, sin emojis, sin indicaciones de escena. Responde solo con el texto narrado, ÍNTEGRAMENTE EN ESPAÑOL."
                  + extra)
        log.info(f"Generando guion Short (intento {intento})...")
        try:
            crudo = llm(cfg, SISTEMA, prompt, 500)
        except ollama.OllamaError as e:
            log.warning(f"Short, intento {intento}: {e}")
            time.sleep(min(10, 3 * intento))
            continue
        t = tx.recortar_a_frase(re.sub(r"#\w+", "", tx.limpiar_narracion(crudo))).strip()
        n = contar_palabras(t)
        if not tx.parece_espanol(t) and n >= 15:
            log.warning(f"Short, intento {intento}: no parece español.")
            continue
        if tx.solapamiento(t, inicio_largo, 4) > 0.5:
            log.warning(f"Short, intento {intento}: copia el inicio del documental.")
            extra = "\n\nIMPORTANTE: no reutilices frases del documental; reescribe con otras palabras."
            continue
        if n > pmax:
            rec = _recortar_a_limite(t, pmax)
            if contar_palabras(rec) >= pmin:
                log.info(f"Short: recortado de {n} a {contar_palabras(rec)} palabras (a frase completa).")
                t, n = rec, contar_palabras(rec)
        dist = 0 if pmin <= n <= pmax else min(abs(n - pmin), abs(n - pmax))
        if dist < mejor_dist:
            mejor, mejor_dist = t, dist
        if dist == 0:
            break
        log.warning(f"Short, intento {intento}: {n} palabras (se piden {pmin}-{pmax}).")
        extra = (f"\n\nIMPORTANTE: tu intento anterior tuvo {n} palabras. Debe tener entre {pmin} y {pmax}.")
    if mejor is None:
        res["advertencias"].append("No se pudo generar el guion Short.")
        return res
    n = contar_palabras(mejor)
    res.update(texto=mejor, palabras=n)
    if pmin <= n <= pmax:
        res["completo"] = True
    elif 60 <= n <= 160:
        res["completo"] = True
        res["advertencias"].append(f"El Short tiene {n} palabras, fuera del rango {pmin}-{pmax}.")
    else:
        res["advertencias"].append(f"El Short tiene {n} palabras: demasiado lejos de {pmin}-{pmax}.")
    if res["completo"]:
        escribir_texto(dir_tema / "guion_short.txt", mejor + "\n")
        log.info(f"Guion Short guardado ({n} palabras).")
    return res

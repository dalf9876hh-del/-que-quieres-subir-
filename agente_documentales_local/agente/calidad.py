# -*- coding: utf-8 -*-
"""Control de calidad previo a publicar. NO existe forma de garantizar la aprobación de la monetización: la decide
YouTube (política de "contenido inauténtico": producido en masa o repetitivo, y contenido reutilizado). Este módulo
detecta SEÑALES de riesgo objetivas y comprobables en cada video y en el conjunto del lote, para corregirlas antes de
subir. Las comprobaciones de derechos de imagen se basan solo en nombres de archivo: no pueden saber la licencia."""
import re
from pathlib import Path

from .utils import ahora, escribir_json, escribir_texto, leer_json, leer_texto

_BASURA = [r"qr[-_ ]?code", r"(^|[-_ ])qr([-_ .]|$)", r"logo", r"icon", r"favicon", r"sprite", r"avatar", r"badge",
           r"button", r"banner", r"app[-_ ]?store", r"google[-_ ]?play", r"facebook", r"instagram", r"linkedin",
           r"tiktok", r"twitter", r"whatsapp", r"youtube", r"pinterest", r"share", r"subscribe", r"newsletter",
           r"placeholder", r"spacer", r"adchoices", r"^x$"]
_RE_BASURA = re.compile("|".join(_BASURA), re.I)
_AGENCIAS = ["gettyimages", "getty", "shutterstock", "istock", "alamy", "reuters", "afp", "apimages", "cnn", "bbc",
             "nytimes", "bloomberg", "peru21", "elcomercio", "larepublica", "infobae", "elpais", "agenciaefe"]
_RE_AGENCIA = re.compile("|".join(_AGENCIAS), re.I)
_RE_BBC = re.compile(r"^p0[0-9a-z]{6}\.(jpg|jpeg|png)(\.webp)?$", re.I)
_RE_NASA = re.compile(r"nasa|jpl|esa[-_ ]", re.I)


def filtrar_basura(rutas):
    """Separa logos, iconos de redes, códigos QR, botones de tiendas... (no aportan valor y delatan 'relleno')."""
    limpias, descartadas = [], []
    for r in rutas:
        nombre = Path(r).name
        stem = re.sub(r"(\.webp)?$", "", Path(nombre).stem, flags=re.I)
        if _RE_BASURA.search(stem):
            descartadas.append((str(r), "logo/icono/QR/botón"))
        else:
            limpias.append(r)
    return limpias, descartadas


def riesgo_licencias(rutas):
    """Clasifica por nombre de archivo: agencias/medios (probable copyright) y NASA (suele ser dominio público)."""
    agencia, nasa = [], []
    for r in rutas:
        n = Path(r).name
        if _RE_AGENCIA.search(n) or _RE_BBC.match(n):
            agencia.append(n)
        elif _RE_NASA.search(n):
            nasa.append(n)
    total = max(1, len(rutas))
    return {"agencia": agencia, "nasa": nasa, "proporcion_agencia": round(len(agencia) / total, 2)}


# ----------------------------------------------------------------------------- similitud entre videos
def _ngramas(texto, n=6):
    p = re.findall(r"\w+", texto.lower())
    return [tuple(p[i:i + n]) for i in range(max(0, len(p) - n + 1))]


def similitud_lote(guiones):
    """guiones: {tema: texto}. Devuelve {tema: {'max_similitud','con','plantilla','ejemplos'}}.
    - max_similitud: fracción de 6-gramas de un guion que aparecen en otro (contenido casi copiado).
    - plantilla: fracción de 6-gramas que se repiten en >= 25 % de los guiones (frases de relleno comunes)."""
    conj = {t: set(_ngramas(x)) for t, x in guiones.items()}
    res = {}
    n = len(guiones)
    umbral = max(3, int(0.25 * n + 0.999))
    cuenta = {}
    if n >= umbral:
        for s in conj.values():
            for g in s:
                cuenta[g] = cuenta.get(g, 0) + 1
    for t, s in conj.items():
        mejor, con = 0.0, None
        for u, o in conj.items():
            if u == t or not s or not o:
                continue
            v = len(s & o) / float(min(len(s), len(o)))
            if v > mejor:
                mejor, con = v, u
        comunes = [g for g in s if cuenta.get(g, 0) >= umbral]
        res[t] = {"max_similitud": round(mejor, 3), "con": con,
                  "plantilla": round(len(comunes) / max(1, len(s)), 3),
                  "ejemplos": [" ".join(g) for g in comunes[:3]]}
    return res


def aperturas_repetidas(guiones, palabras=5):
    """Temas que empiezan igual que otros (plantilla de gancho)."""
    claves = {}
    for t, x in guiones.items():
        k = " ".join(re.findall(r"\w+", x.lower())[:palabras])
        claves.setdefault(k, []).append(t)
    return {k: v for k, v in claves.items() if len(v) >= 3 and k}


def numeros_sin_fuente(guion, texto_fuentes):
    """Cifras y años del guion que no aparecen en las fuentes recuperadas: hay que verificarlas a mano."""
    fuentes = re.sub(r"[.,\s]", "", texto_fuentes or "")
    vistos, res = set(), []
    for m in re.finditer(r"\b\d[\d.,]{1,}\d\b|\b(?:19|20)\d{2}\b|\b\d{3,}\b", guion):
        crudo = m.group(0)
        norm = re.sub(r"[.,]", "", crudo)
        if norm in vistos or len(norm) < 3:
            continue
        vistos.add(norm)
        if norm not in fuentes:
            res.append(crudo)
    return res[:12]


# ----------------------------------------------------------------------------- evaluación
def evaluar_tema(cfg, out, sim=None, apertura_repetida=False):
    """Genera informe_calidad.json de un tema. Devuelve el dict (nivel: ok | revisar | riesgo)."""
    out = Path(out)
    inf = leer_json(out / "informe.json", {}) or {}
    motivos, recs = [], []
    guion = leer_texto(out / "guion_largo.txt") or ""
    inv = leer_json(out / "investigacion.json", {}) or {}
    fuentes = [f for f in inv.get("fuentes", []) if f.get("resumen") and not str(f["resumen"]).startswith("(solo")]
    nivel = "ok"

    def subir(n):
        nonlocal nivel
        orden = {"ok": 0, "revisar": 1, "riesgo": 2}
        if orden[n] > orden[nivel]:
            nivel = n

    # 1. valor propio
    aporte = (leer_texto(Path(inf.get("carpeta_origen", "")) / "aporte.txt") or "").strip() if inf.get("carpeta_origen") else ""
    if not aporte:
        motivos.append("Sin aporte propio: el guion es 100 % generado. YouTube pide contenido original con valor añadido.")
        recs.append("Cree 'aporte.txt' en la carpeta del tema con su opinión, experiencia o análisis (se narra en el video).")
        subir("revisar")
    # 2. fuentes y cifras
    if not fuentes:
        motivos.append("Sin fuentes con resumen: los datos del guion no están respaldados.")
        recs.append("Revise cada dato del guion contra fuentes fiables antes de publicar.")
        subir("revisar")
    sinf = numeros_sin_fuente(guion, " ".join(f.get("resumen", "") + " " + f.get("titulo", "") for f in fuentes))
    if sinf:
        motivos.append(f"Cifras/años del guion que no figuran en las fuentes: {', '.join(sinf)}.")
        recs.append("Verifique esas cifras: un modelo pequeño puede inventarlas.")
        subir("revisar")
    # 3. similitud con otros videos del lote
    if sim:
        if sim["max_similitud"] >= 0.12:
            motivos.append(f"Se parece un {int(sim['max_similitud'] * 100)} % a otro video del lote ('{str(sim['con'])[:50]}').")
            recs.append("Reescriba o elimine uno de los dos: el contenido casi duplicado es motivo de rechazo.")
            subir("riesgo")
        if sim["plantilla"] >= 0.08:
            motivos.append(f"El {int(sim['plantilla'] * 100)} % del guion usa frases que se repiten en muchos videos del lote "
                           f"(ej.: '{(sim['ejemplos'] or [''])[0]}').")
            recs.append("Varíe la estructura y las expresiones; evite la plantilla idéntica entre videos.")
            subir("revisar")
    if apertura_repetida:
        motivos.append("Empieza con las mismas primeras palabras que otros videos del lote.")
        subir("revisar")
    # 4. imágenes
    cal = inf.get("calidad_medios", {}) or {}
    lic = cal.get("licencias", {}) or {}
    if lic.get("proporcion_agencia", 0) >= 0.30:
        motivos.append(f"El {int(lic['proporcion_agencia'] * 100)} % de las imágenes parecen de agencias o medios de noticias "
                       "(Getty, CNN, BBC, AFP...): probable copyright y 'contenido reutilizado'.")
        recs.append("Sustitúyalas por imágenes propias, de dominio público (NASA, Wikimedia con licencia libre) o con licencia.")
        subir("riesgo")
    elif lic.get("proporcion_agencia", 0) >= 0.10:
        motivos.append("Algunas imágenes parecen de agencias o medios de noticias.")
        subir("revisar")
    if cal.get("descartadas_basura"):
        motivos.append(f"Se excluyeron {cal['descartadas_basura']} imágenes que eran logos, iconos de redes o códigos QR.")
    v = inf.get("video", {}).get("documental_largo.mp4", {}) or {}
    if v.get("escenas") and v.get("medios_unicos") and v["medios_unicos"] / v["escenas"] < 0.5:
        motivos.append(f"Muchas repeticiones visuales: {v['medios_unicos']} imágenes para {v['escenas']} escenas.")
        recs.append("Añada más imágenes originales a la carpeta (o aumente 'max_imagenes').")
        subir("revisar")
    # 5. transparencia
    if not cfg.get("descripcion_aviso"):
        motivos.append("La descripción no incluye aviso de voz sintética/IA.")
        recs.append("Active 'descripcion_aviso' y responda la pregunta de contenido sintético de YouTube Studio.")
        subir("revisar")
    if not any(inf.get("video", {}).get(k, {}).get("musica") for k in inf.get("video", {})):
        pass  # música opcional: no es un riesgo de monetización
    r = {"tema": inf.get("tema", out.name), "fecha": ahora(), "nivel": nivel, "motivos": motivos,
         "recomendaciones": list(dict.fromkeys(recs)),
         "nota": "Señales automáticas, no un veredicto: la decisión de monetizar es de YouTube y de su revisión manual."}
    escribir_json(out / "informe_calidad.json", r)
    return r


def evaluar_lote(salida_root, cfg, log=None):
    """Evalúa todos los temas con guion en la carpeta de salida y escribe RESUMEN_CALIDAD.txt."""
    salida_root = Path(salida_root)
    carpetas = []
    for d in sorted(p for p in salida_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if (d / "guion_largo.txt").exists():
            carpetas.append(d)                                    # estructura plana: salida\tema
        else:                                                     # estructura por canales: salida\canal\tema
            carpetas += [s for s in sorted(d.iterdir()) if s.is_dir() and not s.name.startswith("_")
                         and (s / "guion_largo.txt").exists()]
    nombre = lambda d: d.relative_to(salida_root).as_posix()  # noqa: E731
    guiones = {nombre(d): leer_texto(d / "guion_largo.txt") or "" for d in carpetas}
    sims = similitud_lote(guiones) if len(guiones) >= 2 else {}
    rep = aperturas_repetidas(guiones)
    en_rep = {t for v in rep.values() for t in v}
    res = {}
    for d in carpetas:
        res[nombre(d)] = evaluar_tema(cfg, d, sims.get(nombre(d)), nombre(d) in en_rep)
    orden = {"riesgo": 0, "revisar": 1, "ok": 2}
    L = ["RESUMEN DE CALIDAD ANTES DE PUBLICAR", f"Generado: {ahora()}", "",
         "Son señales automáticas para corregir problemas ANTES de subir; no garantizan ni predicen la decisión de YouTube.",
         "Política relevante: contenido 'inauténtico' (producido en masa o repetitivo) y contenido reutilizado.", ""]
    for t, r in sorted(res.items(), key=lambda kv: (orden[kv[1]["nivel"]], kv[0])):
        L.append(f"[{r['nivel'].upper():7s}] {t}")
        for m in r["motivos"]:
            L.append(f"          - {m}")
    conteo = {n: sum(1 for r in res.values() if r["nivel"] == n) for n in orden}
    L += ["", f"Total: {len(res)} videos -> riesgo {conteo['riesgo']}, revisar {conteo['revisar']}, ok {conteo['ok']}."]
    escribir_texto(salida_root / "RESUMEN_CALIDAD.txt", "\n".join(L) + "\n")
    if log:
        log.info(f"Calidad del lote: riesgo {conteo['riesgo']}, revisar {conteo['revisar']}, ok {conteo['ok']} "
                 f"(ver RESUMEN_CALIDAD.txt)")
    return res

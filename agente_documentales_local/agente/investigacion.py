# -*- coding: utf-8 -*-
"""Investigación textual gratuita: OpenAlex, Crossref, PubMed (si procede) y Wikipedia.
Solo se recuperan metadatos y resúmenes de TEXTO. Nunca se descargan imágenes ni videos."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .utils import ahora, escribir_json, leer_json

UA = "AgenteDocumentalLocal/1.0 (proyecto personal; uso moderado)"
PALABRAS_BIO = ("salud", "medicin", "enfermedad", "biolog", "cerebro", "genétic", "genetic", "célula",
                "celula", "virus", "cáncer", "cancer", "neuro", "farmac", "clínic", "clinic", "vacuna")


class SinConexion(Exception):
    pass


def _http_json(url, params, timeout, reintentos=2):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    ultimo = None
    for intento in range(reintentos + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                cuerpo = r.read().decode("utf-8", "replace")
            if not cuerpo.strip():
                raise ValueError("respuesta vacía")
            return json.loads(cuerpo)
        except urllib.error.HTTPError as e:
            ultimo = f"HTTP {e.code}"
            if e.code == 429 or e.code >= 500:
                espera = 2 * (intento + 1)
                try:
                    espera = min(30, int(e.headers.get("Retry-After", espera)))
                except (TypeError, ValueError):
                    pass
                time.sleep(espera)
                continue
            break
        except urllib.error.URLError as e:
            if isinstance(e.reason, (ConnectionError, OSError)) and intento == 0:
                ultimo = f"sin conexión: {e.reason}"
                raise SinConexion(ultimo)
            ultimo = f"error de red: {e.reason}"
            time.sleep(1)
        except TimeoutError:
            ultimo = "tiempo de espera agotado"
        except ValueError as e:
            ultimo = f"JSON inválido o vacío: {e}"
            break
    raise RuntimeError(f"{url.split('?')[0]}: {ultimo}")


def _abstract_openalex(inv):
    if not inv:
        return ""
    pos = {}
    for palabra, idxs in inv.items():
        for i in idxs:
            pos[i] = palabra
    return " ".join(pos[i] for i in sorted(pos))


def _limpiar(t, n=500):
    t = re.sub(r"<[^>]+>", " ", t or "")
    return re.sub(r"\s+", " ", t).strip()[:n]


def _openalex(q, t):
    d = _http_json("https://api.openalex.org/works",
                   {"search": q, "per-page": 5,
                    "select": "id,doi,display_name,publication_year,abstract_inverted_index"}, t)
    out = []
    for w in d.get("results", []) or []:
        titulo = w.get("display_name")
        enlace = w.get("doi") or w.get("id")
        if titulo and enlace:
            out.append({"fuente": "OpenAlex", "titulo": titulo, "anio": w.get("publication_year"),
                        "enlace": enlace, "resumen": _limpiar(_abstract_openalex(w.get("abstract_inverted_index")))})
    return out


def _crossref(q, t):
    d = _http_json("https://api.crossref.org/works",
                   {"query": q, "rows": 5, "select": "title,DOI,issued,abstract,URL"}, t)
    out = []
    for w in (d.get("message", {}) or {}).get("items", []) or []:
        titulo = (w.get("title") or [""])[0]
        doi = w.get("DOI")
        if not titulo or not doi:
            continue
        anio = None
        try:
            anio = w["issued"]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            pass
        out.append({"fuente": "Crossref", "titulo": titulo, "anio": anio,
                    "enlace": w.get("URL") or f"https://doi.org/{doi}", "resumen": _limpiar(w.get("abstract"))})
    return out


def _pubmed(q, t):
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    d = _http_json(base + "esearch.fcgi", {"db": "pubmed", "term": q, "retmax": 3, "retmode": "json"}, t)
    ids = (d.get("esearchresult", {}) or {}).get("idlist", []) or []
    if not ids:
        return []
    s = _http_json(base + "esummary.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}, t)
    out = []
    for i in ids:
        r = (s.get("result", {}) or {}).get(i)
        if r and r.get("title"):
            anio = (r.get("pubdate") or "")[:4]
            out.append({"fuente": "PubMed", "titulo": r["title"], "anio": int(anio) if anio.isdigit() else None,
                        "enlace": f"https://pubmed.ncbi.nlm.nih.gov/{i}/",
                        "resumen": "(solo metadatos bibliográficos; sin resumen recuperado)"})
    return out


def _wikipedia(q, t):
    d = _http_json("https://es.wikipedia.org/w/api.php",
                   {"action": "query", "list": "search", "srsearch": q, "srlimit": 2, "format": "json"}, t)
    out = []
    for r in (d.get("query", {}) or {}).get("search", []) or []:
        titulo = r.get("title")
        if not titulo:
            continue
        try:
            s = _http_json("https://es.wikipedia.org/api/rest_v1/page/summary/"
                           + urllib.parse.quote(titulo.replace(" ", "_")), None, t, reintentos=1)
        except (RuntimeError, SinConexion):
            continue
        extracto = _limpiar(s.get("extract"), 700)
        enlace = ((s.get("content_urls") or {}).get("desktop") or {}).get("page")
        if extracto and enlace:
            out.append({"fuente": "Wikipedia", "titulo": s.get("title", titulo), "anio": None,
                        "enlace": enlace, "resumen": extracto})
    return out


def investigar(cfg, tema, ruta_json, log, consulta_en=None):
    """Devuelve (notas_para_prompt, advertencias). Guarda investigacion.json."""
    previo = leer_json(ruta_json)
    if previo and not previo.get("sin_conexion") and previo.get("tema") == tema and previo.get("completada"):
        log.info("Investigación reutilizada desde disco.")
        return notas_desde(previo), []
    if callable(consulta_en):
        consulta_en = consulta_en()
    doc = {"tema": tema, "fecha": ahora(), "consulta": consulta_en or tema, "fuentes": [],
           "errores": [], "sin_conexion": False, "completada": False,
           "nota": "Metadatos y resúmenes recuperados automáticamente; no equivalen por sí solos a evidencia "
                   "suficiente para cualquier afirmación. Verifique las fuentes originales."}
    advert = []
    t = cfg["investigacion_timeout_seg"]
    consultas = [("OpenAlex", _openalex, consulta_en or tema), ("Crossref", _crossref, consulta_en or tema),
                 ("Wikipedia", _wikipedia, tema)]
    if any(p in tema.lower() for p in PALABRAS_BIO):
        consultas.insert(2, ("PubMed", _pubmed, consulta_en or tema))
    try:
        for nombre, fn, q in consultas:
            try:
                r = fn(q, t)
                if not r:
                    doc["errores"].append(f"{nombre}: sin resultados.")
                doc["fuentes"].extend(r)
            except RuntimeError as e:
                doc["errores"].append(str(e))
            time.sleep(0.5)
        doc["completada"] = True
    except SinConexion as e:
        doc["sin_conexion"] = True
        doc["errores"].append(str(e))
        advert.append("Sin conexión a Internet: el guion se apoya en conocimiento general del modelo, "
                      "sin fuentes verificadas.")
    if not doc["fuentes"] and not doc["sin_conexion"]:
        advert.append("La investigación no encontró fuentes para este tema (ver investigacion.json).")
    escribir_json(ruta_json, doc)
    log.info(f"Investigación: {len(doc['fuentes'])} fuentes, {len(doc['errores'])} incidencias.")
    return notas_desde(doc), advert


def notas_desde(doc):
    lineas = []
    for f in doc.get("fuentes", []):
        res = f.get("resumen", "")
        if not res or res.startswith("(solo metadatos"):
            continue
        lineas.append(f"- {f['titulo']} ({f.get('anio') or 's. f.'}), {f['fuente']}: {res[:320]}")
        if len(lineas) >= 5:
            break
    return "\n".join(lineas)[:1800]

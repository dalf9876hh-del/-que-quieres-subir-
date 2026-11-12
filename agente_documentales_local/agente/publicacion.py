# -*- coding: utf-8 -*-
"""Prepara la carpeta '_lista_videos' para subir a YouTube A MANO: cada video con su ficha de texto
(título, descripción y hashtags listos para copiar y pegar), sus subtítulos y un índice general ordenado."""
import os
import re
import shutil
from pathlib import Path

from . import ollama, texto as tx
from .utils import ahora, contar_palabras, escribir_json, leer_json, leer_texto, tam_archivo

SISTEMA_META = ("Eres un especialista en publicar videos en YouTube. Escribes en español. No inventas datos, "
                "cifras ni afirmaciones que no estén en el material. Sin emojis. Respondes exactamente con el "
                "formato pedido, sin comentarios adicionales.")

_STOP_HT = {"del", "las", "los", "una", "uno", "con", "por", "para", "que", "como", "sus", "este", "esta", "entre",
            "sobre", "desde", "sin", "más", "mas", "muy", "pero", "también", "tambien"}

TIPOS = [("largo", 1, "LARGO", "documental", "documental_largo.mp4", "subtitulos_largos.srt", "audio_generado.meta.json"),
         ("short", 2, "SHORT", "vertical", "short_vertical.mp4", "subtitulos_short.srt", "audio_generado_short.meta.json")]


# ----------------------------------------------------------------------------- saneado
def sanear_titulo(t, maximo=100):
    t = tx._RE_EMOJI.sub("", t or "")
    t = re.sub(r"[*_`<>\"“”«»]", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -–:.")
    if len(t) > maximo:
        t = t[:maximo].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return t


def normalizar_hashtags(texto, maximo=8, primero=None):
    """Devuelve una lista de hashtags válidos (sin espacios, sin repetidos, #Mayúscula opcional)."""
    res = []

    def _add(h):
        if h.lower() not in [x.lower() for x in res]:
            res.append(h)
    if primero:
        _add(primero)
    for tok in re.findall(r"#?\w+", texto or "", re.UNICODE):
        tok = tok.lstrip("#").replace("_", "")
        if len(tok) < 3 or tok.isdigit() or tok.lower() in _STOP_HT:
            continue
        _add("#" + tok)
    return res[:maximo]


def hashtags_desde_tema(tema):
    limpio = re.sub(r"(?<=\D)\d+$", "", tema)
    palabras = [p for p in re.findall(r"[^\W\d_]+", limpio) if len(p) >= 4 and p.lower() not in _STOP_HT]
    return "#" + " #".join(p.capitalize() for p in palabras[:3]) if palabras else ""


def _parsear(crudo):
    """Extrae TITULO / RESUMEN / HASHTAGS de la respuesta del modelo. None si no es válida."""
    t = re.sub(r"[*`]", "", crudo or "")
    mt = re.search(r"T[IÍií]TULO\s*:\s*(.+)", t, re.I)
    mr = re.search(r"RESUMEN\s*:\s*(.+?)(?=\n\s*HASHTAGS?\s*:|\Z)", t, re.I | re.S)
    mh = re.search(r"HASHTAGS?\s*:\s*(.+)", t, re.I)
    if not (mt and mr):
        return None
    titulo = sanear_titulo(mt.group(1))
    resumen = re.sub(r"\s+", " ", mr.group(1)).strip()
    if len(titulo) < 8 or len(resumen) < 40:
        return None
    if contar_palabras(resumen) >= 12 and not tx.parece_espanol(resumen + " " + titulo):
        return None
    return {"titulo": titulo, "resumen": resumen[:600], "hashtags": mh.group(1) if mh else ""}


def generar_metadatos(cfg, tipo, tema, material, log, llm):
    objetivo = "un video documental de varios minutos" if tipo == "largo" else "un YouTube Short de menos de un minuto"
    prompt = (f"Tema: {tema} (puede estar en inglés; escribe en español).\n\nMaterial del video (única base):\n"
              f"{material}\n\nPrepara la publicación en YouTube de {objetivo}. Responde EXACTAMENTE así:\n"
              "TITULO: un título atractivo y veraz de máximo 80 caracteres, sin comillas ni emojis\n"
              "RESUMEN: dos o tres frases (máximo 350 caracteres) que describan el video sin inventar datos\n"
              "HASHTAGS: de 5 a 7 hashtags en español relacionados con el tema, separados por espacios\n"
              "Escribe ÍNTEGRAMENTE EN ESPAÑOL.")
    for intento in (1, 2):
        try:
            crudo = llm(cfg, SISTEMA_META, prompt, 400, 0.6)
        except ollama.ModeloRazonaError as e:
            return None, f"el modelo devolvió razonamiento ({e})"
        except ollama.OllamaError as e:
            log.warning(f"Metadatos ({tipo}), intento {intento}: {e}")
            continue
        p = _parsear(crudo)
        if p:
            return p, None
        log.warning(f"Metadatos ({tipo}), intento {intento}: respuesta sin el formato pedido.")
    return None, "no se obtuvo una respuesta válida del modelo"


# ----------------------------------------------------------------------------- escritura
def _escribir(ruta, texto):
    """UTF-8 con BOM (el Bloc de notas antiguo lo necesita para mostrar bien los acentos). Atómico."""
    ruta = Path(ruta)
    tmp = ruta.with_name(ruta.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(texto)
    os.replace(tmp, ruta)


def _poner_archivo(origen, destino, enlazar=True):
    """Hace aparecer el archivo en la lista sin duplicar disco si es posible (enlace duro), o copiando."""
    origen, destino = Path(origen), Path(destino)
    if destino.exists():
        try:
            if os.path.samefile(origen, destino):          # ya es el mismo archivo (enlace duro vigente)
                return "existente"
        except OSError:
            pass
        if not enlazar and tam_archivo(destino) == tam_archivo(origen) and destino.stat().st_mtime >= origen.stat().st_mtime:
            return "existente"
        destino.unlink()                                    # versión antigua: se reemplaza por la nueva
    if enlazar:
        try:
            os.link(origen, destino)
            return "enlace"
        except OSError:
            pass
    shutil.copy2(origen, destino)
    return "copia"


def _mmss(seg):
    seg = int(round(seg or 0))
    return f"{seg // 60}:{seg % 60:02d}"


def _mb(n):
    return f"{n / 1_048_576:.0f} MB"


def _ficha(etiqueta, etiqueta_larga, base, tiene_srt, titulo, descripcion, hashtags, dur, tam):
    L = ["=" * 62, f" VIDEO: {base}.mp4",
         f" {etiqueta_larga}  |  duración {_mmss(dur)}  |  {_mb(tam)}", "=" * 62, "",
         "--- TÍTULO (copiar y pegar) ---", titulo, "",
         "--- DESCRIPCIÓN (copiar y pegar; ya incluye los hashtags al final) ---", descripcion, "",
         "--- HASHTAGS ---", " ".join(hashtags), "",
         "--- AL SUBIR EN YOUTUBE STUDIO (marque lo que vaya haciendo) ---",
         f"[ ] Subir el archivo: {base}.mp4"]
    if tiene_srt:
        L.append(f"[ ] Subtítulos: idioma Español > Subir archivo > {base}_subtitulos.srt")
    L += ["[ ] Idioma del video: Español",
          "[ ] Audiencia: indicar si es o no contenido para niños",
          "[ ] Contenido sintético o alterado: responder según las políticas de YouTube (la narración es voz "
          "sintética); revise la pregunta que muestra Studio",
          "[ ] Visibilidad: dejar en privado o programar, revisar el video y entonces publicar"]
    if etiqueta == "SHORT":
        L.append("[ ] Comprobar que YouTube lo trata como Short (vertical y de corta duración)")
    L += ["", "NOTA: revise el título y la descripción antes de publicar. Fueron redactados con ayuda de IA "
          "y pueden contener imprecisiones. Puede editar este archivo: no se sobrescribe al volver a ejecutar."]
    return "\n".join(L) + "\n"


def _leer_titulo(ruta):
    t = leer_texto(ruta) or ""
    m = re.search(r"--- TÍTULO[^\n]*---\n(.+?)\n", t)
    return m.group(1).strip() if m else ""


# ----------------------------------------------------------------------------- principal
def _material(out, tipo):
    if tipo == "short":
        return (leer_texto(out / "guion_short.txt") or "").strip()[:1500]
    partes = sorted((out / "partes_guion").glob("parte_*.txt"))
    dig = [tx.primeras_frases(leer_texto(p) or "", 2, 320) for p in partes]
    dig = [d for d in dig if d]
    return "\n".join(f"- {d}" for d in dig) or (leer_texto(out / "guion_largo.txt") or "")[:1500]


def _fuentes(out):
    d = leer_json(out / "investigacion.json", {}) or {}
    res = []
    for f in d.get("fuentes", []):
        if f.get("titulo") and f.get("enlace"):
            res.append(f"- {f['titulo'][:120]} ({f.get('anio') or 's. f.'}) {f['enlace']}")
    return res[:5]


def preparar_tema(cfg, tema, seg, out, lista_root, log, llm=ollama.generar, forzar_textos=False, canal=""):
    """Crea/actualiza la carpeta de lista para un tema. Devuelve {'advertencias': [...], 'items': n}."""
    out, lista_root = Path(out), Path(lista_root)
    adv, n_items = [], 0
    if not any(tam_archivo(out / t[4]) > 0 for t in TIPOS):
        return {"advertencias": [f"'{tema}': aún no tiene videos generados."], "items": 0}
    lista_root.mkdir(parents=True, exist_ok=True)
    indice = leer_json(lista_root / "indice.json", {"siguiente": 1, "temas": {}})
    seg_canal = seg.split("/", 1)[0] if "/" in seg else ""      # seg = "canal/tema" (o solo "tema")
    seg_tema = seg.split("/", 1)[-1]
    reg = indice["temas"].get(seg)
    if not reg:
        reg = {"numero": indice["siguiente"], "tema": tema, "creado": ahora()}
        if seg_canal:                                            # la numeración se reinicia en cada canal
            n_canal = 1 + max([r.get("n_canal", 0) for s, r in indice["temas"].items() if s.startswith(seg_canal + "/")] or [0])
            reg.update(canal=canal or seg_canal, n_canal=n_canal, carpeta=f"{seg_canal}/{n_canal:02d}_{seg_tema}")
        else:
            reg["carpeta"] = f"{reg['numero']:02d}_{seg_tema}"
        indice["temas"][seg] = reg
        indice["siguiente"] += 1
    carpeta = lista_root / reg["carpeta"]
    carpeta.mkdir(parents=True, exist_ok=True)
    datos = leer_json(carpeta / "_datos.json", {}) or {}
    fuentes = _fuentes(out)
    for tipo, num, etq, nom, mp4, srt, meta_n in TIPOS:
        origen = out / mp4
        if tam_archivo(origen) == 0:
            adv.append(f"'{tema}': falta {mp4}; no se incluye en la lista.")
            continue
        base = f"{num}_{etq}_{nom}"
        dur = (leer_json(out / meta_n, {}) or {}).get("duracion", 0)
        modo = _poner_archivo(origen, carpeta / f"{base}.mp4", cfg.get("lista_enlazar_videos", True))
        tiene_srt = tam_archivo(out / srt) > 0
        if tiene_srt:
            shutil.copyfile(out / srt, carpeta / f"{base}_subtitulos.srt")
        ficha = carpeta / f"{base}.txt"
        if ficha.exists() and not forzar_textos:
            log.info(f"Lista: se conserva la ficha existente {ficha.name} (puede estar editada).")
        else:
            meta, motivo = generar_metadatos(cfg, tipo, tema, _material(out, tipo), log, llm)
            limpio = re.sub(r"(?<=\D)\d+$", "", tema).strip()
            fallback_res = tx.primeras_frases(leer_texto(out / "guion_short.txt") or
                                              leer_texto(out / "guion_largo.txt") or "", 3, 350)
            if meta:
                titulo, resumen = meta["titulo"], meta["resumen"]
                hts = normalizar_hashtags(meta["hashtags"], 7, "#Shorts" if tipo == "short" else None)
            else:
                adv.append(f"'{tema}' ({tipo}): título y descripción de respaldo ({motivo}). Revíselos.")
                titulo = sanear_titulo(f"{limpio}: el documental" if tipo == "largo" else f"{limpio} en un minuto")
                resumen, hts = fallback_res, ["#Shorts"] if tipo == "short" else []
            if len(hts) < 3:
                hts = normalizar_hashtags(" ".join(hts) + " " + hashtags_desde_tema(tema) + " #Documental", 7,
                                          "#Shorts" if tipo == "short" else None)
            partes = [resumen]
            if tipo == "largo" and fuentes:
                partes.append("Fuentes consultadas durante la investigación (referencia general):\n" + "\n".join(fuentes))
            if cfg.get("descripcion_aviso"):
                partes.append(cfg["descripcion_aviso"])
            partes.append(" ".join(hts))
            desc = re.sub(r"[<>]", "", "\n\n".join(p for p in partes if p))[:4900]
            _escribir(ficha, _ficha(etq, "Documental largo (horizontal 16:9)" if tipo == "largo"
                                    else "YouTube Short (vertical 9:16)", base, tiene_srt, titulo, desc, hts,
                                    dur, tam_archivo(origen)))
        datos[tipo] = {"base": base, "duracion": dur, "bytes": tam_archivo(origen), "modo": modo}
        n_items += 1
    escribir_json(carpeta / "_datos.json", datos)
    escribir_json(lista_root / "indice.json", indice)
    escribir_indice_general(lista_root)
    log.info(f"Lista de videos actualizada: {carpeta}")
    return {"advertencias": adv, "items": n_items}


def actualizar_calidad(salida_root, lista_root):
    """Copia el resultado del control de calidad de cada tema a su carpeta de la lista (REVISION_ANTES_DE_PUBLICAR.txt)
    y lo refleja en LISTA_GENERAL.txt. Se puede regenerar siempre: no es un archivo para editar."""
    salida_root, lista_root = Path(salida_root), Path(lista_root)
    if not (lista_root / "indice.json").exists():
        return
    indice = leer_json(lista_root / "indice.json", {"temas": {}})
    for seg, reg in indice.get("temas", {}).items():
        q = leer_json(salida_root / seg / "informe_calidad.json")
        carpeta = lista_root / reg["carpeta"]
        if not q or not carpeta.is_dir():
            continue
        L = [f"REVISIÓN ANTES DE PUBLICAR - nivel: {q['nivel'].upper()}", "",
             "Señales automáticas para corregir ANTES de subir. No garantizan ni predicen la decisión de YouTube.", ""]
        L += [f"- {m}" for m in q["motivos"]] or ["- Sin señales de riesgo detectadas automáticamente."]
        if q["recomendaciones"]:
            L += ["", "Qué hacer:"] + [f"* {r}" for r in q["recomendaciones"]]
        L += ["", "Siempre: revise los datos del guion, use solo imágenes y música con derechos claros y responda "
              "con sinceridad la pregunta de contenido sintético/alterado de YouTube Studio."]
        _escribir(carpeta / "REVISION_ANTES_DE_PUBLICAR.txt", "\n".join(L) + "\n")
        datos = leer_json(carpeta / "_datos.json", {}) or {}
        datos["calidad"] = q["nivel"]
        escribir_json(carpeta / "_datos.json", datos)
    escribir_indice_general(lista_root)


def escribir_indice_general(lista_root):
    lista_root = Path(lista_root)
    indice = leer_json(lista_root / "indice.json", {"temas": {}})
    L = ["LISTA DE VIDEOS PARA SUBIR A YOUTUBE (manual)", f"Actualizada: {ahora()}", "",
         "Cómo usar: abra cada carpeta numerada, suba los .mp4 en YouTube Studio y copie título, descripción y",
         "hashtags desde el .txt del mismo nombre. Marque [x] lo que ya subió. Orden recomendado: de arriba a abajo.",
         "Los .mp4 de esta lista son enlaces/copias: borrarlos aquí no borra los originales de su carpeta de salida.",
         "", "-" * 70]
    n, canal_actual, por_canal = 0, None, {}
    items = sorted(indice.get("temas", {}).items(),
                   key=lambda kv: ((kv[1].get("canal") or "").lower(), kv[1].get("n_canal", kv[1]["numero"])))
    for seg, reg in items:
        carpeta = lista_root / reg["carpeta"]
        datos = leer_json(carpeta / "_datos.json", {}) or {}
        filas = []
        for tipo, num, etq, nom, *_ in TIPOS:
            d = datos.get(tipo)
            if not d or tam_archivo(carpeta / f"{d['base']}.mp4") == 0:
                continue
            n += 1
            titulo = _leer_titulo(carpeta / f"{d['base']}.txt") or "(sin título: revise la ficha)"
            filas.append(f"[ ] {n:02d}. [{etq}] {titulo}\n       {reg['carpeta'].replace('/', chr(92))}\\{d['base']}.mp4   "
                         f"({_mmss(d['duracion'])}, {_mb(d['bytes'])})   ficha: {d['base']}.txt")
        if filas:
            nivel = datos.get("calidad")
            marca = f"   [calidad: {nivel.upper()} - ver REVISION_ANTES_DE_PUBLICAR.txt]" if nivel else ""
            canal = reg.get("canal", "")
            if canal and canal != canal_actual:
                L += ["=" * 70, f"CANAL: {canal}", "=" * 70, ""]
            canal_actual = canal
            bloque = [f"TEMA {reg.get('n_canal', reg['numero']):02d}: {reg['tema']}{marca}"] + filas + [""]
            L += bloque
            if canal:
                por_canal.setdefault(seg.split("/")[0], (canal, []))[1].extend(bloque)
    for seg_canal, (nombre, bloque) in por_canal.items():        # una lista de subida por cada canal de referencia
        _escribir(lista_root / seg_canal / "LISTA_CANAL.txt",
                  "\n".join([f"LISTA DE VIDEOS - CANAL DE REFERENCIA: {nombre}", f"Actualizada: {ahora()}", "", "-" * 70, ""]
                            + bloque) + "\n")
    if n == 0:
        L.append("(aún no hay videos listos)")
    _escribir(lista_root / "LISTA_GENERAL.txt", "\n".join(L) + "\n")
    return n

# -*- coding: utf-8 -*-
"""Agente Documental Local: convierte cada subcarpeta de una biblioteca multimedia en un documental
largo (16:9) y un YouTube Short (9:16) en español, usando Ollama, pyttsx3 y FFmpeg. Solo CPU."""
import argparse
import os
import re
import sys
import traceback
from pathlib import Path

from agente import (calidad, guion, investigacion, ollama, produccion, publicacion, subtitulos, video, voz,
                    voz_edge, voz_kokoro)
from agente.utils import (EXT_IMAGEN, EXT_VIDEO, ahora, cargar_config, configurar_log, contar_palabras,
                          dimensiones_imagen, escribir_json, hash_texto, leer_json, leer_texto, nombre_seguro,
                          tam_archivo, validar_config)

AQUI = Path(__file__).resolve().parent
ENTREGABLES = ["guion_largo.txt", "guion_short.txt", "audio_generado.wav", "audio_generado_short.wav",
               "subtitulos_largos.srt", "subtitulos_short.srt", "documental_largo.mp4", "short_vertical.mp4"]
ESTADOS_MALOS = ("incompleto", "error")


class SinMedios(Exception):
    pass


class Contexto:
    def __init__(self, cfg, log, salida):
        self.cfg, self.log, self.salida = cfg, log, salida
        self.ffmpeg = video.buscar_herramienta("ffmpeg", cfg.get("ruta_ffmpeg", ""))
        self.ffprobe = video.buscar_herramienta("ffprobe", cfg.get("ruta_ffprobe", ""))
        self.motor = None  # motor de voz: se crea al necesitarlo


# ----------------------------------------------------------------------------- descubrimiento
def _subcarpetas(p):
    try:
        return sorted((d for d in Path(p).iterdir() if d.is_dir() and not d.name.startswith((".", "_"))),
                      key=lambda x: x.name.lower())
    except OSError:
        return []


def _tiene_medios_directos(p):
    try:
        return any(f.is_file() and f.suffix.lower() in (EXT_IMAGEN | EXT_VIDEO) and not f.name.startswith(".")
                   for f in Path(p).iterdir())
    except OSError:
        return False


def descubrir_temas_y_canales(biblioteca, carpeta_salida):
    """Devuelve [(carpeta_del_tema, canal)] con dos estructuras posibles, detectadas automáticamente:
      * PLANA:  biblioteca\\Tema\\(imágenes y videos)            -> canal = ""
      * CANALES: biblioteca\\Canal\\Tema\\(imágenes y videos)    -> canal = nombre de la carpeta del canal
    Una carpeta es un CANAL cuando no tiene medios sueltos y alguna de sus subcarpetas sí los tiene; en ese caso
    cada subcarpeta es un tema (sus medios se buscan también en sus propias subcarpetas)."""
    res = []
    for p in sorted(Path(biblioteca).iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir() or p.name == carpeta_salida or p.name.startswith((".", "_")):
            continue
        subs = _subcarpetas(p)
        if subs and not _tiene_medios_directos(p) and any(any(descubrir_medios(s)) for s in subs):
            res += [(s, p.name) for s in subs]
        else:
            res.append((p, ""))
    return res


def descubrir_temas(biblioteca, carpeta_salida):
    """Solo las carpetas de los temas (con estructura plana o por canales)."""
    return [t for t, _ in descubrir_temas_y_canales(biblioteca, carpeta_salida)]


def crear_motor(cfg, ctx):
    """Elige la voz. 'kokoro' | 'edge' | 'local' la fuerzan; 'auto' prueba Kokoro (si sus archivos existen), luego
    edge-tts y por último la voz local, avisando SIEMPRE del motivo cuando salta de uno a otro."""
    modo = os.environ.get("AGENTE_MOTOR_VOZ") or cfg.get("motor_voz", "auto")      # la variable manda sobre config.json
    if modo == "kokoro":
        return voz_kokoro.MotorKokoro(cfg, ctx.log)
    avisos = []
    if modo == "auto":
        try:
            voz_kokoro.localizar_archivos(cfg.get("kokoro_modelo") or None, cfg.get("kokoro_voces") or None)
        except voz_kokoro.KokoroError:
            ctx.log.info("Kokoro no está configurado en este equipo (no se encuentran sus archivos): se prueba otra voz.")
        else:
            try:
                return voz_kokoro.MotorKokoro(cfg, ctx.log)
            except voz.VozError as e:
                avisos.append(f"Kokoro no se pudo usar ({str(e).splitlines()[0]})")
                ctx.log.warning(f"{avisos[-1]}. Si usa ejecutar.bat, pruebe ejecutar_kokoro.bat (usa el Python que tiene kokoro-onnx).")
    if modo in ("auto", "edge"):
        try:
            m = voz_edge.MotorEdge(cfg, ctx.log, ctx.ffmpeg)
            m.advertencias[:0] = avisos
            return m
        except voz.VozError as e:
            if modo == "edge":
                raise
            avisos.append(f"edge-tts no disponible ({e})")
            ctx.log.warning(f"{avisos[-1]}.")
    m = voz.MotorVoz(cfg, ctx.log)
    if avisos:
        m.advertencias.insert(0, "Se usó la voz local porque: " + "; ".join(avisos) + ".")
    return m


def tema_completado(out, motor=None):
    """True si el tema ya terminó bien con LA MISMA VOZ: informe 'completado*', los 8 entregables existen y, si se
    indica el motor actual, la voz con la que se narró coincide (si cambia de voz, el tema se rehace)."""
    inf = leer_json(Path(out) / "informe.json", {}) or {}
    ok = str(inf.get("estado", "")).startswith("completado") and all(tam_archivo(Path(out) / f) > 0 for f in ENTREGABLES)
    if ok and motor is not None:
        v = inf.get("voz") or {}
        ok = v.get("motor") == motor.tipo and v.get("voz") == motor.voz
    return ok


def leer_tema(tema_dir):
    """Título del tema: primera línea de 'tema.txt' dentro de la carpeta (si existe)."""
    t = leer_texto(Path(tema_dir) / "tema.txt")
    for linea in (t or "").splitlines():
        if linea.strip():
            return linea.strip()[:150]
    return ""


def asignar_nombres(temas, canales=None):
    """Ruta relativa segura, única y DETERMINISTA por tema (sirve para reanudar): 'tema' en bibliotecas planas y
    'canal/tema' cuando el tema está dentro de la carpeta de un canal."""
    canales = canales or {}
    segs_canal, usados_canal = {}, {}
    for c in dict.fromkeys(c for c in canales.values() if c):
        s = nombre_seguro(c)
        if s in usados_canal and usados_canal[s] != c:
            s = f"{s}_{hash_texto(c)[:4]}"
        usados_canal[s] = c
        segs_canal[c] = s
    usados, res = {}, []
    for t in temas:
        canal = canales.get(t, "")
        seg = nombre_seguro(t.name)
        if (canal, seg) in usados and usados[(canal, seg)] != t.name:
            seg = f"{seg}_{hash_texto(t.name)[:4]}"
        usados[(canal, seg)] = t.name
        res.append((t, f"{segs_canal[canal]}/{seg}" if canal else seg))
    return res


def clave_tema(t, canales):
    """Nombre para mostrar y registrar: 'Canal/Tema' o solo 'Tema'."""
    c = (canales or {}).get(t, "")
    return f"{c}/{t.name}" if c else t.name


def coincide_tema(t, canal, texto):
    a = texto.strip().replace("\\", "/").lower()
    claves = {t.name.lower(), nombre_seguro(t.name)}
    if canal:
        claves |= {f"{canal}/{t.name}".lower(), f"{nombre_seguro(canal)}/{nombre_seguro(t.name)}"}
    return a in claves


def coincide_canal(canal, texto):
    return bool(canal) and (canal.lower() == texto.strip().lower() or nombre_seguro(canal) == nombre_seguro(texto))


def descubrir_medios(tema_dir):
    """Imágenes y videos locales dentro del tema (recursivo), ignorando carpetas ocultas/técnicas."""
    imgs, vids = [], []
    for raiz, dirs, archivos in __import__("os").walk(tema_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))
        for a in sorted(archivos):
            p = Path(raiz) / a
            if a.startswith(".") or tam_archivo(p) == 0:
                continue
            e = p.suffix.lower()
            if e in EXT_IMAGEN:
                imgs.append(p)
            elif e in EXT_VIDEO:
                vids.append(p)
    return imgs, vids


def _repartidos(lista, n):
    if not n or n <= 0 or len(lista) <= n:
        return list(lista)
    paso = len(lista) / n
    return [lista[int(i * paso)] for i in range(n)]


def seleccionar_medios(imgs, vids, cfg):
    """Selecciona (con límites de config; 0 = sin límite) e intercala imágenes y videos."""
    im = _repartidos(imgs, cfg["max_imagenes"])
    vi = _repartidos(vids, cfg["max_videos"])
    mezcla = []
    for k in range(max(len(im), len(vi))):
        if k < len(vi):
            mezcla.append((vi[k], "video"))
        if k < len(im):
            mezcla.append((im[k], "imagen"))
    return mezcla


# ----------------------------------------------------------------------------- procesado de un tema
def consulta_ingles(cfg, tema, log):
    try:
        r = ollama.generar(cfg, "Respondes solo con la consulta pedida, sin explicaciones.",
                           f"Traduce al inglés este tema para una búsqueda académica, en 2 a 5 palabras: {tema}",
                           40, 0.2)
        r = " ".join(r.replace('"', "").split())[:80]
        return r or tema
    except ollama.OllamaError:
        return tema


def procesar_tema(tema_dir, nombre_seg, cfg, ctx, solo_guion=False, canal=""):
    log = ctx.log
    tema = tema_dir.name
    out = ctx.salida / nombre_seg
    out.mkdir(parents=True, exist_ok=True)
    inf = {"tema": tema, "canal": canal, "carpeta_origen": str(tema_dir), "carpeta_salida": str(out), "inicio": ahora(),
           "fin": None, "estado": "error", "imagenes": 0, "videos": 0, "medios_usados": 0,
           "palabras_largo": 0, "palabras_short": 0, "advertencias": [], "errores": [], "etapas": {},
           "voz": None, "video": {}, "notas": []}
    log.info("=" * 70)
    log.info(f"TEMA: {tema}" + (f"   (canal: {canal})" if canal else ""))
    titulo = leer_tema(tema_dir)
    if titulo:
        tema = titulo
        log.info(f"Título del tema (tema.txt): {titulo}")
    elif re.fullmatch(r"[\d\W_]+", tema):
        inf["advertencias"].append(
            f"El nombre de la carpeta '{tema}' no describe ningún tema: el guion saldrá genérico o inventado. "
            "Cree un archivo tema.txt dentro de la carpeta con el título del tema en una línea.")
        log.warning(inf["advertencias"][-1])
    fatal = False
    modelo0 = cfg["modelo_ollama"]
    try:
        imgs, vids = descubrir_medios(tema_dir)
        inf["imagenes"], inf["videos"] = len(imgs), len(vids)
        log.info(f"Medios locales: {len(imgs)} imágenes, {len(vids)} videos.")
        if not imgs and not vids:
            raise SinMedios("La carpeta no contiene imágenes ni videos compatibles.")
        # Calidad de los medios: sin logos/iconos/QR y sin imágenes demasiado pequeñas para un video 720p
        inf["calidad_medios"] = {"descartadas_basura": 0, "bajo_resolucion": 0}
        if cfg.get("filtrar_imagenes_basura", True):
            imgs, basura = calidad.filtrar_basura(imgs)
            inf["calidad_medios"]["descartadas_basura"] = len(basura)
            if basura:
                log.info(f"Excluidas {len(basura)} imágenes que son logos, iconos o códigos QR.")
        minr = int(cfg.get("min_lado_render_px", 0) or 0)
        if minr and len(imgs) > 8:
            grandes = [r for r in imgs if min(dimensiones_imagen(r) or (minr, minr)) >= minr]
            if len(grandes) >= 8:
                inf["calidad_medios"]["bajo_resolucion"] = len(imgs) - len(grandes)
                imgs = grandes
        inf["calidad_medios"]["licencias"] = calidad.riesgo_licencias(imgs)
        if inf["calidad_medios"]["licencias"]["proporcion_agencia"] >= 0.3:
            inf["advertencias"].append(
                "Muchas imágenes parecen de agencias o medios de noticias (Getty, CNN, BBC, AFP...): riesgo de "
                "copyright y de 'contenido reutilizado'. Use imágenes propias, libres o con licencia.")
        if not imgs and not vids:
            raise SinMedios("Tras excluir logos/iconos no queda ninguna imagen ni video utilizable.")
        seleccion = seleccionar_medios(imgs, vids, cfg)
        inf["medios_usados"] = len(seleccion)

        # 1. Investigación (solo texto)
        notas = ""
        ruta_inv = out / "investigacion.json"
        if cfg["investigacion_web"]:
            notas, adv = investigacion.investigar(cfg, tema, ruta_inv, log, lambda: consulta_ingles(cfg, tema, log))
            inf["advertencias"] += adv
            inf["etapas"]["investigacion"] = "ok"
        else:
            escribir_json(ruta_inv, {"tema": tema, "fecha": ahora(), "fuentes": [], "desactivada": True,
                                     "nota": "Investigación web desactivada en config.json."})
            inf["etapas"]["investigacion"] = "desactivada"
            inf["advertencias"].append("Investigación web desactivada: el guion usa conocimiento general del "
                                       "modelo, sin fuentes verificadas.")

        # 2. Guion largo por secciones
        aporte = leer_texto(tema_dir / "aporte.txt") or ""
        g = guion.generar_guion_largo(cfg, tema, out, notas, log, aporte=aporte)
        inf["palabras_largo"] = g["palabras"]
        inf["ampliaciones"] = g["ampliaciones"]
        inf["advertencias"] += g["advertencias"]
        if cfg["modelo_ollama"] != modelo0:
            inf["advertencias"].append(f"Se cambió automáticamente del modelo '{modelo0}' a "
                                       f"'{cfg['modelo_ollama']}' porque el primero devolvía razonamiento.")
        largo_ok = g["completo"]
        inf["etapas"]["guion_largo"] = "ok" if largo_ok else "incompleto"
        if largo_ok:
            guion_largo = leer_texto(out / "guion_largo.txt")
            log.info(f"Guion largo completo: {g['palabras']} palabras (guardado en guion_largo.txt).")
        else:
            inf["errores"].append("El guion largo no está completo (ver advertencias): no se crea el documental "
                                  "largo. Se conserva el borrador; vuelva a ejecutar para reanudar.")
            guion_largo = guion.ensamblar(out / "partes_guion")   # borrador: solo material para el Short

        # 3. Guion Short (independiente: un fallo del largo no impide el Short, ni al revés)
        short_ok = False
        if guion_largo.strip():
            s = guion.generar_short(cfg, tema, out, guion_largo, log)
            inf["palabras_short"] = s["palabras"]
            inf["advertencias"] += s["advertencias"]
            short_ok = s["completo"]
        inf["etapas"]["guion_short"] = "ok" if short_ok else "incompleto"
        if not short_ok:
            inf["errores"].append("No se pudo generar el guion Short: no se crea el Short vertical.")
        if solo_guion or not (largo_ok or short_ok):
            return _cerrar(inf, out, ctx, solo_guion)

        # 4-5. Narración y subtítulos (cada video por separado: si uno falla, el otro continúa)
        log.info("Iniciando narración (motor de voz)...")
        if ctx.motor is None:
            ctx.motor = crear_motor(cfg, ctx)
            log.info(f"Voz: motor={ctx.motor.tipo}, voz='{ctx.motor.voz}', español={ctx.motor.voz_es}")
        metas = {}
        for clave, ok, texto_fn, wav, srt in (
                ("largo", largo_ok, lambda: guion_largo, "audio_generado.wav", "subtitulos_largos.srt"),
                ("short", short_ok, lambda: leer_texto(out / "guion_short.txt"), "audio_generado_short.wav",
                 "subtitulos_short.srt")):
            if not ok:
                continue
            try:
                esc = cfg.get("modo_video", "escenas") == "escenas"
                mc = int(cfg.get("escena_max_caracteres_short" if clave == "short" else "escena_max_caracteres", 170)) \
                    if esc else 350
                meta = voz.sintetizar_texto(ctx.motor, texto_fn(), out / wav, log, clave, mc, esc)
                if clave == "largo" and cfg.get("duracion_objetivo_min"):
                    limite = float(cfg["duracion_objetivo_min"]) * 60
                    for _ in range(2):                         # medir la duración REAL y recortar si se pasa
                        if meta["duracion"] <= limite:
                            break
                        palabras = contar_palabras(guion_largo)
                        wpm_real = palabras / meta["duracion"] * 60
                        nuevo_max = int(limite * 0.985 * wpm_real / 60)
                        log.warning(f"El documental dura {int(meta['duracion'] // 60)}:{int(meta['duracion'] % 60):02d} y "
                                    f"el máximo es {int(limite // 60)}:00: se recorta el guion a ~{nuevo_max} palabras.")
                        guion.guardar_ritmo(out, wpm_real)
                        texto2, _ = guion.reescribir_guion_largo(out, cfg, nuevo_max)
                        if contar_palabras(texto2) >= palabras:
                            break
                        guion_largo = texto2
                        meta = voz.sintetizar_texto(ctx.motor, guion_largo, out / wav, log, clave, mc, esc)
                    inf["palabras_largo"] = contar_palabras(guion_largo)
                    d = meta["duracion"]
                    if d > limite + 1:
                        inf["advertencias"].append(f"El documental dura {int(d // 60)}:{int(d % 60):02d}, por encima de "
                                                   f"los {cfg['duracion_objetivo_min']} min pedidos (no se pudo recortar más "
                                                   "sin bajar del mínimo de palabras).")
                    else:
                        log.info(f"Duración del documental largo: {int(d // 60)}:{int(d % 60):02d} "
                                 f"(máximo {cfg['duracion_objetivo_min']}:00).")
                n = subtitulos.generar_srt(meta["trozos"], out / srt)
                prob = subtitulos.validar_srt(out / srt, meta["duracion"])
                if prob:
                    inf["advertencias"].append(f"{srt}: {'; '.join(prob)}")
                log.info(f"{srt}: {n} bloques.")
                metas[clave] = meta
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                inf["errores"].append(f"Narración o subtítulos del {clave}: {e}")
                log.error(f"{tema}: narración/subtítulos del {clave} fallaron: {e}")
        if metas:
            inf["voz"] = {"motor": ctx.motor.tipo, "voz": ctx.motor.voz, "voz_espanola": ctx.motor.voz_es,
                          **{f"duracion_{k}_seg": round(m["duracion"], 1) for k, m in metas.items()}}
            inf["advertencias"] += [a for a in ctx.motor.advertencias if a not in inf["advertencias"]]
            inf["etapas"]["narracion"] = "ok" if len(metas) == int(largo_ok) + int(short_ok) else "parcial"
            inf["notas"].append("Subtítulos: sincronización aproximada, anclada a la duración real de cada trozo "
                                "de audio y repartida por caracteres dentro del trozo. No verificada fonema a fonema.")
        if not metas:
            return _cerrar(inf, out, ctx, solo_guion)

        # 6. Video
        if not ctx.ffmpeg:
            inf["errores"].append("FFmpeg no encontrado: no se generaron los MP4. Instálelo (ver README).")
            inf["etapas"]["video"] = "omitida"
            return _cerrar(inf, out, ctx, solo_guion)
        log.info("Preparando medios para el montaje de video...")
        items, avisos = video.preparar_medios(seleccion, ctx.ffprobe, ctx.ffmpeg)
        inf["advertencias"] += avisos
        if not items:
            raise RuntimeError("Ningún medio local es utilizable por FFmpeg.")
        for it in items:
            it["dim"] = dimensiones_imagen(it["ruta"]) if it["tipo"] == "imagen" else None
        ilegibles = [it for it in items if it["tipo"] == "imagen" and it["dim"] is None]
        if ilegibles and len(items) > len(ilegibles):     # archivos con extensión de imagen que no lo son
            items = [it for it in items if it not in ilegibles]
            inf["advertencias"].append(f"{len(ilegibles)} archivo(s) con extensión de imagen no son imágenes válidas "
                                       f"y se excluyeron: {', '.join(Path(i['ruta']).name for i in ilegibles[:5])}")
        escenas_modo = cfg.get("modo_video", "escenas") == "escenas"

        def ya_vigente(destino, wav_p, meta):
            return (destino.exists() and tam_archivo(wav_p) > 0 and destino.stat().st_mtime >= wav_p.stat().st_mtime
                    and not video.verificar_mp4(destino, ctx.ffprobe, ctx.ffmpeg, meta["duracion"]))
        videos_esc = produccion.producir_videos(inf, out, cfg, ctx, metas, items, tema, AQUI, log, ya_vigente) \
            if escenas_modo else 0
        trabajos = [] if escenas_modo else [("largo", "documental_largo.mp4", cfg["resolucion_horizontal"], "subtitulos_largos.srt",
                     cfg["duracion_escena_seg"], "audio_generado.wav"),
                    ("short", "short_vertical.mp4", cfg["resolucion_vertical"], "subtitulos_short.srt",
                     cfg["duracion_escena_short_seg"], "audio_generado_short.wav")]
        videos_ok = videos_esc
        for clave, nombre, fmt, srt, base, wav in trabajos:
            if clave not in metas:
                continue
            meta, destino = metas[clave], out / nombre
            try:
                vigente = (destino.exists() and tam_archivo(out / wav) > 0
                           and destino.stat().st_mtime >= (out / wav).stat().st_mtime
                           and not video.verificar_mp4(destino, ctx.ffprobe, ctx.ffmpeg, meta["duracion"]))
                if vigente:
                    log.info(f"{nombre} ya existe y es válido: se conserva.")
                    inf["video"][nombre] = {"reutilizado": True}
                    videos_ok += 1
                    continue
                st = video.crear_video(items, out / wav, out / srt, destino, fmt, meta["duracion"], float(base),
                                       cfg, ctx.ffmpeg, ctx.ffprobe, log)
                inf["video"][nombre] = st
                videos_ok += 1
                if st["reutilizacion"]:
                    inf["advertencias"].append(
                        f"{nombre}: hay {st['medios_unicos']} medios únicos y {st['escenas']} escenas; los medios se "
                        "reutilizan varias veces para cubrir la narración.")
                if st["medios_omitidos"]:
                    inf["advertencias"] += [f"Medio omitido en {nombre}: {x}" for x in st["medios_omitidos"]]
            except KeyboardInterrupt:
                raise
            except Exception as e:  # noqa: BLE001
                inf["errores"].append(f"No se pudo crear {nombre}: {e}")
                log.error(f"{tema}: {nombre} falló: {e}")
        inf["etapas"]["video"] = "ok" if videos_ok == len(metas) else ("parcial" if videos_ok else "fallo")

        # 7. Lista de videos para subir a mano (no esencial: un fallo aquí solo genera advertencia)
        if videos_ok:
            try:
                r = publicacion.preparar_tema(cfg, tema, nombre_seg, out, ctx.salida / cfg["carpeta_lista_videos"], log,
                                              canal=canal)
                inf["advertencias"] += r["advertencias"]
                inf["etapas"]["lista_videos"] = "ok"
            except Exception as e:  # noqa: BLE001
                inf["advertencias"].append(f"No se pudo preparar la lista de subida: {e}")
                log.warning(f"Lista de videos: {e}")
    except SinMedios as e:
        inf["errores"].append(str(e))
        log.error(f"{tema}: {e}")
        fatal = True
    except KeyboardInterrupt:
        inf["errores"].append("Interrumpido por el usuario; ejecute de nuevo para reanudar.")
        _cerrar(inf, out, ctx, solo_guion, interrumpido=True)
        raise
    except Exception as e:  # noqa: BLE001 - un tema que falla no debe detener el resto
        inf["errores"].append(f"{type(e).__name__}: {e}")
        log.error(f"{tema}: {e}\n{traceback.format_exc()}")
        fatal = True
    return _cerrar(inf, out, ctx, solo_guion, fatal=fatal)


def _cerrar(inf, out, ctx, solo_guion, fatal=False, interrumpido=False):
    faltan = [f for f in ENTREGABLES[:2 if solo_guion else None] if tam_archivo(out / f) == 0]
    inf["archivos"] = {f: {"existe": tam_archivo(out / f) > 0, "bytes": tam_archivo(out / f)} for f in ENTREGABLES}
    if interrumpido:
        inf["estado"] = "incompleto"
    elif any("No hay imágenes" in e or "no contiene imágenes" in e for e in inf["errores"]):
        inf["estado"] = "error"
    elif fatal:
        inf["estado"] = "incompleto" if tam_archivo(out / "guion_largo_en_progreso.txt") > 0 else "error"
    elif inf["errores"] or faltan or solo_guion:
        inf["estado"] = "incompleto"
        if solo_guion and not faltan:
            inf["notas"].append("Modo --solo-guion: no se generaron narración ni video, por diseño.")
    else:
        inf["estado"] = "completado_con_advertencias" if inf["advertencias"] else "completado"
    if faltan and not solo_guion:
        inf["errores"].append(f"Faltan entregables: {', '.join(faltan)}")
    inf["fin"] = ahora()
    escribir_json(out / "informe.json", inf)
    ctx.log.info(f"Estado de '{inf['tema']}': {inf['estado']} "
                 f"({inf['palabras_largo']} palabras largo, {inf['palabras_short']} short)")
    return inf


# ----------------------------------------------------------------------------- comprobación del entorno
def preparar_windows(log):
    """Evita que el equipo se suspenda y que un clic en la consola (QuickEdit) pause el proceso."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetThreadExecutionState(ctypes.c_uint(0x80000001))   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        h = k.GetStdHandle(ctypes.c_int(-10))
        modo = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(modo)):
            k.SetConsoleMode(h, ctypes.c_uint32((modo.value | 0x80) & ~0x40))   # sin QuickEdit
        log.info("Suspensión del equipo bloqueada y selección de texto en consola desactivada mientras trabaja.")
    except Exception as e:  # noqa: BLE001
        log.warning(f"No se pudo bloquear la suspensión del equipo: {e}")


def mostrar_estado(temas, nombres, salida, canales=None):
    """Tabla de estado de todas las carpetas, leyendo los informe.json ya generados (no procesa nada)."""
    cuenta, canal_actual = {}, None
    for t in temas:
        out = salida / nombres[t]
        canal = (canales or {}).get(t, "")
        if canal != canal_actual and canal:
            print(f"\n=== CANAL: {canal} ===")
        canal_actual = canal
        inf = leer_json(out / "informe.json")
        if not inf:
            estado = "sin procesar"
            print(f"- {t.name}: sin procesar")
        else:
            estado = inf.get("estado", "?")
            faltan = [f for f in ENTREGABLES if tam_archivo(out / f) == 0]
            print(f"- {t.name}: {estado} | guion largo {inf.get('palabras_largo', 0)} pal. | "
                  f"short {inf.get('palabras_short', 0)} pal. | videos: "
                  f"{'largo' if tam_archivo(out / 'documental_largo.mp4') else '-'} / "
                  f"{'short' if tam_archivo(out / 'short_vertical.mp4') else '-'}  ({inf.get('fin', '')})")
            for e in (inf.get("errores") or [])[:3]:
                print(f"     motivo: {e[:220]}")
            if faltan and not estado.startswith("completado"):
                print(f"     faltan: {', '.join(faltan)}")
        cuenta[estado] = cuenta.get(estado, 0) + 1
    print("\nResumen: " + ", ".join(f"{n} {e}" for e, n in sorted(cuenta.items())) + f" (de {len(temas)} carpetas)")


def resumen_por_canal(salida):
    """Escribe RESUMEN_CANAL.txt dentro de la carpeta de cada canal y RESUMEN_POR_CANAL.txt en la raíz de salida."""
    salida = Path(salida)
    general = ["RESUMEN POR CANAL", f"Actualizado: {ahora()}", ""]
    n_canales = 0
    for d in sorted((p for p in salida.iterdir() if p.is_dir() and not p.name.startswith("_")), key=lambda x: x.name.lower()):
        if (d / "informe.json").exists():
            continue                                           # es un tema de estructura plana
        temas = [t for t in sorted(d.iterdir(), key=lambda x: x.name.lower()) if t.is_dir() and (t / "informe.json").exists()]
        if not temas:
            continue
        n_canales += 1
        infs = [(t, leer_json(t / "informe.json", {}) or {}) for t in temas]
        nombre = next((i.get("canal") for _, i in infs if i.get("canal")), d.name)
        ok = sum(1 for _, i in infs if str(i.get("estado", "")).startswith("completado"))
        L = [f"CANAL: {nombre}", f"Temas: {len(infs)}  |  completados: {ok}  |  pendientes o con problemas: {len(infs) - ok}",
             f"Actualizado: {ahora()}", "-" * 70]
        for t, i in infs:
            v = i.get("voz") or {}
            dur = lambda k: (f"{int(v[k] // 60)}:{int(v[k] % 60):02d}" if v.get(k) else "-")  # noqa: E731
            motivo = f" | {i['errores'][0][:100]}" if i.get("errores") else ""
            L.append(f"- {i.get('tema', t.name)}: {i.get('estado', '?')} | largo {dur('duracion_largo_seg')} | "
                     f"short {dur('duracion_short_seg')} | carpeta: {t.name}{motivo}")
        publicacion._escribir(d / "RESUMEN_CANAL.txt", "\n".join(L) + "\n")
        general += [f"{nombre}: {len(infs)} temas, {ok} completados"]
    if n_canales:
        publicacion._escribir(salida / "RESUMEN_POR_CANAL.txt", "\n".join(general) + "\n")
    return n_canales


def probar_voz(cfg, ctx):
    """Escribe muestras_voz/muestra_edge.wav y muestra_local.wav para comprobar CON LOS OÍDOS qué voz se usa."""
    carpeta = AQUI / "muestras_voz"
    carpeta.mkdir(exist_ok=True)
    frase = ("Hola. Esta es una prueba de voz para tus documentales. Si escuchas una voz natural, con entonación "
             "humana, es la voz neuronal de Microsoft Edge. Si suena robótica, es la voz básica de Windows.")
    fabricas = [("edge", lambda: voz_edge.MotorEdge(cfg, ctx.log, ctx.ffmpeg)),
                ("kokoro", lambda: voz_kokoro.MotorKokoro(cfg, ctx.log)), ("local", lambda: voz.MotorVoz(cfg, ctx.log))]
    ok = {}
    for nombre, fab in fabricas:
        try:
            m = fab()
            destino = carpeta / f"muestra_{nombre}.wav"
            m.sintetizar(frase, destino)
            print(f"[OK] {nombre}: motor={m.tipo}, voz='{m.voz}' -> {destino} ({voz.duracion_wav(destino):.1f} s)")
            ok[nombre] = True
        except Exception as e:  # noqa: BLE001
            print(f"[NO DISPONIBLE] {nombre}: {e}")
            ok[nombre] = False
    modo = cfg.get("motor_voz", "auto")
    usa = ("kokoro" if modo == "kokoro" else "edge-tts" if ok.get("edge") and modo in ("auto", "edge") else
           "kokoro" if ok.get("kokoro") and modo == "auto" else "voz local")
    print(f"\nCon motor_voz='{modo}' el agente usará: {usa}.")
    if not ok.get("edge"):
        print("Para activar edge-tts: ejecute instalar.bat (instala el paquete edge-tts) y compruebe su conexión a Internet.")
    print(f"Escuche los archivos de {carpeta} para comparar.")
    return 0 if ok.get("edge") or ok.get("local") else 1


def comprobar_entorno(cfg, ctx):
    log = ctx.log
    ok = True
    log.info(f"Python {sys.version.split()[0]}")
    try:
        bien, lista = ollama.modelo_disponible(cfg)
        log.info(f"Ollama responde en {cfg['ollama_url']}. Modelos: {', '.join(lista) or '(ninguno)'}")
        if bien:
            log.info(f"Modelo '{cfg['modelo_ollama']}' instalado.")
        else:
            log.error(f"El modelo '{cfg['modelo_ollama']}' NO está instalado. Cámbielo en config.json "
                      f"(no se descarga nada automáticamente).")
            ok = False
    except ollama.OllamaError as e:
        log.error(f"Ollama: {e}")
        ok = False
    for nombre, ruta in (("ffmpeg", ctx.ffmpeg), ("ffprobe", ctx.ffprobe)):
        if ruta:
            log.info(f"{nombre}: {ruta}")
        else:
            log.error(f"{nombre} no encontrado en PATH ni en config.json.")
            ok = ok and nombre == "ffprobe"
    try:
        import pyttsx3  # noqa: F401
        m = crear_motor(cfg, ctx)
        log.info(f"Voz: motor={m.tipo}, voz='{m.voz}', español={m.voz_es}")
        for a in m.advertencias:
            log.warning(a)
    except Exception as e:  # noqa: BLE001
        log.warning(f"pyttsx3 no disponible: {e}")
        try:
            m = voz.MotorVoz(cfg, log)
            log.info(f"Voz alternativa: motor={m.tipo}")
        except voz.VozError as e2:
            log.error(f"Sin motor de voz: {e2}")
            ok = False
    return ok


# ----------------------------------------------------------------------------- programa principal
def main(argv=None):
    ap = argparse.ArgumentParser(description="Agente Documental Local (Ollama + pyttsx3 + FFmpeg)")
    ap.add_argument("--config", default=str(AQUI / "config.json"), help="ruta de config.json")
    ap.add_argument("--biblioteca", help="ruta de la biblioteca (sustituye a config.json)")
    ap.add_argument("--tema", help="procesar solo la subcarpeta con este nombre")
    ap.add_argument("--listar", action="store_true", help="listar temas y medios, sin generar nada")
    ap.add_argument("--comprobar", action="store_true", help="comprobar Ollama, modelo, FFmpeg y voz")
    ap.add_argument("--solo-guion", action="store_true", help="generar solo guiones (sin voz ni video)")
    ap.add_argument("--probar-voz", action="store_true",
                    help="genera muestras de audio de la voz edge-tts y de la voz local para que las escuche")
    ap.add_argument("--canal", metavar="NOMBRE", help="procesar solo los temas de la carpeta de este canal")
    ap.add_argument("--calidad", action="store_true",
                    help="solo evalúa la calidad/riesgo de monetización de los temas ya generados (no procesa nada)")
    ap.add_argument("--estado", action="store_true",
                    help="muestra el estado de cada carpeta (completado, incompleto, error, sin procesar) y por qué")
    ap.add_argument("--max-temas", type=int, metavar="N",
                    help="procesar como máximo N temas pendientes en esta ejecución (los ya completados se omiten)")
    ap.add_argument("--rehacer", action="store_true",
                    help="no omitir los temas ya completados (se reanuda igualmente lo ya guardado)")
    ap.add_argument("--preparar-lista", action="store_true",
                    help="solo crear/actualizar la carpeta de lista de videos para subir a mano (con lo ya generado)")
    ap.add_argument("--forzar-textos", action="store_true",
                    help="con --preparar-lista: rehacer también las fichas .txt ya existentes (sobrescribe ediciones)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass

    cfg = cargar_config(args.config)
    assets = Path(cfg.get("assets_carpeta", "assets"))
    cfg["assets_carpeta"] = str(assets if assets.is_absolute() else AQUI / assets)
    if args.biblioteca:
        cfg["ruta_biblioteca"] = args.biblioteca
    errs = validar_config(cfg)
    if errs:
        print("Errores en config.json:\n - " + "\n - ".join(errs))
        return 2

    biblio = Path(cfg["ruta_biblioteca"]) if cfg["ruta_biblioteca"] else None
    if args.probar_voz:
        return probar_voz(cfg, Contexto(cfg, configurar_log(), Path(".")))
    if args.comprobar:
        log = configurar_log()
        return 0 if comprobar_entorno(cfg, Contexto(cfg, log, Path("."))) else 1
    if not biblio or not biblio.is_dir():
        print("La 'ruta_biblioteca' de config.json no existe o está vacía. Edite config.json (ver README.txt) "
              "o use --biblioteca \"C:\\ruta\\a\\su\\Biblioteca\".")
        return 2

    salida = biblio / cfg["carpeta_salida"]
    pares = descubrir_temas_y_canales(biblio, cfg["carpeta_salida"])
    temas = [t for t, _ in pares]
    canales = {t: c for t, c in pares}
    nombres = dict(asignar_nombres(temas, canales))
    if args.listar:
        actual = None
        for t in temas:
            if canales[t] != actual and canales[t]:
                print(f"\n=== CANAL: {canales[t]} ===")
            actual = canales[t]
            i, v = descubrir_medios(t)
            print(f"{'  ' if canales[t] else ''}{t.name}: {len(i)} imágenes, {len(v)} videos")
        return 0
    if args.estado:
        mostrar_estado(temas, nombres, salida, canales)
        return 0
    if args.calidad:
        if not salida.is_dir():
            print("Aún no hay resultados que evaluar.")
            return 2
        log = configurar_log()
        calidad.evaluar_lote(salida, cfg, log)
        publicacion.actualizar_calidad(salida, salida / cfg["carpeta_lista_videos"])
        print(f"Resumen en: {salida / 'RESUMEN_CALIDAD.txt'}")
        return 0
    if args.canal:
        temas = [t for t in temas if coincide_canal(canales[t], args.canal)]
        if not temas:
            disponibles = sorted({c for c in canales.values() if c})
            print(f"No existe el canal '{args.canal}' en la biblioteca." +
                  (f" Canales: {', '.join(disponibles)}." if disponibles else " La biblioteca no tiene carpetas de canales."))
            return 2
    if args.tema:
        temas = [t for t in temas if coincide_tema(t, canales[t], args.tema)]
        if not temas:
            print(f"No existe el tema '{args.tema}' en la biblioteca (con canal use 'Canal/Tema').")
            return 2
    if not temas:
        print("La biblioteca no contiene subcarpetas temáticas.")
        return 2

    salida.mkdir(parents=True, exist_ok=True)
    log = configurar_log(salida / "agente.log")
    ctx = Contexto(cfg, log, salida)
    if args.preparar_lista:
        total = 0
        for t in temas:
            r = publicacion.preparar_tema(cfg, t.name, nombres[t], salida / nombres[t],
                                          salida / cfg["carpeta_lista_videos"], log, forzar_textos=args.forzar_textos,
                                          canal=canales[t])
            total += r["items"]
            for a in r["advertencias"]:
                log.warning(a)
        log.info(f"Lista lista: {total} videos. Abra {salida / cfg['carpeta_lista_videos'] / 'LISTA_GENERAL.txt'}")
        return 0
    n_canales = len({c for c in canales.values() if c})
    log.info(f"Biblioteca: {biblio} | " + (f"Canales: {n_canales} | " if n_canales else "") +
             f"Temas: {len(temas)} | Modelo: {cfg['modelo_ollama']}")
    preparar_windows(log)
    try:
        bien, lista = ollama.modelo_disponible(cfg)
        cfg["_modelos_instalados"] = lista
        if not bien:
            log.error(f"El modelo '{cfg['modelo_ollama']}' no está instalado en Ollama (instalados: "
                      f"{', '.join(lista)}). Corrija 'modelo_ollama' en config.json. No se descarga nada.")
            return 3
    except ollama.OllamaError as e:
        log.error(f"{e}\nInicie Ollama (aplicación o 'ollama serve') y vuelva a ejecutar.")
        return 3
    if not ctx.ffmpeg and not args.solo_guion:
        log.warning("FFmpeg no encontrado: se generarán guiones, narración y subtítulos, pero no los MP4.")
    if not args.solo_guion:
        # La voz se prepara AL PRINCIPIO: si Kokoro no carga, se avisa ya y no tras 15 minutos de guion.
        try:
            ctx.motor = crear_motor(cfg, ctx)
            log.info(f"Voz: motor={ctx.motor.tipo}, voz='{ctx.motor.voz}', español={ctx.motor.voz_es}")
        except voz.VozError as e:
            log.error(f"No se pudo preparar la voz: {e}")
            return 4

    pendientes = [t for t in temas if args.rehacer or args.solo_guion or not tema_completado(salida / nombres[t], ctx.motor)]
    ya = len(temas) - len(pendientes)
    if ya:
        log.info(f"{ya} tema(s) ya completado(s) con esta voz: se omiten (use --rehacer para volver a revisarlos).")
    cambiados = [t for t in temas if t in pendientes and tema_completado(salida / nombres[t])]
    if cambiados and ctx.motor is not None:
        log.info(f"{len(cambiados)} tema(s) ya estaban terminados con otra voz: se rehacen con "
                 f"{ctx.motor.tipo} ({ctx.motor.voz}).")
    restantes = 0
    if args.max_temas and len(pendientes) > args.max_temas:
        restantes = len(pendientes) - args.max_temas
        pendientes = pendientes[:args.max_temas]
    log.info(f"Temas a procesar ahora: {len(pendientes)}" + (f" (quedarán {restantes} pendientes)" if restantes else ""))
    temas = pendientes

    registro_ruta = salida / "registro_agente.json"
    registro = leer_json(registro_ruta, {"version": 1, "ejecuciones": [], "ultimo_estado_por_tema": {}})
    ejec = {"inicio": ahora(), "fin": None, "biblioteca": str(biblio), "modelo": cfg["modelo_ollama"], "temas": {}}
    registro.setdefault("ejecuciones", []).append(ejec)
    registro["ejecuciones"] = registro["ejecuciones"][-50:]
    registro.setdefault("ultimo_estado_por_tema", {})

    interrumpido = False
    for i, t in enumerate(temas, 1):
        seg = nombres[t]
        clave = clave_tema(t, canales)
        log.info(f"##### Tema {i} de {len(temas)}: {clave}")
        try:
            inf = procesar_tema(t, seg, cfg, ctx, args.solo_guion, canal=canales[t])
        except KeyboardInterrupt:
            interrumpido = True
            ejec["temas"][clave] = {"estado": "incompleto", "nota": "interrumpido"}
            break
        res = {k: inf[k] for k in ("estado", "inicio", "fin", "imagenes", "videos", "palabras_largo",
                                   "palabras_short")}
        res.update(errores=inf["errores"], advertencias=len(inf["advertencias"]), carpeta=seg)
        ejec["temas"][clave] = res
        registro["ultimo_estado_por_tema"][clave] = res
        escribir_json(registro_ruta, registro)
    ejec["fin"] = ahora()
    escribir_json(registro_ruta, registro)
    try:
        calidad.evaluar_lote(salida, cfg, log)
        publicacion.actualizar_calidad(salida, salida / cfg["carpeta_lista_videos"])
    except Exception as e:  # noqa: BLE001
        log.warning(f"No se pudo evaluar la calidad del lote: {e}")
    try:
        if resumen_por_canal(salida):
            log.info(f"Resumen por canal: {salida / 'RESUMEN_POR_CANAL.txt'}")
    except Exception as e:  # noqa: BLE001
        log.warning(f"No se pudo escribir el resumen por canal: {e}")

    log.info("=" * 70)
    log.info("RESUMEN FINAL")
    for n, r in ejec["temas"].items():
        motivo = f" - {r['errores'][0][:160]}" if r.get("errores") else ""
        log.info(f"  {n}: {r['estado']}{motivo}")
    ok = sum(1 for r in ejec["temas"].values() if r["estado"].startswith("completado"))
    log.info(f"Temas completados en esta ejecución: {ok} de {len(temas)}. Detalle en el informe.json de cada uno.")
    if restantes:
        log.info(f"Quedan {restantes} tema(s) pendiente(s): vuelva a ejecutar para continuar con los siguientes.")
    if interrumpido:
        log.warning("Ejecución interrumpida. Vuelva a ejecutar para reanudar.")
        return 130
    return 1 if any(r["estado"] in ESTADOS_MALOS for r in ejec["temas"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())

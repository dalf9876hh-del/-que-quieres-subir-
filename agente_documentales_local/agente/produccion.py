# -*- coding: utf-8 -*-
"""Orquesta el montaje por escenas de un tema: plan (VOICEOVER/VISUAL_PROMPT/SFX), tiempos por palabra, subtítulos
ASS, imágenes opcionales de ComfyUI y render con zooms, SFX y música."""
from pathlib import Path

from . import alineacion, comfy, escenas, render, subs_ass, texto as tx, video
from .utils import dimensiones_imagen, hash_texto, leer_texto, tam_archivo
from .voz import PAUSA_SEG

NOMBRES = {"largo": ("documental_largo.mp4", "audio_generado.wav", "subtitulos_largos.ass"),
           "short": ("short_vertical.mp4", "audio_generado_short.wav", "subtitulos_short.ass")}


def prefijos_de_seccion(out):
    """Primeras palabras de cada PÁRRAFO del guion largo: la escena que abre un párrafo lleva un whoosh."""
    texto = leer_texto(Path(out) / "guion_largo.txt") or ""
    pref = []
    for par in [p for p in texto.split("\n\n") if p.strip()]:
        frases = tx.dividir_frases(par)
        if frases:
            pref.append(frases[0][:35])
    return tuple(pref)


def palabras_por_escena(meta, usar_whisper, wav, cfg, log):
    """[(inicio, [palabras con tiempos relativos])] + método usado. Whisper se aplica sobre el audio completo."""
    trozos = meta["trozos"]
    n = len(trozos)
    inicios, off = [], 0.0
    for t in trozos:
        inicios.append(off)
        off += t["duracion"]
    if usar_whisper:
        try:
            textos = " ".join(t["texto"] for t in trozos)
            glob = alineacion.alinear_con_guion(textos, alineacion.transcribir_palabras(wav, cfg.get("whisper_modelo", "base")),
                                                meta["duracion"])
            if glob:
                res, k = [], 0
                for i, t in enumerate(trozos):
                    nt = len(alineacion.tokens(t["texto"]))
                    rel = [{"t": w["t"], "ini": w["ini"] - inicios[i], "fin": w["fin"] - inicios[i]}
                           for w in glob[k:k + nt]]
                    k += nt
                    res.append((inicios[i], rel))
                return res, "whisper"
        except Exception as e:  # noqa: BLE001
            log.warning(f"Whisper no se pudo usar ({e}); se usan tiempos por escena.")
    metodos, res = {}, []
    for i, t in enumerate(trozos):
        dur_voz = max(0.15, t["duracion"] - (PAUSA_SEG if i < n - 1 else 0.0))
        pal, m = alineacion.palabras_de_escena(t["texto"], dur_voz, t.get("limites"))
        metodos[m] = metodos.get(m, 0) + 1
        res.append((inicios[i], pal))
    return res, "+".join(sorted(metodos))


def producir_videos(inf, out, cfg, ctx, metas, items, tema, base_dir, log, ya_vigente):
    """Crea los MP4 con el montaje por escenas. Devuelve el nº de videos correctos."""
    semilla = int(hash_texto(tema)[:8], 16)
    formatos = {"largo": cfg["resolucion_horizontal"], "short": cfg["resolucion_vertical"]}
    plans = {}
    comfy_on = bool((cfg.get("comfyui") or {}).get("activar"))
    for clave, meta in metas.items():
        textos = [t["texto"] for t in meta["trozos"]]
        if cfg.get("escenas_con_llm") or comfy_on:
            plans[clave], av = escenas.planificar_con_llm(cfg, tema, textos, log)
            inf["advertencias"] += av
        else:
            plans[clave] = escenas.plan_deterministico(
                textos, tema, prefijos_de_seccion(out) if clave == "largo" else (), ritmo=None if clave == "largo" else 3)

    extra = []
    if comfy_on:
        base = plans.get("largo") or next(iter(plans.values()))
        paso = max(1, len(base) // max(1, int(cfg["comfyui"].get("max_por_tema", 6))))
        prompts = [p["visual_prompt"] for p in base[::paso]]
        for r in comfy.generar_imagenes(cfg["comfyui"], prompts, Path(out) / "imagenes_generadas", log, base_dir):
            extra.append({"ruta": str(r), "tipo": "imagen", "mtime": int(Path(r).stat().st_mtime), "dur": None,
                          "dim": dimensiones_imagen(r)})
        inf["comfyui_imagenes"] = len(extra)
    medios = list(items) + extra

    n_img = sum(1 for m in medios if m["tipo"] == "imagen")
    esc_largo = len(metas["largo"]["trozos"]) if "largo" in metas else 0
    if esc_largo and n_img < 20:
        inf["advertencias"].append(
            f"Solo {n_img} imágenes para {esc_largo} escenas: cada una se repetirá unas {esc_largo // max(1, n_img)} "
            "veces (con zoom y encuadre distintos). Para un video de ~14 min conviene tener 30-60 imágenes originales.")
    musica = render.elegir_musica(Path(cfg.get("assets_carpeta", "assets")) / "musica", semilla)
    if not musica:
        inf["advertencias"].append("Sin música de fondo (opcional): ponga pistas libres de derechos en assets/musica.")
    ok = 0
    usar_w_cfg = cfg.get("whisper", "short")
    for clave, meta in metas.items():
        mp4, wav, ass_n = NOMBRES[clave]
        destino = Path(out) / mp4
        if ya_vigente(destino, Path(out) / wav, meta):
            log.info(f"{mp4} ya existe y es válido: se conserva.")
            inf["video"][mp4] = {"reutilizado": True}
            ok += 1
            continue
        try:
            usar_w = usar_w_cfg == "todo" or (usar_w_cfg == "short" and clave == "short")
            if usar_w and not alineacion.whisper_disponible():
                usar_w = False
                if "whisper" not in inf.get("_avisos", ""):
                    inf["advertencias"].append("Whisper no está instalado: tiempos por palabra estimados por escena "
                                               "(pip install faster-whisper para afinarlos).")
                    inf["_avisos"] = inf.get("_avisos", "") + "whisper"
            por_escena, metodo = palabras_por_escena(meta, usar_w, Path(out) / wav, cfg, log)
            estilo = cfg.get("subtitulos_estilo_short" if clave == "short" else "subtitulos_estilo_largo",
                             "palabra" if clave == "short" else "bloques")
            subs_ass.generar_ass(por_escena, Path(out) / ass_n, formatos[clave], estilo)
            prob = subs_ass.validar_ass(Path(out) / ass_n, meta["duracion"])
            if prob:
                inf["advertencias"].append(f"{ass_n}: {'; '.join(prob)}")
            inf.setdefault("alineacion", {})[clave] = metodo
            plan = plans[clave]
            timeline, off = [], 0.0
            for i, t in enumerate(meta["trozos"]):
                timeline.append({"ini": off, "dur": t["duracion"], "sfx": plan[i]["sfx"] if i < len(plan) else "none"})
                off += t["duracion"]
            quemar = bool(cfg.get("quemar_subtitulos_short" if clave == "short" else "quemar_subtitulos_largo", True))
            voz_txt = f"{getattr(ctx.motor, 'tipo', '?')} {getattr(ctx.motor, 'voz', '?')}"
            st = render.crear_video_escenas(timeline, medios, Path(out) / wav, Path(out) / ass_n, destino,
                                            formatos[clave], cfg, ctx.ffmpeg, ctx.ffprobe, log, musica, quemar, semilla,
                                            comentario=f"voz: {voz_txt}")
            st["voz"] = voz_txt
            log.info(f"{mp4}: voz={voz_txt} | subtítulos incrustados={'sí' if st.get('subtitulos_incrustados') else 'no'} | "
                     f"música={st.get('musica') or 'no'} | efectos de sonido={st.get('sfx')} | transición={st.get('transicion')} | "
                     f"escenas={st['escenas']} con {st['medios_unicos']} imágenes distintas | "
                     f"volumen medio={st.get('volumen_medio_db', '?')} dB")
            inf["video"][mp4] = st
            ok += 1
            if st["escenas"] and st["medios_unicos"] / st["escenas"] < 0.5:
                inf["advertencias"].append(f"{mp4}: {st['medios_unicos']} imágenes para {st['escenas']} escenas "
                                           "(se repiten); añada más imágenes originales.")
            if st["medios_omitidos"]:
                inf["advertencias"] += [f"Medio omitido en {mp4}: {x}" for x in st["medios_omitidos"][:5]]
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            inf["errores"].append(f"No se pudo crear {mp4}: {e}")
            log.error(f"{inf['tema']}: {mp4} falló: {e}")
    inf.pop("_avisos", None)
    return ok

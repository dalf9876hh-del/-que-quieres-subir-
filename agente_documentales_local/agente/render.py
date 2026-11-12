# -*- coding: utf-8 -*-
"""Render por escenas: cada escena dura lo que dura su narración y lleva movimiento (zoom/paneo), de modo que el
video no sea una presentación estática. Mezcla de audio: voz 100 %, música baja con 'ducking' automático bajo la
voz, efectos de sonido en los cortes y normalización de volumen. Subtítulos ASS incrustados (opcional)."""
import array
import hashlib
import math
import os
import random
import shutil
import wave
from pathlib import Path

from .utils import dimensiones_imagen, tam_archivo
from .video import FFmpegError, _ejecutar, _filtro, verificar_mp4


# ----------------------------------------------------------------------------- planificación
def elegir_efecto(dim, formato, i, semilla=0):
    """Zoom si la imagen encaja con el encuadre; paneo si es mucho más ancha o más alta que el video."""
    w, h = formato
    fa = w / float(h)
    rnd = random.Random(f"{semilla}-{i}")
    if dim and dim[0] > 0 and dim[1] > 0:
        ia = dim[0] / float(dim[1])
        if ia > fa * 1.25:
            return rnd.choice(["pan_izq_der", "pan_der_izq"])
        if ia < fa / 1.25:
            return rnd.choice(["pan_arr_aba", "pan_aba_arr"])
    return rnd.choice(["zoom_in", "zoom_out", "zoom_in", "zoom_out", "pan_izq_der", "pan_der_izq"])


def asignar_medios(n_escenas, medios, semilla=0, separacion=4):
    """Reparte los medios entre las escenas sin repetir el mismo en las últimas `separacion` escenas
    (si hay suficientes), y mezclando el orden para que dos temas no tengan el mismo ritmo visual."""
    if not medios:
        return []
    rnd = random.Random(semilla)
    base = list(medios)
    rnd.shuffle(base)
    res, cola, reciente = [], [], []
    for _ in range(n_escenas):
        if not cola:
            cola = list(base)
            rnd.shuffle(cola)
        pos = next((k for k, m in enumerate(cola) if m["ruta"] not in reciente), 0)
        m = cola.pop(pos)
        res.append(m)
        reciente.append(m["ruta"])
        if len(reciente) > min(separacion, max(0, len(base) - 1)):
            reciente.pop(0)
    return res


def _par(x):
    return int(x) // 2 * 2


def foco_de_escena(ruta, dur, efecto):
    """Punto (fx, fy) hacia el que se acerca/aleja el zoom: distinto en cada escena, estable al reanudar."""
    r = random.Random(f"{ruta}|{dur:.2f}|{efecto}")
    return round(r.uniform(0.15, 0.85), 2), round(r.uniform(0.15, 0.85), 2)


def filtro_imagen(w, h, fps, dur, efecto, dim, cfg, extra_fade=False, foco=(0.5, 0.5)):
    """Cadena de filtros de FFmpeg para una imagen con movimiento. Devuelve (filtro_complex, etiqueta_salida)."""
    n = max(2, int(round(dur * fps)))
    zi = float(cfg.get("zoom_intensidad", 0.18))
    if efecto in ("zoom_in", "zoom_out"):
        S = 1.5
        pre = (f"scale={_par(w * S)}:{_par(h * S)}:force_original_aspect_ratio=increase,"
               f"crop={_par(w * S)}:{_par(h * S)}")
        z = f"1+{zi}*on/{n}" if efecto == "zoom_in" else f"{1 + zi}-{zi}*on/{n}"
        fx, fy = foco
        f = (f"[0:v]{pre},zoompan=z='{z}':x='(iw-iw/zoom)*{fx}':y='(ih-ih/zoom)*{fy}':d={n}:s={w}x{h}:fps={fps},"
             f"setsar=1,format=yuv420p")
    else:
        iw0, ih0 = dim if dim else (w, h)
        sf = max(w * 1.25 / iw0, h * 1.25 / ih0)
        sw, sh = _par(iw0 * sf), _par(ih0 * sf)
        sw, sh = max(sw, _par(w * 1.2)), max(sh, _par(h * 1.2))
        factor = float(cfg.get("paneo_factor", 0.35))
        if efecto.startswith("pan_izq") or efecto.startswith("pan_der"):
            rec = min(sw - w, w * factor)
            c = (sw - w) / 2.0
            a, b = (c - rec / 2, c + rec / 2) if efecto == "pan_izq_der" else (c + rec / 2, c - rec / 2)
            xe, ye = f"{a:.1f}+({b - a:.1f})*t/{dur:.3f}", f"{(sh - h) / 2.0:.1f}"
        else:
            rec = min(sh - h, h * factor)
            c = (sh - h) / 2.0
            a, b = (c - rec / 2, c + rec / 2) if efecto == "pan_arr_aba" else (c + rec / 2, c - rec / 2)
            xe, ye = f"{(sw - w) / 2.0:.1f}", f"{a:.1f}+({b - a:.1f})*t/{dur:.3f}"
        f = (f"[0:v]scale={sw}:{sh},crop={w}:{h}:x='{xe}':y='{ye}',fps={fps},setsar=1,format=yuv420p")
    tr = float(cfg.get("transicion_seg", 0.25))
    if cfg.get("transicion") == "fundido" and dur > 3 * tr:
        f += f",fade=t=in:st=0:d={tr / 2:.3f},fade=t=out:st={dur - tr / 2:.3f}:d={tr / 2:.3f}"
    return f + "[v]"


def _clave_escena(m, dur, w, h, efecto, cfg):
    f = cfg["ffmpeg"]
    base = (f"{m['ruta']}|{m['mtime']}|{dur:.3f}|{w}x{h}|{cfg['fps']}|{efecto}|{cfg.get('zoom_intensidad')}|"
            f"{cfg.get('paneo_factor')}|{cfg.get('transicion')}|{cfg.get('transicion_seg')}|{f['crf']}|{f['preset']}")
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:14]


def codificar_escena(ffmpeg, m, dur, w, h, efecto, cfg, destino):
    f = cfg["ffmpeg"]
    tmp = destino.with_name(destino.stem + ".tmp.mp4")
    if m["tipo"] == "imagen":
        entrada = ["-loop", "1", "-framerate", str(cfg["fps"]), "-i", m["ruta"]]
        filtro = filtro_imagen(w, h, cfg["fps"], dur, efecto, m.get("dim"), cfg,
                               foco=foco_de_escena(m["ruta"], dur, efecto))
    else:
        entrada = ["-stream_loop", "-1", "-i", m["ruta"]]
        filtro = _filtro(w, h, cfg["fps"], cfg.get("fondo_desenfocado", True))
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *entrada, "-filter_complex", filtro, "-map", "[v]",
           "-t", f"{dur:.3f}", "-an", "-c:v", "libx264", "-preset", str(f["preset"]), "-crf", str(f["crf"]),
           "-threads", str(f["threads"]), "-r", str(cfg["fps"]), "-g", str(int(cfg["fps"]) * 2),
           "-pix_fmt", "yuv420p", str(tmp)]
    _ejecutar(cmd, int(cfg.get("escena_timeout_seg") or max(60, dur * 20)))   # un archivo raro no bloquea el lote
    os.replace(tmp, destino)


# ----------------------------------------------------------------------------- audio: efectos y mezcla
def _sintetizar_sfx(ffmpeg, nombre, sr, destino):
    """Efectos generados en Python (sin descargar nada) para cuando el usuario no aporta los suyos.
    whoosh: ruido con un filtro que barre de grave a agudo y vuelve (el típico 'shhhwoo'); pop: golpe corto con
    caída de tono. Ambos normalizados a un pico alto para que se oigan por encima de la voz."""
    muestras = []
    if nombre == "whoosh":
        rnd = random.Random(7)
        n, y = int(0.85 * sr), 0.0
        for i in range(n):
            p = i / float(n)
            fc = 250 + 6000 * math.sin(math.pi * p) ** 2
            a = 1 - math.exp(-2 * math.pi * fc / sr)
            y += a * (rnd.uniform(-1, 1) - y)
            muestras.append(y * math.sin(math.pi * p) ** 1.3)
    else:
        n, fase = int(0.2 * sr), 0.0
        rnd = random.Random(3)
        for i in range(n):
            t = i / float(sr)
            fase += 2 * math.pi * (260 + 900 * math.exp(-14 * t)) / sr
            clic = rnd.uniform(-1, 1) * math.exp(-t * 900)
            muestras.append(math.sin(fase) * math.exp(-t * 22) + 0.6 * clic)
    pico = max(abs(x) for x in muestras) or 1.0
    datos = array.array("h", (int(x / pico * 0.9 * 32767) for x in muestras))
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(datos.tobytes())


def preparar_sfx(ffmpeg, carpeta_assets, trabajo, sr):
    """Devuelve {'whoosh': ruta_wav, 'pop': ruta_wav} al sample rate de la narración; usa assets/sfx/<n>.mp3|wav
    si existen y, si no, genera efectos sintéticos."""
    res = {}
    for nombre in ("whoosh", "pop"):
        destino = trabajo / f"sfx_{nombre}_{sr}.wav"
        if not destino.exists():
            propio = next((p for ext in (".wav", ".mp3", ".ogg", ".m4a") for p in
                           [Path(carpeta_assets) / "sfx" / (nombre + ext)] if p.exists()), None)
            if propio:
                _ejecutar([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(propio), "-ac", "1",
                           "-ar", str(sr), "-c:a", "pcm_s16le", str(destino)], 60)
            else:
                _sintetizar_sfx(ffmpeg, nombre, sr, destino)
        res[nombre] = destino
    return res


def pista_sfx(timeline, total_seg, sfx_wavs, destino, sr, ganancia=0.6):
    """WAV del tamaño de la narración con los efectos colocados en cada corte (mezcla en Python puro)."""
    total = int(total_seg * sr) + sr
    pista = array.array("h", bytes(2 * total))
    cache = {}
    n = 0
    for esc in timeline:
        nombre = esc.get("sfx", "none")
        if nombre not in sfx_wavs:
            continue
        if nombre not in cache:
            with wave.open(str(sfx_wavs[nombre]), "rb") as w:
                a = array.array("h")
                a.frombytes(w.readframes(w.getnframes()))
                cache[nombre] = a
        efecto = cache[nombre]
        inicio = max(0, int((esc["ini"] - (0.12 if nombre == "whoosh" else 0.0)) * sr))
        for k in range(min(len(efecto), total - inicio)):
            v = pista[inicio + k] + int(efecto[k] * ganancia)
            pista[inicio + k] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
        n += 1
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pista.tobytes())
    return n


def elegir_musica(carpeta, semilla):
    exts = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
    try:
        pistas = sorted(p for p in Path(carpeta).iterdir() if p.suffix.lower() in exts)
    except OSError:
        return None
    return random.Random(semilla).choice(pistas) if pistas else None


def ffmpeg_soporta(ffmpeg, filtro, opcion):
    import subprocess
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-h", f"filter={filtro}"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        return opcion in (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False


def grafo_audio(tiene_musica, cfg, total_seg, normalize_ok):
    """Voz 100 % + SFX + música baja con ducking; normalización final de volumen."""
    mv = float(cfg.get("musica_volumen", 0.12))
    sv = float(cfg.get("sfx_volumen", 0.9))
    norm = "normalize=0" if normalize_ok else ""
    partes = ["[1:a]aresample=48000,asplit=2[vz][vk]" if tiene_musica else "[1:a]aresample=48000[vz]",
              f"[2:a]aresample=48000,volume={sv}[sf]"]
    if tiene_musica:
        partes.append(f"[3:a]aresample=48000,volume={mv},atrim=0:{total_seg:.2f},"
                      f"afade=t=out:st={max(0.0, total_seg - 3):.2f}:d=3[mu]")
        partes.append("[mu][vk]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=400[md]")
        mezcla = f"[vz][sf][md]amix=inputs=3:duration=first{':' + norm if norm else ''}"
        n = 3
    else:
        mezcla = f"[vz][sf]amix=inputs=2:duration=first{':' + norm if norm else ''}"
        n = 2
    if not normalize_ok:
        mezcla += f",volume={n}"                       # amix antiguo divide por el nº de entradas
    if cfg.get("normalizar_audio", True):
        mezcla += ",loudnorm=I=-14:TP=-1.5:LRA=11"
    partes.append(mezcla + "[a]")
    return ";".join(partes)


# ----------------------------------------------------------------------------- transiciones xfade (opcional)
def _xfade_bloque(ffmpeg, clips, durs, d, cfg, destino):
    """Une un bloque de clips con fundido cruzado real (re-codifica solo ese bloque)."""
    f = cfg["ffmpeg"]
    entradas = []
    for c in clips:
        entradas += ["-i", str(c)]
    filtros, acum_prev, etiqueta = [], 0.0, "0:v"
    for j in range(1, len(clips)):
        acum_prev += durs[j - 1]
        sal = f"x{j}"
        filtros.append(f"[{etiqueta}][{j}:v]xfade=transition=fade:duration={d:.3f}:offset={acum_prev:.3f}[{sal}]")
        etiqueta = sal
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *entradas, "-filter_complex", ";".join(filtros),
           "-map", f"[{etiqueta}]", "-an", "-c:v", "libx264", "-preset", str(f["preset"]), "-crf", str(f["crf"]),
           "-threads", str(f["threads"]), "-r", str(cfg["fps"]), "-pix_fmt", "yuv420p", str(destino)]
    _ejecutar(cmd, 3600)


# ----------------------------------------------------------------------------- video completo
def medir_volumen(ffmpeg, ruta):
    """Volumen medio y máximo (dB) de la pista de audio; None si no se puede medir."""
    import re
    import subprocess
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(ruta), "-vn", "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600)
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr or "")
        x = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr or "")
        return (float(m.group(1)), float(x.group(1))) if m and x else None
    except (OSError, subprocess.SubprocessError):
        return None


def crear_video_escenas(timeline, medios, wav, ass, salida, formato, cfg, ffmpeg, ffprobe, log, musica=None,
                        quemar=False, semilla=0, comentario=""):
    """timeline: [{'ini','dur','sfx'}] (una por trozo de narración). Devuelve estadísticas del montaje."""
    w, h = formato
    salida = Path(salida)
    trabajo = salida.parent / f"_tmp_{salida.stem}"
    trabajo.mkdir(parents=True, exist_ok=True)
    assets = Path(cfg.get("assets_carpeta", "assets"))
    stats = {"escenas": len(timeline), "medios_unicos": 0, "medios_omitidos": [], "reutilizacion": False,
             "efectos": {}, "musica": None, "sfx": 0, "transicion": cfg.get("transicion", "corte")}

    asignados = asignar_medios(len(timeline), medios, semilla)
    if not asignados:
        raise FFmpegError("No hay medios para montar el video.")
    usados = {}
    d_fade = float(cfg.get("transicion_seg", 0.25)) if cfg.get("transicion") == "xfade" else 0.0
    if d_fade:                                       # escenas muy cortas no admiten un solapamiento largo
        corta = min(e["dur"] for e in timeline)
        d_fade = 0.0 if corta < 0.5 else min(d_fade, corta * 0.4)
    bloque = int(cfg.get("xfade_bloque", 8))
    clips, durs = [], []
    n = len(timeline)
    malos = set()
    for i, (esc, m) in enumerate(zip(timeline, asignados)):
        while True:
            if m["ruta"] in malos:                       # un medio defectuoso se descarta para siempre
                libres = [x for x in medios if x["ruta"] not in malos]
                if not libres:
                    raise FFmpegError("Ningún medio pudo convertirse en escena de video.")
                m = libres[i % len(libres)]
            efecto = elegir_efecto(m.get("dim"), formato, i, semilla) if m["tipo"] == "imagen" else "video"
            # con xfade cada escena (salvo la última del bloque) se alarga d_fade para solaparse con la siguiente
            extra = d_fade if (d_fade and (i % bloque != bloque - 1) and i != n - 1) else 0.0
            dur = esc["dur"] + extra
            dest = trabajo / f"esc_{_clave_escena(m, dur, w, h, efecto, cfg)}.mp4"
            try:
                if not (dest.exists() and tam_archivo(dest) > 1000):
                    codificar_escena(ffmpeg, m, dur, w, h, efecto, cfg, dest)
                break
            except FFmpegError as e:
                malos.add(m["ruta"])
                stats["medios_omitidos"].append(f"{Path(m['ruta']).name}: {str(e)[-160:]}")
                log.warning(f"Medio omitido ({Path(m['ruta']).name}); se usa otro en esa escena.")
        usados[m["ruta"]] = usados.get(m["ruta"], 0) + 1
        stats["efectos"][efecto] = stats["efectos"].get(efecto, 0) + 1
        clips.append(dest)
        durs.append(esc["dur"])
        if (i + 1) % 25 == 0:
            log.info(f"Escenas codificadas: {i + 1}/{n}")
    stats["medios_unicos"] = len(usados)
    stats["reutilizacion"] = len(timeline) > len(usados)

    # --- unión de escenas
    union = trabajo / "union.mp4"
    if d_fade:
        bloques = []
        for b in range(0, len(clips), bloque):
            dest_b = trabajo / f"bloque_{b // bloque:03d}_{hashlib.sha1(''.join(c.name for c in clips[b:b + bloque]).encode()).hexdigest()[:8]}.mp4"
            if not (dest_b.exists() and tam_archivo(dest_b) > 1000):
                if len(clips[b:b + bloque]) == 1:
                    shutil.copyfile(clips[b], dest_b)
                else:
                    _xfade_bloque(ffmpeg, clips[b:b + bloque], durs[b:b + bloque], d_fade, cfg, dest_b)
            bloques.append(dest_b)
        lista = bloques
    else:
        lista = clips
    with open(trabajo / "lista.txt", "w", encoding="utf-8", newline="\n") as fh:
        for c in lista:
            fh.write(f"file '{c.name}'\n")
    _ejecutar([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", "lista.txt",
               "-c", "copy", str(union)], 3600, cwd=str(trabajo))

    # --- audio: SFX en los cortes + música
    with wave.open(str(wav), "rb") as wv:
        sr, total = wv.getframerate(), wv.getnframes() / float(wv.getframerate())
    n_sfx = 0
    sfx_wav = trabajo / "sfx_pista.wav"
    if cfg.get("sfx_activar", True):
        n_sfx = pista_sfx(timeline, total, preparar_sfx(ffmpeg, assets, trabajo, sr), sfx_wav, sr,
                          float(cfg.get("sfx_ganancia", 0.6)))
    if n_sfx == 0:                                   # pista silenciosa para mantener el grafo de audio fijo
        pista_sfx([], total, {}, sfx_wav, sr)
    stats["sfx"] = n_sfx
    stats["musica"] = Path(musica).name if musica else None
    normalize_ok = ffmpeg_soporta(ffmpeg, "amix", "normalize")
    fg = grafo_audio(bool(musica), cfg, total, normalize_ok)

    f = cfg["ffmpeg"]
    tmp_salida = salida.with_name(salida.stem + ".tmp.mp4")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(union), "-i", str(Path(wav).resolve()),
           "-i", str(sfx_wav)]
    if musica:
        cmd += ["-stream_loop", "-1", "-i", str(musica)]
    if quemar and ass and Path(ass).exists():
        shutil.copyfile(ass, trabajo / "subs.ass")            # nombre simple: sin escapes de rutas de Windows
        fg = f"[0:v]ass=subs.ass[vv];" + fg
        mapa_v, codec_v = "[vv]", ["-c:v", "libx264", "-preset", str(f["preset"]), "-crf", str(f["crf"]),
                                   "-threads", str(f["threads"]), "-pix_fmt", "yuv420p"]
    else:
        mapa_v, codec_v = "0:v", ["-c:v", "copy"]
    cmd += ["-filter_complex", fg, "-map", mapa_v, "-map", "[a]", *codec_v, "-c:a", "aac",
            "-b:a", str(f["audio_bitrate"]), "-t", f"{total:.3f}"]
    if comentario:
        cmd += ["-metadata", f"comment={comentario}"]
    cmd += ["-movflags", "+faststart", str(tmp_salida)]
    log.info(f"Mezclando audio ({'con' if musica else 'sin'} música, {n_sfx} efectos)"
             f"{' y subtitulos incrustados' if quemar else ''} -> {salida.name}")
    _ejecutar(cmd, 7200, cwd=str(trabajo))
    os.replace(tmp_salida, salida)
    problemas = verificar_mp4(salida, ffprobe, ffmpeg, total)
    if problemas:
        raise FFmpegError(f"{salida.name} no pasó la verificación: {'; '.join(problemas)}")
    vol = medir_volumen(ffmpeg, salida)
    if vol:
        stats["volumen_medio_db"], stats["volumen_max_db"] = vol
        if vol[0] < -45:
            raise FFmpegError(f"{salida.name}: el audio es casi inaudible (media {vol[0]:.0f} dB).")
    stats["subtitulos_incrustados"] = bool(quemar and ass)
    shutil.rmtree(trabajo, ignore_errors=True)
    return stats

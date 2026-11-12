# -*- coding: utf-8 -*-
"""Montaje con FFmpeg. Procesamiento secuencial (un solo FFmpeg a la vez).
Cada medio único se codifica una vez como escena (caché reanudable) y las escenas se repiten
(concat sin recodificar) hasta cubrir la narración."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .utils import tam_archivo


class FFmpegError(Exception):
    pass


def buscar_herramienta(nombre, ruta_cfg=""):
    if ruta_cfg:
        p = Path(ruta_cfg)
        if p.is_dir():
            p = p / (nombre + (".exe" if os.name == "nt" else ""))
        if p.exists():
            return str(p)
    return shutil.which(nombre)


def _ejecutar(cmd, timeout, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"Tiempo agotado ({timeout}s) ejecutando FFmpeg.")
    except OSError as e:
        raise FFmpegError(f"No se pudo ejecutar {cmd[0]}: {e}")
    if r.returncode != 0:
        raise FFmpegError(f"FFmpeg terminó con código {r.returncode}: {(r.stderr or '')[-600:].strip()}")
    return r


def sondear(ruta, ffprobe, ffmpeg):
    """Devuelve {'duracion','video','audio'} usando ffprobe (o ffmpeg -i como alternativa)."""
    info = {"duracion": 0.0, "video": False, "audio": False}
    if ffprobe:
        try:
            r = _ejecutar([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                           str(ruta)], 60)
            d = json.loads(r.stdout or "{}")
            for s in d.get("streams", []):
                if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic"):
                    info["video"] = True
                    info["vcodec"] = s.get("codec_name")
                if s.get("codec_type") == "audio":
                    info["audio"] = True
                    info["acodec"] = s.get("codec_name")
            info["duracion"] = float((d.get("format") or {}).get("duration") or 0)
            return info
        except (FFmpegError, ValueError):
            pass
    if ffmpeg:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(ruta)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        t = r.stderr or ""
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", t)
        if m:
            info["duracion"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        info["video"] = bool(re.search(r"Stream #\S+.*Video:", t))
        info["audio"] = bool(re.search(r"Stream #\S+.*Audio:", t))
    return info


def verificar_mp4(ruta, ffprobe, ffmpeg, dur_audio=None):
    """Lista de problemas (vacía si el MP4 es aceptable)."""
    p = Path(ruta)
    if not p.exists():
        return ["no existe"]
    if tam_archivo(p) < 20_000:
        return [f"tamaño sospechoso ({tam_archivo(p)} bytes)"]
    i = sondear(p, ffprobe, ffmpeg)
    problemas = []
    if not i["video"]:
        problemas.append("sin pista de video")
    if not i["audio"]:
        problemas.append("sin pista de audio")
    if i["duracion"] <= 0:
        problemas.append("duración inválida")
    elif dur_audio and i["duracion"] < dur_audio - 1.0:
        problemas.append(f"dura {i['duracion']:.1f}s, menos que la narración ({dur_audio:.1f}s)")
    return problemas


def _filtro(w, h, fps, desenfoque):
    if desenfoque:
        return (f"[0:v]split=2[a][b];"
                f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"scale={max(2, w // 8)}:{max(2, h // 8)},boxblur=2:1,scale={w}:{h}[bg];"
                f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1,fps={fps}[v]")
    return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p,setsar=1,fps={fps}[v]")


def _clave(medio, dur, w, h, cfg):
    f = cfg["ffmpeg"]
    base = f"{medio['ruta']}|{medio['mtime']}|{dur:.3f}|{w}x{h}|{cfg['fps']}|{cfg['fondo_desenfocado']}|{f['crf']}|{f['preset']}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _codificar_escena(ff, medio, dur, w, h, cfg, destino):
    f = cfg["ffmpeg"]
    tmp = destino.with_name(destino.stem + ".tmp.mp4")
    entrada = ["-loop", "1", "-framerate", str(cfg["fps"]), "-i", medio["ruta"]] if medio["tipo"] == "imagen" \
        else ["-i", medio["ruta"]]
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", *entrada,
           "-filter_complex", _filtro(w, h, cfg["fps"], cfg["fondo_desenfocado"]), "-map", "[v]",
           "-t", f"{dur:.3f}", "-an", "-c:v", "libx264", "-preset", str(f["preset"]), "-crf", str(f["crf"]),
           "-threads", str(f["threads"]), "-r", str(cfg["fps"]), "-pix_fmt", "yuv420p", str(tmp)]
    _ejecutar(cmd, 900)
    os.replace(tmp, destino)


def preparar_medios(seleccion, ffprobe, ffmpeg):
    """Añade tipo/mtime/duración. Descarta los medios ilegibles (devuelve avisos)."""
    items, avisos = [], []
    for ruta, tipo in seleccion:
        try:
            mtime = int(Path(ruta).stat().st_mtime)
        except OSError as e:
            avisos.append(f"No se puede leer {Path(ruta).name}: {e}")
            continue
        it = {"ruta": str(ruta), "tipo": tipo, "mtime": mtime, "dur": None}
        if tipo == "video":
            d = sondear(ruta, ffprobe, ffmpeg)
            if not d["video"] or d["duracion"] < 0.5:
                avisos.append(f"Video ilegible o demasiado corto, omitido: {Path(ruta).name}")
                continue
            it["dur"] = d["duracion"]
        items.append(it)
    return items, avisos


def crear_video(items, wav, srt, salida, formato, dur_audio, base_escena, cfg, ffmpeg, ffprobe, log):
    """Genera el MP4 final. Devuelve estadísticas. Lanza FFmpegError si no se puede."""
    w, h = formato
    salida = Path(salida)
    trabajo = salida.parent / f"_tmp_{salida.stem}"
    trabajo.mkdir(parents=True, exist_ok=True)
    stats = {"medios_unicos": 0, "medios_omitidos": [], "escenas": 0, "reutilizacion": False}
    escenas = []
    for it in items:
        d = base_escena if it["tipo"] == "imagen" else min(it["dur"], float(cfg["max_duracion_clip_seg"]))
        dest = trabajo / f"esc_{_clave(it, d, w, h, cfg)}.mp4"
        try:
            if not (dest.exists() and tam_archivo(dest) > 1000):
                log.info(f"Codificando escena ({it['tipo']}): {Path(it['ruta']).name}")
                _codificar_escena(ffmpeg, it, d, w, h, cfg, dest)
            real = sondear(dest, ffprobe, ffmpeg)["duracion"]  # duración REAL codificada (puede ser < nominal)
            escenas.append((dest.name, real if real > 0.3 else d))
        except FFmpegError as e:
            stats["medios_omitidos"].append(f"{Path(it['ruta']).name}: {e}")
            log.warning(f"Medio omitido {Path(it['ruta']).name}: {e}")
    if not escenas:
        raise FFmpegError("Ningún medio pudo convertirse en escena de video.")
    stats["medios_unicos"] = len(escenas)
    lista, acum, i = [], 0.0, 0
    while acum < dur_audio + 1.0:
        n, d = escenas[i % len(escenas)]
        lista.append(n)
        acum += d
        i += 1
        if i > 20000:
            raise FFmpegError("Plan de escenas demasiado largo.")
    stats["escenas"] = len(lista)
    stats["reutilizacion"] = len(lista) > len(escenas)
    with open(trabajo / "lista.txt", "w", encoding="utf-8", newline="\n") as f:
        for n in lista:
            f.write(f"file '{n}'\n")
    f = cfg["ffmpeg"]
    tmp_salida = salida.with_name(salida.stem + ".tmp.mp4")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", "lista.txt",
           "-i", str(Path(wav).resolve())]
    if cfg.get("quemar_subtitulos") and srt and Path(srt).exists():
        shutil.copyfile(srt, trabajo / "subs.srt")  # nombre simple: evita escapar rutas de Windows
        estilo = "FontSize=" + ("18" if h > w else "20") + ",Outline=2,MarginV=" + ("90" if h > w else "30")
        cmd += ["-map", "0:v", "-map", "1:a", "-vf", f"subtitles=subs.srt:force_style='{estilo}'",
                "-c:v", "libx264", "-preset", str(f["preset"]), "-crf", str(f["crf"]),
                "-threads", str(f["threads"]), "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a", "-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", str(f["audio_bitrate"]), "-shortest", "-movflags", "+faststart", str(tmp_salida)]
    log.info(f"Uniendo {len(lista)} escenas con la narración -> {salida.name}")
    _ejecutar(cmd, 7200, cwd=str(trabajo))
    os.replace(tmp_salida, salida)
    problemas = verificar_mp4(salida, ffprobe, ffmpeg, dur_audio)
    if problemas:
        raise FFmpegError(f"{salida.name} no pasó la verificación: {'; '.join(problemas)}")
    shutil.rmtree(trabajo, ignore_errors=True)
    return stats

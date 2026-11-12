# -*- coding: utf-8 -*-
"""Genera una presentación .pptx a partir de un tema usando Llama (Ollama) con salida JSON:
    {"slides": [{"title": "...", "bullets": ["..."], "notes": "..."}]}
El JSON se valida (y se reintenta si viene mal) y se convierte en diapositivas con título, viñetas y notas del orador.
Con --voz se genera además un .wav por diapositiva con Kokoro, leyendo las notas.

Necesita python-pptx:  pip install python-pptx   (kokoro-onnx, soundfile y ollama ya los tiene instalados)
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

from agente import ollama_cli, voz_kokoro                       # noqa: E402
from agente.escenas import _bloque_json, _reparar                # noqa: E402
from agente.texto import _RE_EMOJI, parece_espanol               # noqa: E402
from agente.utils import cargar_config, nombre_seguro            # noqa: E402

SISTEMA = ('Eres un asistente que diseña presentaciones en español. Devuelves ÚNICAMENTE un objeto JSON válido con '
           'la forma {"slides":[{"title":"...","bullets":["...","..."],"notes":"..."}]}, sin texto adicional ni '
           'markdown. Reglas: "title" breve (máximo 70 caracteres); "bullets" de 3 a 5 viñetas cortas (máximo 110 '
           'caracteres cada una); "notes" son las notas del orador, de 2 a 4 frases (40 a 90 palabras) para leer en '
           'voz alta. No uses emojis. No inventes cifras ni citas: si no estás seguro de un dato, no lo incluyas.')


def _config():
    try:
        return cargar_config(RAIZ / "config.json")
    except (OSError, ValueError):
        return {}


# ----------------------------------------------------------------------------- validación del JSON
def _texto(v, maximo):
    if not isinstance(v, (str, int, float)):
        return ""
    t = _RE_EMOJI.sub("", str(v))
    t = re.sub(r"[*`#]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > maximo:
        t = t[:maximo].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
    return t


def parsear_json(crudo):
    """json.loads y, si falla, reparación tolerante. None si no hay forma de interpretarlo."""
    t = re.sub(r"<think>.*?</think>", "", crudo or "", flags=re.S).strip()
    for cand in (t, _bloque_json(t) or ""):
        for s in (cand, _reparar(cand) if cand else ""):
            if not s:
                continue
            try:
                return json.loads(s, strict=False)
            except ValueError:
                continue
    return None


def validar(obj, n):
    """Devuelve (slides, errores). Cada slide: {'title','bullets','notes'}."""
    lista = None
    if isinstance(obj, dict):
        for k in ("slides", "diapositivas", "presentacion"):
            if isinstance(obj.get(k), list):
                lista = obj[k]
                break
    elif isinstance(obj, list):
        lista = obj
    if not lista:
        return [], ['falta la lista "slides" o está vacía']
    slides, errores = [], []
    for i, s in enumerate(lista, 1):
        if not isinstance(s, dict):
            errores.append(f"diapositiva {i}: no es un objeto JSON")
            continue
        titulo = _texto(s.get("title") or s.get("titulo") or s.get("título"), 90)
        vin = s.get("bullets") or s.get("vinetas") or s.get("viñetas") or s.get("puntos")
        if isinstance(vin, str):
            vin = [x for x in re.split(r"[\n;]+", vin) if x.strip()]
        vin = [b for b in (_texto(x, 140) for x in (vin if isinstance(vin, list) else [])) if b][:7]
        notas = _texto(s.get("notes") or s.get("notas"), 1200)
        if not titulo:
            errores.append(f"diapositiva {i}: falta el título (title)")
        if not vin:
            errores.append(f"diapositiva {i}: la lista bullets está vacía")
        if not notas:
            errores.append(f"diapositiva {i}: faltan las notas (notes)")
        if titulo and vin and notas and len(notas.split()) >= 15 and not parece_espanol(notas + " " + " ".join(vin)):
            errores.append(f"diapositiva {i}: el texto no está en español")
        slides.append({"title": titulo, "bullets": vin, "notes": notas})
    if len(slides) < n:
        errores.append(f"se pidieron {n} diapositivas y llegaron {len(slides)}")
    return slides[:n], errores


def pedir_slides(modelo, tema, n, intentos=3):
    mensajes = [{"role": "system", "content": SISTEMA},
                {"role": "user", "content": f"Tema de la presentación: {tema}\nNúmero de diapositivas: {n}\n"
                                            "Devuelve únicamente el JSON."}]
    ultimo_error = "respuesta vacía"
    for k in range(1, intentos + 1):
        crudo = ollama_cli.chat(modelo, mensajes, format="json",
                                options={"temperature": 0.4, "num_ctx": 4096, "num_predict": 2800})
        obj = parsear_json(crudo)
        if obj is None:
            errores, slides = ["la respuesta no era un JSON válido"], []
        else:
            slides, errores = validar(obj, n)
        if not errores:
            return slides
        solo_cantidad = slides and all(e.startswith("se pidieron") for e in errores) and \
            all(s["title"] and s["bullets"] and s["notes"] for s in slides)
        if k == intentos and solo_cantidad:
            print(f"[AVISO] El modelo entregó {len(slides)} diapositivas en lugar de {n}.")
            return slides
        ultimo_error = "; ".join(errores[:4])
        print(f"[AVISO] Intento {k}/{intentos}: {ultimo_error}.")
        mensajes += [{"role": "assistant", "content": (crudo or "")[:1500]},
                     {"role": "user", "content": f"Tu respuesta tuvo estos problemas: {ultimo_error}. Devuelve de nuevo "
                                                 f"SOLO el JSON corregido, con exactamente {n} diapositivas."}]
    raise ollama_cli.OllamaCliError(f"El modelo no devolvió un JSON válido tras {intentos} intentos ({ultimo_error}). "
                                    "Pruebe con --modelo, con menos diapositivas o vuelva a ejecutar.")


# ----------------------------------------------------------------------------- .pptx
def importar_pptx():
    try:
        import pptx  # noqa: F401
        return pptx
    except ImportError:
        raise ollama_cli.OllamaCliError("Falta python-pptx. Instálelo en el entorno virtual con: pip install python-pptx")


def crear_pptx(slides, ruta, tema=""):
    importar_pptx()
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)          # 16:9
    layout = prs.slide_layouts[1]                                             # título y contenido
    for s in slides:
        diap = prs.slides.add_slide(layout)
        titulo, cuerpo = diap.shapes.title, diap.placeholders[1]
        titulo.left, titulo.top, titulo.width, titulo.height = Inches(0.8), Inches(0.4), Inches(11.7), Inches(1.3)
        cuerpo.left, cuerpo.top, cuerpo.width, cuerpo.height = Inches(0.8), Inches(1.9), Inches(11.7), Inches(5.0)
        titulo.text = s["title"]
        for p in titulo.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(36)
                r.font.bold = True
        tf = cuerpo.text_frame
        tf.word_wrap = True
        tam = Pt(28 if len(s["bullets"]) <= 4 else 24 if len(s["bullets"]) <= 5 else 20)
        for k, b in enumerate(s["bullets"]):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            p.text = b
            for r in p.runs:
                r.font.size = tam
        diap.notes_slide.notes_text_frame.text = s["notes"]                   # notas del orador
    prs.core_properties.title = tema or "Presentación"
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(ruta))
    return ruta


# ----------------------------------------------------------------------------- programa
def parsear(argv=None):
    cfg = _config()
    ap = argparse.ArgumentParser(description="Crea un .pptx en español con Llama (Ollama, salida JSON).")
    ap.add_argument("tema", nargs="*", help="tema de la presentación (si se omite, se pregunta)")
    ap.add_argument("-n", "--diapositivas", type=int, default=8, help="número de diapositivas (por defecto 8)")
    ap.add_argument("-o", "--salida", default=None, help="archivo .pptx de salida (por defecto presentaciones\\<tema>.pptx)")
    ap.add_argument("--modelo", default=os.environ.get("OLLAMA_MODEL") or cfg.get("modelo_ollama") or "llama3.2:3b")
    ap.add_argument("--voz", action="store_true", help="generar también un .wav por diapositiva con Kokoro (lee las notas)")
    ap.add_argument("--voz-nombre", default=os.environ.get("KOKORO_VOZ") or cfg.get("kokoro_voz") or "ef_dora",
                    help="voz de Kokoro: ef_dora, em_alex o em_santa")
    ap.add_argument("--velocidad", default=os.environ.get("KOKORO_SPEED") or cfg.get("kokoro_velocidad") or 1.0)
    ap.add_argument("--lang", default=os.environ.get("KOKORO_LANG") or "es")
    ap.add_argument("--kokoro-modelo", default=None)
    ap.add_argument("--kokoro-voces", default=None)
    return ap.parse_args(argv)


def main(argv=None):
    args = parsear(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    tema = " ".join(args.tema).strip()
    if not tema:
        try:
            tema = input("Tema de la presentación: ").strip()
        except (EOFError, KeyboardInterrupt):
            tema = ""
    if not tema:
        print("[ERROR] Debe indicar un tema.")
        return 2
    if not 1 <= args.diapositivas <= 30:
        print("[ERROR] El número de diapositivas debe estar entre 1 y 30.")
        return 2
    try:
        importar_pptx()
        ollama_cli.verificar(args.modelo)
        tts = None
        if args.voz:                                      # se comprueba antes de gastar tiempo con el modelo
            try:
                tts = voz_kokoro.KokoroTTS(args.kokoro_modelo, args.kokoro_voces, args.voz_nombre, args.velocidad, args.lang)
            except voz_kokoro.KokoroError as e:
                print(f"[ERROR] {e}\nEjecute sin --voz para generar solo la presentación.")
                return 2
        print(f"Generando {args.diapositivas} diapositivas sobre '{tema}' con {args.modelo}...")
        slides = pedir_slides(args.modelo, tema, args.diapositivas)
    except ollama_cli.OllamaCliError as e:
        print(f"[ERROR] {e}")
        return 1
    salida = Path(args.salida) if args.salida else Path("presentaciones") / f"{nombre_seguro(tema)}.pptx"
    try:
        crear_pptx(slides, salida, tema)
    except ollama_cli.OllamaCliError as e:
        print(f"[ERROR] {e}")
        return 1
    except OSError as e:
        print(f"[ERROR] No se pudo guardar {salida}: {e}. ¿Está abierto en PowerPoint?")
        return 1
    print(f"[OK] Presentación guardada: {salida} ({len(slides)} diapositivas con notas del orador)")
    if tts:
        carpeta = salida.with_name(salida.stem + "_audio")
        fallos = 0
        for i, s in enumerate(slides, 1):
            try:
                ruta = tts.guardar(s["notes"], carpeta / f"diapositiva_{i:02d}.wav")
                print(f"[OK] Audio {i}/{len(slides)}: {ruta}")
            except Exception as e:  # noqa: BLE001
                fallos += 1
                print(f"[AVISO] Diapositiva {i}: no se pudo generar el audio ({e})")
        return 3 if fallos else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

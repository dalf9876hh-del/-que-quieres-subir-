# -*- coding: utf-8 -*-
"""Pruebas automáticas con datos ficticios locales.
- Unitarias: recuento de palabras, nombres seguros, SRT, limpieza, descubrimiento de temas.
- Integración: servidor Ollama FALSO (solo para pruebas), motor de voz de silencio y FFmpeg real
  (se omiten si FFmpeg no está instalado). NO prueban Ollama real, voces de Windows ni calidad del texto."""
import json
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["AGENTE_TEST_TTS"] = "1"

import main as agente_main  # noqa: E402
from agente import guion, publicacion, subtitulos, texto as tx, voz, video  # noqa: E402
from agente.utils import cargar_config, contar_palabras, nombre_seguro, validar_config  # noqa: E402

VOCAB = ("de la el que en y los las un una se por con para es del al lo su como más pero sus ya este esta "
         "entre cuando muy sin sobre también hasta hay donde desde todo son fue ser han tiene puede "
         "ciencia historia planeta estrella cerebro máquina océano selva ciudad imperio teoría método "
         "experimento descubrimiento energía materia tiempo memoria lenguaje señal sistema paisaje "
         "civilización observación hipótesis evidencia pregunta respuesta camino futuro pasado "
         "profundo lejano antiguo complejo sutil enorme frágil curioso persistente silencioso").split()


class OllamaFalso(BaseHTTPRequestHandler):
    escala = 1.0
    fallar_short = False
    peticiones = 0
    semilla = random.Random(7)

    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._json({"models": [{"name": "qwen3:4b"}, {"name": "llama3.2:3b"}]})

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        d = json.loads(self.rfile.read(n))
        OllamaFalso.peticiones += 1
        prompt = d["prompt"] if "prompt" in d else d["messages"][1]["content"]
        es_generate = self.path == "/api/generate"
        def resp(txt):
            return self._json({"response": txt} if es_generate else {"message": {"content": txt}})
        if "Traduce al inglés" in prompt:
            return resp("test topic")
        m = re.search(r"unas (\d+) palabras", prompt) or re.search(r"apunta a (\d+)", prompt)
        objetivo = int(m.group(1)) if m else 100
        if "YouTube Shorts" in prompt and OllamaFalso.fallar_short:
            return resp("Texto demasiado corto.")
        if "YouTube Shorts" in prompt:
            n_pal = 110
        else:
            n_pal = int(objetivo * OllamaFalso.escala)
        palabras = [OllamaFalso.semilla.choice(VOCAB) for _ in range(n_pal)]
        frases, i = [], 0
        while i < len(palabras):
            k = OllamaFalso.semilla.randint(8, 16)
            f = " ".join(palabras[i:i + k])
            frases.append(f[0].upper() + f[1:] + ".")
            i += k
        cuerpo = "## Título que debe eliminarse\n\n**" + " ".join(frases[:3]) + "**\n\n" + " ".join(frases[3:])
        resp(cuerpo)


def servidor(escala=1.0):
    OllamaFalso.escala = escala
    OllamaFalso.fallar_short = False
    OllamaFalso.peticiones = 0
    srv = HTTPServer(("127.0.0.1", 0), OllamaFalso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class LogFalso:
    def __getattr__(self, n):
        return lambda *a, **k: None


class TestUtilidades(unittest.TestCase):
    def test_config_valida(self):
        cfg = cargar_config(RAIZ / "config.json")
        self.assertEqual(validar_config(cfg), [])
        self.assertEqual(cfg["modelo_ollama"], "llama3.2:3b")
        self.assertEqual(cfg["modelo_respaldo"], "llama3.2:3b")

    def test_recuento_palabras(self):
        self.assertEqual(contar_palabras("Hola mundo"), 2)
        self.assertEqual(contar_palabras("El niño comió piñas en la cañada."), 7)
        self.assertEqual(contar_palabras("  Una   cifra: 1.000 años, y el año 2,5 ¿verdad? "), 9)
        self.assertEqual(contar_palabras(""), 0)
        self.assertEqual(contar_palabras("————  ...  !!"), 0)
        self.assertEqual(contar_palabras("hola " * 1000), 1000)
        self.assertEqual(contar_palabras("anti-materia y ¡qué día!"), 4)

    def test_nombre_seguro(self):
        self.assertEqual(nombre_seguro("Inteligencia artificial"), "inteligencia_artificial")
        self.assertEqual(nombre_seguro("Exploración espacial"), "exploracion_espacial")
        self.assertEqual(nombre_seguro("¿Año/ñandú?*"), "ano_nandu")
        self.assertEqual(nombre_seguro("***"), "tema")

    def test_limpieza_texto(self):
        crudo = "<think>pensando</think>## Título\n**Parte 1**\n- Una viñeta.\nTexto normal aquí [música] sigue.\n\nOtro párrafo."
        limpio = tx.limpiar_narracion(crudo)
        self.assertNotIn("Título", limpio)
        self.assertNotIn("pensando", limpio)
        self.assertNotIn("[música]", limpio)
        self.assertIn("Una viñeta.", limpio)
        self.assertEqual(tx.recortar_a_frase("Primera frase completa y bastante larga. Frase dos incomple"), "Primera frase completa y bastante larga.")
        self.assertTrue(tx.parece_espanol("la historia de los planetas es una de las más antiguas que se conocen en el mundo y sus misterios"))
        self.assertFalse(tx.parece_espanol("the history of the planets is one of the oldest known stories in the world today and beyond it"))

    def test_solapamiento(self):
        a = "uno dos tres cuatro cinco seis siete ocho nueve diez"
        self.assertGreater(tx.solapamiento(a, a), 0.9)
        self.assertEqual(tx.solapamiento("otra cosa totalmente distinta que no se parece en nada", a), 0.0)


class TestOllamaCliente(unittest.TestCase):
    def _con_falso(self, respuesta, modelo):
        from agente import ollama
        vistos = {}

        def falso(cfg, ruta, payload=None, timeout=None):
            vistos.update(ruta=ruta, payload=payload)
            return respuesta
        orig = ollama._peticion
        ollama._peticion = falso
        try:
            r = ollama.generar({"modelo_ollama": modelo, "ollama_num_ctx": 4096}, "s", "hola")
        finally:
            ollama._peticion = orig
        return r, vistos

    def test_qwen3_usa_plantilla_sin_razonamiento(self):
        r, v = self._con_falso({"response": "Texto final en español."}, "qwen3:4b")
        self.assertEqual(r, "Texto final en español.")
        self.assertEqual(v["ruta"], "/api/generate")
        self.assertTrue(v["payload"]["raw"])
        self.assertTrue(v["payload"]["prompt"].endswith("<think>\n\n</think>\n\n"))

    def test_otro_modelo_y_razonamiento_huerfano(self):
        r, v = self._con_falso({"message": {"content": "Okay, pienso...\n</think>\n\nSolo el guion."}}, "llama3.2:3b")
        self.assertEqual(r, "Solo el guion.")
        self.assertEqual(v["ruta"], "/api/chat")

    def test_cambio_automatico_a_modelo_de_respaldo(self):
        from agente import ollama
        modelos = []

        def falso(cfg, ruta, payload=None, timeout=None):
            modelos.append(payload["model"])
            if payload["model"] == "qwen3:4b":
                return {"response": "Okay, I need to write the section..."}
            return {"message": {"content": "Texto correcto en español."}}
        orig = ollama._peticion
        ollama._peticion = falso
        cfg = {"modelo_ollama": "qwen3:4b", "modelo_respaldo": "llama3.2:3b", "ollama_num_ctx": 4096,
               "_modelos_instalados": ["qwen3:4b", "llama3.2:3b"]}
        try:
            r = ollama.generar(cfg, "s", "hola")
            r2 = ollama.generar(cfg, "s", "otra vez")
        finally:
            ollama._peticion = orig
        self.assertEqual((r, r2), ("Texto correcto en español.",) * 2)
        self.assertEqual(cfg["modelo_ollama"], "llama3.2:3b")
        self.assertEqual(modelos, ["qwen3:4b", "llama3.2:3b", "llama3.2:3b"])   # el cambio es permanente

    def test_sin_respaldo_instalado_sigue_fallando(self):
        from agente import ollama
        orig = ollama._peticion
        ollama._peticion = lambda *a, **k: {"response": "Okay, I need to write..."}
        try:
            with self.assertRaises(ollama.ModeloRazonaError):
                ollama.generar({"modelo_ollama": "qwen3:4b", "modelo_respaldo": "llama3.2:3b",
                                "ollama_num_ctx": 4096, "_modelos_instalados": ["qwen3:4b"]}, "s", "hola")
        finally:
            ollama._peticion = orig

    def test_razonamiento_en_ingles_se_detecta(self):
        from agente import ollama
        with self.assertRaises(ollama.ModeloRazonaError):
            self._con_falso({"response": "Okay, I need to write the introduction section..."}, "qwen3:4b")


FAKE_PYTTSX3 = """
import time, wave, os
class _V:
    def __init__(s, n, i): s.name, s.id, s.languages = n, i, []
class _E:
    def __init__(s): s.t = None; s.p = {}
    def getProperty(s, k): return [_V('Microsoft Pablo Desktop - Spanish (Spain)', 'MSTTS_V110_esES_PabloM'), _V('David', 'en')]
    def setProperty(s, k, v): s.p[k] = v
    def save_to_file(s, t, f): s.t, s.f = t, f
    def runAndWait(s):
        if os.environ.get('FAKE_TTS_HANG'): time.sleep(60)
        with wave.open(s.f, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b'\\x00\\x00' * 22050)
    def stop(s): pass
def init(): return _E()
"""


class TestShortRecorte(unittest.TestCase):
    def test_recorte_a_frase_completa(self):
        t = " ".join(f"Frase número {i} con varias palabras más." for i in range(30))
        r = guion._recortar_a_limite(t, 130)
        self.assertLessEqual(contar_palabras(r), 130)
        self.assertGreater(contar_palabras(r), 110)
        self.assertTrue(r.endswith("."))
        self.assertEqual(guion._recortar_a_limite("Una sola frase larguísima " * 30 + ".", 20), "")


class TestVozProceso(unittest.TestCase):
    """Proceso auxiliar de pyttsx3 con un módulo FALSO (comprueba selección de voz, WAV y el tiempo máximo)."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        (self.d / "pyttsx3.py").write_text(FAKE_PYTTSX3, encoding="utf-8")
        self.old = {k: os.environ.get(k) for k in ("PYTHONPATH", "AGENTE_TEST_TTS", "FAKE_TTS_HANG")}
        os.environ["PYTHONPATH"] = str(self.d)
        os.environ.pop("AGENTE_TEST_TTS", None)
        os.environ.pop("FAKE_TTS_HANG", None)
        self.addCleanup(self._restaurar)
        self.cfg = cargar_config(RAIZ / "config.json")
        self.cfg["voz_timeout_seg"] = 4

    def _restaurar(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_voz_espanola_y_wav(self):
        m = voz.MotorVoz(self.cfg, LogFalso())
        self.assertEqual(m.tipo, "pyttsx3")
        self.assertTrue(m.voz_es)
        self.assertIn("Pablo", m.voz)
        meta = voz.sintetizar_texto(m, "Una frase de prueba. Otra frase distinta.", self.d / "n.wav", LogFalso(), "t")
        self.assertTrue(voz.wav_valido(self.d / "n.wav"))
        self.assertGreater(meta["duracion"], 0.9)

    def test_cuelgue_se_corta_por_tiempo(self):
        import time
        m = voz.MotorVoz(self.cfg, LogFalso())
        os.environ["FAKE_TTS_HANG"] = "1"
        t0 = time.time()
        with self.assertRaises(voz.VozError):
            m.sintetizar("Texto que se cuelga.", self.d / "x.wav")
        self.assertLess(time.time() - t0, 20)


class TestListaVideos(unittest.TestCase):
    def test_saneado(self):
        self.assertEqual(publicacion.sanear_titulo('"El **gran** misterio" <del> océano 🌊.'), "El gran misterio del océano")
        self.assertLessEqual(len(publicacion.sanear_titulo("palabra " * 40)), 100)
        h = publicacion.normalizar_hashtags("#Océano #corriente del niño #de #12 #Océano", 8, "#Shorts")
        self.assertEqual(h[0], "#Shorts")
        self.assertEqual(len([x for x in h if x.lower() == "#océano"]), 1)
        self.assertTrue(all(" " not in x and x.startswith("#") for x in h))
        self.assertNotIn("#de", h)
        self.assertEqual(publicacion.hashtags_desde_tema("Corriente del niño1"), "#Corriente #Niño")

    def test_parseo_respuesta(self):
        ok = ("TITULO: El misterio de la corriente del Pacífico\nRESUMEN: Este video explica de forma sencilla "
              "qué es la corriente del Pacífico y por qué influye en el clima de muchas regiones del mundo.\n"
              "HASHTAGS: #Clima #Océano #Ciencia")
        p = publicacion._parsear(ok)
        self.assertEqual(p["titulo"], "El misterio de la corriente del Pacífico")
        self.assertIsNone(publicacion._parsear("hola esto no tiene formato"))
        self.assertIsNone(publicacion._parsear("TITULO: Breve\nRESUMEN: corto"))

    def _tema(self, base):
        out = base / "salida" / "corriente_del_nino1"
        (out / "partes_guion").mkdir(parents=True)
        (out / "partes_guion" / "parte_01.txt").write_text("El océano guarda secretos. Hay corrientes enormes.", encoding="utf-8")
        (out / "guion_largo.txt").write_text("El océano guarda secretos. " * 20, encoding="utf-8")
        (out / "guion_short.txt").write_text("Una corriente cambia el clima del planeta entero.", encoding="utf-8")
        (out / "documental_largo.mp4").write_bytes(b"L" * 3000)
        (out / "short_vertical.mp4").write_bytes(b"S" * 2000)
        (out / "subtitulos_largos.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHola\n", encoding="utf-8")
        (out / "investigacion.json").write_text(json.dumps({"fuentes": [{"titulo": "Estudio X", "anio": 2020,
                                                "enlace": "https://doi.org/10.1/x", "fuente": "Crossref"}]}), encoding="utf-8")
        (out / "audio_generado.meta.json").write_text(json.dumps({"duracion": 785.0}), encoding="utf-8")
        return out

    def test_lista_estructura_y_no_sobrescribe(self):
        cfg = cargar_config(RAIZ / "config.json")
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            out = self._tema(base)
            lista = base / "salida" / "_lista_videos"

            def llm(cfg, sis, prompt, mt=400, temp=0.6):
                return ("TITULO: La corriente que cambia el clima del planeta\nRESUMEN: Un recorrido sencillo por "
                        "una corriente oceánica que influye en el clima de muchas regiones, con sus causas y sus "
                        "efectos más importantes.\nHASHTAGS: #Clima #Océano #Ciencia #Documental")
            r = publicacion.preparar_tema(cfg, "Corriente del niño1", "corriente_del_nino1", out, lista, LogFalso(), llm)
            self.assertEqual(r["items"], 2, r)
            carpeta = lista / "01_corriente_del_nino1"
            for f in ["1_LARGO_documental.mp4", "1_LARGO_documental.txt", "1_LARGO_documental_subtitulos.srt",
                      "2_SHORT_vertical.mp4", "2_SHORT_vertical.txt"]:
                self.assertTrue((carpeta / f).exists(), f)
            ficha = (carpeta / "1_LARGO_documental.txt").read_text(encoding="utf-8-sig")
            self.assertIn("--- TÍTULO", ficha)
            self.assertIn("La corriente que cambia el clima del planeta", ficha)
            self.assertIn("https://doi.org/10.1/x", ficha)
            self.assertIn("#Clima", ficha)
            self.assertNotIn("<", ficha.split("--- DESCRIPCIÓN")[1])
            short = (carpeta / "2_SHORT_vertical.txt").read_text(encoding="utf-8-sig")
            self.assertIn("#Shorts", short)
            self.assertNotIn("https://doi.org", short)
            # el usuario edita la ficha; volver a ejecutar NO debe pisarla
            (carpeta / "1_LARGO_documental.txt").write_text(ficha.replace("La corriente que cambia", "MI TITULO EDITADO"),
                                                            encoding="utf-8-sig")
            publicacion.preparar_tema(cfg, "Corriente del niño1", "corriente_del_nino1", out, lista, LogFalso(), llm)
            self.assertIn("MI TITULO EDITADO", (carpeta / "1_LARGO_documental.txt").read_text(encoding="utf-8-sig"))
            gen = (lista / "LISTA_GENERAL.txt").read_text(encoding="utf-8-sig")
            self.assertIn("MI TITULO EDITADO", gen)
            self.assertIn("[ ] 01. [LARGO]", gen)
            self.assertIn("[ ] 02. [SHORT]", gen)
            # segundo tema recibe el número 2 y el primero conserva el suyo
            out2 = base / "salida" / "otro"
            shutil.copytree(out, out2)
            publicacion.preparar_tema(cfg, "Otro", "otro", out2, lista, LogFalso(), llm)
            self.assertTrue((lista / "02_otro").is_dir())
            self.assertTrue((carpeta / "1_LARGO_documental.mp4").exists())

    def test_respaldo_si_el_modelo_falla(self):
        from agente import ollama
        cfg = cargar_config(RAIZ / "config.json")
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            out = self._tema(base)

            def llm(*a, **k):
                raise ollama.OllamaError("sin conexión")
            r = publicacion.preparar_tema(cfg, "Corriente del niño1", "c", out, base / "l", LogFalso(), llm)
            self.assertEqual(r["items"], 2)
            self.assertTrue(r["advertencias"])
            ficha = (base / "l" / "01_c" / "1_LARGO_documental.txt").read_text(encoding="utf-8-sig")
            self.assertIn("Corriente del niño: el documental", ficha)

    def test_tema_sin_videos(self):
        cfg = cargar_config(RAIZ / "config.json")
        with tempfile.TemporaryDirectory() as d:
            r = publicacion.preparar_tema(cfg, "X", "x", Path(d) / "nada", Path(d) / "l", LogFalso())
            self.assertEqual(r["items"], 0)


class TestSubtitulos(unittest.TestCase):
    def test_srt_valido(self):
        trozos = [{"texto": "Primera frase del documental. Segunda frase, que es bastante más larga para forzar "
                            "el salto de línea dentro del subtítulo.", "duracion": 7.3},
                  {"texto": "Tercera frase.", "duracion": 2.0}]
        with tempfile.TemporaryDirectory() as d:
            r = Path(d) / "a.srt"
            n = subtitulos.generar_srt(trozos, r)
            self.assertGreaterEqual(n, 3)
            self.assertEqual(subtitulos.validar_srt(r, 9.3), [])
            self.assertTrue(subtitulos.validar_srt(r, 30.0))  # no cubre 30 s -> problema
            self.assertNotIn("-", re.findall(r"\d\d:\d\d:\d\d,\d{3}", r.read_text(encoding="utf-8"))[0][:1])

    def test_srt_invalido_detectado(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d) / "mal.srt"
            r.write_text("1\n00:00:05,000 --> 00:00:02,000\nhola\n\n3\n00:00:06,000 --> 00:00:07,000\nx\n", encoding="utf-8")
            self.assertTrue(subtitulos.validar_srt(r, 7.0))

    def test_trozos_de_voz(self):
        t = ("Una frase. " * 100).strip()
        trozos = voz.dividir_en_trozos(t, 120)
        self.assertTrue(all(len(x) <= 125 for x in trozos))
        self.assertEqual(" ".join(trozos).split(), t.split())


class TestBiblioteca(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bib ñandú "))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _foto(self, ruta, color="red", size="320x240"):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        os.system(f'ffmpeg -loglevel error -y -f lavfi -i color=c={color}:s={size} -frames:v 1 "{ruta}"')

    def test_descubrimiento_excluye_salida_y_ocultas(self):
        for n in ["Ciencia", "Exploración espacial", "_documentales_generados", ".oculta", "_tmp"]:
            (self.tmp / n).mkdir()
        (self.tmp / "archivo_suelto.jpg").write_bytes(b"x")
        nombres = [p.name for p in agente_main.descubrir_temas(self.tmp, "_documentales_generados")]
        self.assertEqual(nombres, ["Ciencia", "Exploración espacial"])

    def test_deteccion_de_medios_y_formatos(self):
        t = self.tmp / "Historia"
        t.mkdir()
        for n in ["a.JPG", "b.png", "c.webp", "d.bmp", "e.jpeg", "v1.mp4", "v2.MOV", "v3.mkv", "v4.avi",
                  "v5.webm", "v6.m4v", "nota.txt", "doc.pdf"]:
            (t / n).write_bytes(b"datos")
        (t / "vacio.jpg").write_bytes(b"")
        (t / "_documentales").mkdir()
        (t / "_documentales" / "no.jpg").write_bytes(b"x")
        imgs, vids = agente_main.descubrir_medios(t)
        self.assertEqual(len(imgs), 5)
        self.assertEqual(len(vids), 6)

    def test_titulo_desde_tema_txt(self):
        t = self.tmp / "2510"
        t.mkdir()
        self.assertEqual(agente_main.leer_tema(t), "")
        (t / "tema.txt").write_text("\n\n  La Gran Muralla China  \nnotas ignoradas", encoding="utf-8")
        self.assertEqual(agente_main.leer_tema(t), "La Gran Muralla China")
        imgs, vids = agente_main.descubrir_medios(t)
        self.assertEqual((len(imgs), len(vids)), (0, 0))   # tema.txt no cuenta como medio

    def test_limites_de_seleccion(self):
        imgs = [Path(f"i{k}.jpg") for k in range(30)]
        vids = [Path(f"v{k}.mp4") for k in range(10)]
        cfg = {"max_imagenes": 12, "max_videos": 3}
        sel = agente_main.seleccionar_medios(imgs, vids, cfg)
        self.assertEqual(sum(1 for _, t in sel if t == "imagen"), 12)
        self.assertEqual(sum(1 for _, t in sel if t == "video"), 3)

    def test_tema_sin_medios_no_detiene_el_resto(self):
        cfg = cargar_config(RAIZ / "config.json")
        cfg["investigacion_web"] = False
        (self.tmp / "Vacio").mkdir()
        (self.tmp / "Solo texto").mkdir()
        (self.tmp / "Solo texto" / "nota.txt").write_text("hola", encoding="utf-8")
        ctx = agente_main.Contexto(cfg, LogFalso(), self.tmp / "_documentales_generados")
        for t in agente_main.descubrir_temas(self.tmp, "_documentales_generados"):
            inf = agente_main.procesar_tema(t, nombre_seguro(t.name), cfg, ctx)
            self.assertEqual(inf["estado"], "error")
            self.assertTrue((ctx.salida / nombre_seguro(t.name) / "informe.json").exists())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg no instalado")
class TestIntegracion(unittest.TestCase):
    """Flujo completo con Ollama FALSO, voz de silencio y FFmpeg real."""

    def preparar(self, escala=1.0):
        self.srv = servidor(escala)
        self.addCleanup(self.srv.shutdown)
        self.tmp = Path(tempfile.mkdtemp(prefix="biblioteca ñ "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cfg = cargar_config(RAIZ / "config.json")
        cfg.update(ruta_biblioteca=str(self.tmp), investigacion_web=False, max_imagenes=3, max_videos=1, motor_voz="local",
                   duracion_escena_seg=3, duracion_escena_short_seg=2,
                   ollama_url=f"http://127.0.0.1:{self.srv.server_port}",
                   resolucion_horizontal=[320, 180], resolucion_vertical=[180, 320], fps=10)
        cfg["ffmpeg"].update(preset="ultrafast", crf=32)
        self.cfg = cfg
        t = self.tmp / "Exploración espacial"
        t.mkdir()
        for k, c in enumerate(["red", "green", "blue"]):
            os.system(f'ffmpeg -loglevel error -y -f lavfi -i color=c={c}:s=400x300 -frames:v 1 "{t / f"foto {k}.png"}"')
        os.system(f'ffmpeg -loglevel error -y -f lavfi -i testsrc=s=320x240:d=2:r=10 -pix_fmt yuv420p "{t / "clip ñ.mp4"}"')
        (self.tmp / "Sin medios").mkdir()  # debe fallar sin detener al resto
        self.cfg_path = self.tmp / "cfg.json"
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    def test_flujo_completo(self):
        self.preparar(1.0)
        rc = agente_main.main(["--config", str(self.cfg_path)])
        self.assertEqual(rc, 1)  # un tema con error -> código 1, pero el otro se completó
        out = self.tmp / "_documentales_generados" / "exploracion_espacial"
        inf = json.loads((out / "informe.json").read_text(encoding="utf-8"))
        self.assertIn(inf["estado"], ("completado", "completado_con_advertencias"), inf)
        for f in agente_main.ENTREGABLES:
            self.assertGreater((out / f).stat().st_size, 0, f)
        largo = (out / "guion_largo.txt").read_text(encoding="utf-8")
        self.assertGreater(contar_palabras(largo), 1000)
        corto = contar_palabras((out / "guion_short.txt").read_text(encoding="utf-8"))
        self.assertTrue(90 <= corto <= 130, corto)
        self.assertEqual(len(list((out / "partes_guion").glob("parte_*.txt"))), 7)
        self.assertNotIn("Título que debe eliminarse", largo)
        ff = video.buscar_herramienta("ffprobe")
        self.assertEqual(video.verificar_mp4(out / "documental_largo.mp4", ff, None), [])
        self.assertEqual(video.verificar_mp4(out / "short_vertical.mp4", ff, None), [])
        s = video.sondear(out / "short_vertical.mp4", ff, None)
        self.assertTrue(s["video"] and s["audio"] and s["duracion"] > 5)
        lista = self.tmp / "_documentales_generados" / "_lista_videos"
        self.assertTrue((lista / "LISTA_GENERAL.txt").exists())
        self.assertTrue((lista / "01_exploracion_espacial" / "1_LARGO_documental.mp4").exists())
        self.assertTrue((lista / "01_exploracion_espacial" / "2_SHORT_vertical.txt").exists())
        reg = json.loads((self.tmp / "_documentales_generados" / "registro_agente.json").read_text(encoding="utf-8"))
        self.assertEqual(reg["ultimo_estado_por_tema"]["Sin medios"]["estado"], "error")
        self.assertIn("exploracion_espacial", [p.name for p in (self.tmp / "_documentales_generados").iterdir()])
        # El segundo ciclo no debe recortar ni volver a pedir texto: reanudación
        antes = OllamaFalso.peticiones
        agente_main.main(["--config", str(self.cfg_path), "--tema", "Exploración espacial"])
        self.assertLessEqual(OllamaFalso.peticiones - antes, 3)  # solo la consulta y el Short

    def test_la_duracion_real_nunca_pasa_del_maximo(self):
        """Voz de prueba a ~158 palabras/min; config optimista (220) -> el primer cálculo se pasa y debe corregirse."""
        self.preparar(1.0)
        cfg = dict(self.cfg)
        cfg.update(duracion_objetivo_min=7, palabras_por_minuto=220, motor_voz="local")
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        agente_main.main(["--config", str(self.cfg_path), "--tema", "Exploración espacial"])
        out = self.tmp / "_documentales_generados" / "exploracion_espacial"
        inf = json.loads((out / "informe.json").read_text(encoding="utf-8"))
        self.assertLessEqual(inf["voz"]["duracion_largo_seg"], 7 * 60 + 1, inf["voz"])
        self.assertGreater(inf["palabras_largo"], 1000)
        self.assertTrue((out / "documental_largo.mp4").exists())
        ritmo = json.loads((out / "partes_guion" / "ritmo.json").read_text(encoding="utf-8"))
        self.assertTrue(140 < ritmo["wpm"] < 175, ritmo)

    def _con_kokoro_falso(self):
        from tests.test_kokoro import modulo_kokoro, modulo_soundfile
        previos = {k: sys.modules.get(k) for k in ("kokoro_onnx", "soundfile")}
        self.kok_llamadas = []
        sys.modules["kokoro_onnx"] = modulo_kokoro(self.kok_llamadas)
        sys.modules["soundfile"] = modulo_soundfile()

        def restaurar():
            for k, v in previos.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.addCleanup(restaurar)

    def test_narracion_con_kokoro_de_principio_a_fin(self):
        self.preparar(1.0)
        self._con_kokoro_falso()
        m, v = self.tmp / "kokoro-v1.0.onnx", self.tmp / "voices-v1.0.bin"
        m.write_bytes(b"x")
        v.write_bytes(b"x")
        cfg = dict(self.cfg)
        cfg.update(motor_voz="kokoro", kokoro_modelo=str(m), kokoro_voces=str(v), kokoro_voz="em_alex",
                   kokoro_velocidad=1.1)
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        rc = agente_main.main(["--config", str(self.cfg_path), "--tema", "Exploración espacial"])
        out = self.tmp / "_documentales_generados" / "exploracion_espacial"
        inf = json.loads((out / "informe.json").read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertIn(inf["estado"], ("completado", "completado_con_advertencias"), inf["errores"])
        self.assertEqual(inf["voz"]["motor"], "kokoro")
        self.assertEqual(inf["voz"]["voz"], "em_alex")
        self.assertTrue(all(c["voice"] == "em_alex" and c["speed"] == 1.1 and c["lang"] == "es" for c in self.kok_llamadas))
        self.assertTrue((out / "documental_largo.mp4").exists() and (out / "short_vertical.mp4").exists())
        self.assertIn("kokoro", inf["video"]["documental_largo.mp4"]["voz"])      # queda anotado en el informe

    def test_si_kokoro_no_carga_se_avisa_antes_de_gastar_tiempo_con_el_guion(self):
        self.preparar(1.0)
        self._con_kokoro_falso()
        cfg = dict(self.cfg)
        cfg.update(motor_voz="kokoro", kokoro_modelo=str(self.tmp / "no.onnx"), kokoro_voces=str(self.tmp / "no.bin"))
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        antes = OllamaFalso.peticiones
        rc = agente_main.main(["--config", str(self.cfg_path), "--tema", "Exploración espacial"])
        self.assertEqual(rc, 4)
        self.assertEqual(OllamaFalso.peticiones, antes)              # no se escribió ni una sección del guion

    def test_variable_de_entorno_manda_sobre_config(self):
        self.preparar(1.0)
        self._con_kokoro_falso()
        m, v = self.tmp / "k.onnx", self.tmp / "v.bin"
        m.write_bytes(b"x")
        v.write_bytes(b"x")
        cfg = dict(self.cfg)
        cfg.update(motor_voz="local", kokoro_modelo=str(m), kokoro_voces=str(v))
        os.environ["AGENTE_MOTOR_VOZ"] = "kokoro"
        self.addCleanup(os.environ.pop, "AGENTE_MOTOR_VOZ", None)
        ctx = agente_main.Contexto(cfg, LogFalso(), self.tmp)
        self.assertEqual(agente_main.crear_motor(cfg, ctx).tipo, "kokoro")

    def test_short_fallido_no_impide_el_documental_largo(self):
        self.preparar(1.0)
        OllamaFalso.fallar_short = True
        agente_main.main(["--config", str(self.cfg_path), "--tema", "Exploración espacial"])
        out = self.tmp / "_documentales_generados" / "exploracion_espacial"
        inf = json.loads((out / "informe.json").read_text(encoding="utf-8"))
        self.assertEqual(inf["estado"], "incompleto")
        self.assertTrue((out / "documental_largo.mp4").exists())
        self.assertFalse((out / "short_vertical.mp4").exists())
        self.assertTrue(any("Short" in e for e in inf["errores"]))

    def test_diez_temas_se_procesan_todos(self):
        self.preparar(1.0)
        shutil.rmtree(self.tmp / "Sin medios")
        base = self.tmp / "Exploración espacial"
        for n in range(9):
            d = self.tmp / f"Tema {n}"
            shutil.copytree(base, d)
        agente_main.main(["--config", str(self.cfg_path)])
        reg = json.loads((self.tmp / "_documentales_generados" / "registro_agente.json").read_text(encoding="utf-8"))
        estados = [r["estado"] for r in reg["ultimo_estado_por_tema"].values()]
        self.assertEqual(len(estados), 10)
        self.assertTrue(all(e.startswith("completado") for e in estados), estados)
        self.assertEqual(len(list((self.tmp / "_documentales_generados").glob("*/*.mp4"))), 20)
        # segunda ejecución: todos completados -> se omiten sin llamar a Ollama
        antes = OllamaFalso.peticiones
        agente_main.main(["--config", str(self.cfg_path)])
        self.assertEqual(OllamaFalso.peticiones, antes)

    def test_estado_muestra_cada_carpeta(self):
        import io, contextlib
        self.preparar(1.0)
        agente_main.main(["--config", str(self.cfg_path), "--tema", "Exploración espacial"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = agente_main.main(["--config", str(self.cfg_path), "--estado"])
        txt = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Exploración espacial: completado", txt)
        self.assertIn("Sin medios: sin procesar", txt)
        self.assertIn("Resumen:", txt)

    def test_max_temas_procesa_por_tandas(self):
        self.preparar(1.0)
        shutil.rmtree(self.tmp / "Sin medios")
        for n in range(3):
            shutil.copytree(self.tmp / "Exploración espacial", self.tmp / f"Tema {n}")
        agente_main.main(["--config", str(self.cfg_path), "--max-temas", "2"])
        mp4 = lambda: len(list((self.tmp / "_documentales_generados").glob("*/*.mp4")))
        self.assertEqual(mp4(), 4)                      # 2 temas x (largo + short)
        agente_main.main(["--config", str(self.cfg_path), "--max-temas", "2"])
        self.assertEqual(mp4(), 8)
        agente_main.main(["--config", str(self.cfg_path), "--max-temas", "2"])
        self.assertEqual(mp4(), 8)                      # nada pendiente

    def test_ampliaciones_cuando_las_secciones_son_cortas(self):
        self.preparar(0.5)  # el modelo falso escribe la mitad de lo pedido
        cfg = dict(self.cfg)
        out = self.tmp / "salida"
        out.mkdir()
        r = guion.generar_guion_largo(cfg, "Exploración espacial", out, "", LogFalso())
        self.assertGreater(r["ampliaciones"], 0, r)
        self.assertEqual(r["completo"], r["palabras"] > 1000, r)
        if r["completo"]:
            self.assertGreater(contar_palabras((out / "guion_largo.txt").read_text(encoding="utf-8")), 1000)

    def test_guion_insuficiente_no_se_marca_completo(self):
        self.preparar(0.4)
        cfg = dict(self.cfg)
        cfg["max_ampliaciones"] = 1
        out = self.tmp / "salida2"
        out.mkdir()
        r = guion.generar_guion_largo(cfg, "Tema", out, "", LogFalso())
        self.assertFalse(r["completo"], r)
        self.assertFalse((out / "guion_largo.txt").exists())
        self.assertTrue((out / "guion_largo_en_progreso.txt").exists())

    def test_reanudacion_desde_secciones_guardadas(self):
        self.preparar(1.0)
        out = self.tmp / "salida3"
        out.mkdir()
        guion.generar_guion_largo(self.cfg, "Tema", out, "", LogFalso())
        (out / "guion_largo.txt").unlink()
        (out / "partes_guion" / "parte_05.txt").unlink()
        antes = OllamaFalso.peticiones
        r = guion.generar_guion_largo(self.cfg, "Tema", out, "", LogFalso())
        self.assertTrue(r["completo"])
        self.assertLessEqual(OllamaFalso.peticiones - antes, 3)  # solo regenera la sección borrada


if __name__ == "__main__":
    unittest.main(verbosity=2)

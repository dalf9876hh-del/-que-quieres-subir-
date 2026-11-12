# -*- coding: utf-8 -*-
"""Pruebas del pipeline por escenas: parser JSON, plan de escenas, alineación, subtítulos ASS, render con FFmpeg,
edge-tts (módulo FALSO), ComfyUI (servidor FALSO) y control de calidad."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["AGENTE_TEST_TTS"] = "1"

from agente import alineacion, calidad, comfy, escenas, guion, render, subs_ass, video, voz  # noqa: E402
from agente.utils import cargar_config, contar_palabras  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


class LogFalso:
    def __getattr__(self, n):
        return lambda *a, **k: None


class TestParserJson(unittest.TestCase):
    def casos(self):
        return {
            "limpio": '{"scenes":[{"VOICEOVER":"Hola mundo, esto es una prueba.","VISUAL_PROMPT":"a city at night","SFX":"whoosh"}]}',
            "markdown": 'Claro:\n```json\n[{"voiceover":"Primera.","visual_prompt":"red car","sfx":"pop"},'
                        '{"voiceover":"Segunda.","visual_prompt":"blue sea","sfx":"none"},]\n```\nListo.',
            "comillas_simples_claves_sin_comillas": "{scenes: [{voiceover: 'Escena con texto.', visual_prompt: 'sunset', sfx: 'pop'}]}",
            "salto_de_linea_en_cadena": '[{"voiceover": "Linea uno\nlinea dos", "visual_prompt": "x y z", "sfx": "none"}]',
            "comillas_tipograficas": '[{“voiceover”: “Hola con comillas.”, “visual_prompt”: “mountains”, “sfx”: “whoosh”}]',
            "cortado": '{"scenes":[{"voiceover":"Completa.","visual_prompt":"a","sfx":"pop"},{"voiceover":"Se corta aq',
            "claves_en_espanol": '[{"Narración":"Texto en español.","Visual":"un bosque","Efecto":"swoosh"}]',
            "think": '<think>pensando {mal}</think>[{"voiceover":"Tras el razonamiento.","visual_prompt":"abc def ghi","sfx":"none"}]',
            "roto": 'VOICEOVER: "Una escena rota", VISUAL_PROMPT: "dark forest", SFX: whoosh\nVOICEOVER: "Otra", VISUAL_PROMPT: "sea", SFX: none',
        }

    def test_todos_los_formatos_dañados_se_recuperan(self):
        for nombre, texto in self.casos().items():
            esc, av = escenas.parsear_guion_json(texto)
            self.assertGreaterEqual(len(esc), 1, nombre)
            self.assertTrue(all(e["voiceover"] and e["sfx"] in escenas.SFX_VALIDOS for e in esc), nombre)

    def test_valores(self):
        esc, _ = escenas.parsear_guion_json(self.casos()["claves_en_espanol"])
        self.assertEqual(esc[0]["sfx"], "whoosh")                      # "swoosh" -> whoosh
        self.assertEqual(esc[0]["visual_prompt"], "un bosque")
        esc, av = escenas.parsear_guion_json(self.casos()["markdown"])
        self.assertEqual([e["sfx"] for e in esc], ["pop", "none"])
        self.assertTrue(av)                                            # avisó de que reparó el JSON
        esc, _ = escenas.parsear_guion_json(self.casos()["roto"])
        self.assertEqual(len(esc), 2)

    def test_basura_no_lanza_excepcion(self):
        for t in ("", "No puedo ayudar con eso.", "{{{{", "[]", None, '{"a": 1}'):
            esc, av = escenas.parsear_guion_json(t)
            self.assertEqual(esc, [])
            self.assertTrue(av)


class TestPlanEscenas(unittest.TestCase):
    def test_plan_deterministico(self):
        trozos = ["Primera idea importante del tema.", "Un dato curioso: 1990 fue clave.", "Otra escena distinta aquí.",
                  "Nueva sección empieza aquí con algo.", "¿Y ahora qué pasa entonces?"]
        plan = escenas.plan_deterministico(trozos, "Tema X", inicios_seccion=("Nueva sección empieza",))
        self.assertEqual(len(plan), 5)
        self.assertEqual(plan[3]["sfx"], "whoosh")                    # cambio de sección
        self.assertEqual(plan[1]["sfx"], "pop")                       # dato con cifra
        self.assertTrue(all(p["visual_prompt"] for p in plan))

    def test_planificar_con_llm_reparando_y_completando(self):
        trozos = [f"Escena número {i} con bastante texto para narrar." for i in range(10)]
        respuestas = iter([
            '```json\n[{"VISUAL_PROMPT":"mountain sunrise, cinematic","SFX":"whoosh"},{"visual_prompt":"old library interior","sfx":"pop"},]\n```',
            "Lo siento, no puedo."])

        def llm(cfg, sis, prompt, mt=0, temp=0):
            return next(respuestas)
        plan, av = escenas.planificar_con_llm({}, "Tema", trozos, LogFalso(), llm, lote=8)
        self.assertEqual(len(plan), 10)
        self.assertEqual(plan[0]["visual_prompt"], "mountain sunrise, cinematic")
        self.assertEqual(plan[0]["sfx"], "whoosh")
        self.assertEqual([p["voiceover"] for p in plan], trozos)      # la narración NO cambia nunca
        self.assertTrue(av)
        activos = [i for i, p in enumerate(plan) if p["sfx"] != "none"]
        self.assertTrue(all(b - a >= 3 for a, b in zip(activos, activos[1:])))   # efectos espaciados


class TestAlineacion(unittest.TestCase):
    def test_proporcional_cubre_toda_la_duracion(self):
        p = alineacion.proporcional("Hola mundo, esto es una prueba larga.", 6.0)
        self.assertEqual(len(p), 7)
        self.assertAlmostEqual(p[-1]["fin"], 6.0, places=3)
        self.assertTrue(all(b["ini"] >= a["fin"] - 1e-9 for a, b in zip(p, p[1:])))

    def test_alinear_con_asr_conserva_el_texto_del_guion(self):
        texto = "El Océano Pacífico es enorme."
        asr = [{"t": "el", "ini": 0.0, "fin": 0.2}, {"t": "oceano", "ini": 0.2, "fin": 0.8},
               {"t": "pacifico", "ini": 0.8, "fin": 1.5}, {"t": "enorme", "ini": 1.9, "fin": 2.5}]   # sin "es"
        r = alineacion.alinear_con_guion(texto, asr, 2.6)
        self.assertEqual([x["t"] for x in r], ["El", "Océano", "Pacífico", "es", "enorme."])
        self.assertTrue(1.5 <= r[3]["ini"] <= 1.9)                    # "es" interpolada entre sus vecinas
        self.assertTrue(all(b["ini"] >= a["ini"] for a, b in zip(r, r[1:])))

    def test_limites_de_edge(self):
        lim = [{"tipo": "SentenceBoundary", "t": "Primera frase.", "ini": 0.0, "fin": 1.0},
               {"tipo": "SentenceBoundary", "t": "Segunda frase más larga.", "ini": 1.2, "fin": 3.0}]
        r = alineacion.desde_limites("Primera frase. Segunda frase más larga.", lim, 3.0)
        self.assertEqual(len(r), 6)
        self.assertLessEqual(r[1]["fin"], 1.0 + 1e-6)
        self.assertGreaterEqual(r[2]["ini"], 1.2 - 1e-6)
        r2, metodo = alineacion.palabras_de_escena("Hola a todos.", 1.5, None)
        self.assertEqual(metodo, "proporcional")


class TestSubtitulosAss(unittest.TestCase):
    def _escenas(self):
        pal = alineacion.proporcional("Esta es una frase de prueba para los subtítulos dinámicos del video.", 6.0)
        return [(0.0, pal), (6.0, alineacion.proporcional("Y otra escena distinta con más palabras aquí.", 4.0))]

    def test_ass_palabra_y_bloques(self):
        with tempfile.TemporaryDirectory() as d:
            for estilo in ("palabra", "bloques"):
                ruta = Path(d) / f"{estilo}.ass"
                n = subs_ass.generar_ass(self._escenas(), ruta, (720, 1280), estilo)
                self.assertGreater(n, 3 if estilo == "palabra" else 1)
                self.assertEqual(subs_ass.validar_ass(ruta, 10.0), [], estilo)
                t = ruta.read_text(encoding="utf-8")
                self.assertIn("PlayResX: 720", t)
                self.assertIn("[Events]", t)
            t = (Path(d) / "palabra.ass").read_text(encoding="utf-8")
            self.assertIn("&H0000FFFF&", t)                           # palabra actual resaltada
            self.assertTrue(subs_ass.validar_ass(Path(d) / "palabra.ass", 2.0))   # el audio sería más corto -> problema

    def test_caracteres_especiales_escapados(self):
        pal = [{"t": "{peligro}", "ini": 0.0, "fin": 1.0}, {"t": "a\\b", "ini": 1.0, "fin": 2.0}]
        with tempfile.TemporaryDirectory() as d:
            subs_ass.generar_ass([(0.0, pal)], Path(d) / "x.ass", (1280, 720), "bloques")
            t = (Path(d) / "x.ass").read_text(encoding="utf-8")
            self.assertNotIn("{peligro}", t)


class TestRenderHelpers(unittest.TestCase):
    def test_efecto_segun_aspecto(self):
        self.assertTrue(render.elegir_efecto((3000, 1000), (1280, 720), 0).startswith("pan_"))
        self.assertIn(render.elegir_efecto((500, 1000), (1280, 720), 0), ("pan_arr_aba", "pan_aba_arr"))
        self.assertIn(render.elegir_efecto((1920, 1080), (1280, 720), 3, 5),
                      ("zoom_in", "zoom_out", "pan_izq_der", "pan_der_izq"))

    def test_asignar_medios_sin_repetir_seguido(self):
        medios = [{"ruta": f"i{k}.jpg"} for k in range(6)]
        asig = render.asignar_medios(40, medios, semilla=3, separacion=4)
        self.assertEqual(len(asig), 40)
        for i in range(len(asig) - 1):
            self.assertNotEqual(asig[i]["ruta"], asig[i + 1]["ruta"])
        self.assertEqual(set(m["ruta"] for m in asig), {m["ruta"] for m in medios})
        self.assertEqual(render.asignar_medios(5, [], 1), [])

    def test_grafo_audio(self):
        cfg = {"musica_volumen": 0.12, "sfx_volumen": 0.9, "normalizar_audio": True}
        con = render.grafo_audio(True, cfg, 100.0, True)
        self.assertIn("sidechaincompress", con)
        self.assertIn("volume=0.12", con)
        self.assertIn("amix=inputs=3", con)
        self.assertIn("loudnorm", con)
        sin = render.grafo_audio(False, cfg, 100.0, True)
        self.assertNotIn("sidechain", sin)
        self.assertNotIn("[vk]", sin)                                 # sin salidas sin conectar
        self.assertIn("volume=3", render.grafo_audio(True, cfg, 100.0, False))

    @unittest.skipUnless(FFMPEG, "FFmpeg no instalado")
    def test_pista_sfx_en_python_puro(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            wavs = render.preparar_sfx(FFMPEG, d / "assets", d, 24000)
            self.assertTrue(wavs["whoosh"].exists() and wavs["pop"].exists())
            destino = d / "sfx.wav"
            n = render.pista_sfx([{"ini": 1.0, "dur": 2, "sfx": "pop"}, {"ini": 3.0, "dur": 2, "sfx": "whoosh"},
                                  {"ini": 5.0, "dur": 2, "sfx": "none"}], 8.0, wavs, destino, 24000)
            self.assertEqual(n, 2)
            with wave.open(str(destino), "rb") as w:
                self.assertGreaterEqual(w.getnframes(), 8 * 24000)
                datos = w.readframes(w.getnframes())
            self.assertTrue(any(b for b in datos[2 * 24000:2 * 24000 + 4000]))   # hay señal donde va el "pop"


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg no instalado")
class TestRenderConFFmpeg(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = cargar_config(RAIZ / "config.json")
        self.cfg.update(fps=10, assets_carpeta=str(self.tmp / "assets"))
        self.cfg["ffmpeg"].update(preset="ultrafast", crf=34)
        medios = []
        for k, (w, h) in enumerate([(800, 600), (1600, 500), (400, 800)]):
            ruta = self.tmp / f"i{k}.png"
            subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:d=1",
                            "-frames:v", "1", str(ruta)], check=True)
            medios.append({"ruta": str(ruta), "tipo": "imagen", "mtime": 1, "dur": None, "dim": (w, h)})
        self.medios = medios
        self.wav = self.tmp / "voz.wav"
        with wave.open(str(self.wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x10" * int(24000 * 9.5))
        self.timeline = [{"ini": 0.0, "dur": 3.0, "sfx": "none"}, {"ini": 3.0, "dur": 2.5, "sfx": "whoosh"},
                         {"ini": 5.5, "dur": 2.0, "sfx": "pop"}, {"ini": 7.5, "dur": 2.0, "sfx": "none"}]
        pal = alineacion.proporcional("Palabras de prueba para el subtítulo dinámico de este video corto.", 9.0)
        self.ass = self.tmp / "s.ass"
        subs_ass.generar_ass([(0.0, pal)], self.ass, (320, 180), "palabra")

    def _crear(self, nombre, **kw):
        cfg = dict(self.cfg)
        cfg.update(kw.pop("cfg", {}))
        musica = kw.pop("musica", None)
        return render.crear_video_escenas(self.timeline, self.medios, self.wav, self.ass, self.tmp / nombre, (320, 180),
                                          cfg, FFMPEG, FFPROBE, LogFalso(), musica, kw.get("quemar", False), 1)

    def test_video_con_efectos_sin_musica_y_con_subtitulos(self):
        st = self._crear("a.mp4", quemar=True)
        self.assertEqual(st["escenas"], 4)
        self.assertEqual(video.verificar_mp4(self.tmp / "a.mp4", FFPROBE, FFMPEG, 9.5), [])
        self.assertGreater(len(st["efectos"]), 1)                     # hay variedad de movimiento
        self.assertEqual(st["sfx"], 2)

    def test_musica_con_ducking_y_transicion_xfade(self):
        musica = self.tmp / "m.mp3"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=330:d=4", "-c:a", "libmp3lame",
                        str(musica)], check=True)                     # más corta que el video: debe repetirse
        st = self._crear("b.mp4", musica=musica, cfg={"transicion": "xfade", "xfade_bloque": 3, "transicion_seg": 0.3})
        self.assertEqual(st["musica"], "m.mp3")
        self.assertEqual(video.verificar_mp4(self.tmp / "b.mp4", FFPROBE, FFMPEG, 9.4), [])
        info = video.sondear(self.tmp / "b.mp4", FFPROBE, FFMPEG)
        self.assertAlmostEqual(info["duracion"], 9.5, delta=0.6)
        self.assertTrue(info["audio"] and info["video"])

    def test_imagen_defectuosa_no_rompe_el_video(self):
        mala = self.tmp / "mala.png"
        mala.write_bytes(b"\x89PNG\r\n\x1a\n" + b"corrupta" * 50)     # cabecera PNG con datos rotos
        self.medios.append({"ruta": str(mala), "tipo": "imagen", "mtime": 1, "dur": None, "dim": None})
        self.timeline = self.timeline * 2
        for i, t in enumerate(self.timeline):
            t["ini"] = i * 2.4
        self.cfg["escena_timeout_seg"] = 25
        st = render.crear_video_escenas(self.timeline, self.medios, self.wav, None, self.tmp / "c.mp4", (320, 180),
                                        self.cfg, FFMPEG, FFPROBE, LogFalso(), None, False, 2)
        self.assertGreaterEqual(len(st["medios_omitidos"]), 0)
        self.assertTrue((self.tmp / "c.mp4").exists())


FAKE_EDGE = """
import asyncio
class Communicate:
    def __init__(self, text, voice, rate="+0%", volume="+0%", pitch="+0Hz", boundary="SentenceBoundary"):
        self.text, self.voice, self.boundary = text, voice, boundary
    async def stream(self):
        yield {"type": "audio", "data": open(MP3, "rb").read()}
        for i, w in enumerate(self.text.split()):
            yield {"type": "WordBoundary", "offset": int(i * 3e6), "duration": int(2.5e6), "text": w}
"""


@unittest.skipUnless(FFMPEG, "FFmpeg no instalado")
class TestEdgeTts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        mp3 = self.tmp / "fake.mp3"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=1.5", "-c:a",
                        "libmp3lame", str(mp3)], check=True)
        mod = types.ModuleType("edge_tts")
        exec(FAKE_EDGE, {"MP3": str(mp3), **mod.__dict__}, mod.__dict__)
        self._previo = sys.modules.get("edge_tts")
        sys.modules["edge_tts"] = mod
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        if self._previo is None:
            sys.modules.pop("edge_tts", None)
        else:
            sys.modules["edge_tts"] = self._previo

    def test_motor_y_sintesis_por_escenas(self):
        from agente import voz_edge
        cfg = cargar_config(RAIZ / "config.json")
        cfg["edge_pausa_seg"] = 0
        m = voz_edge.MotorEdge(cfg, LogFalso(), FFMPEG)
        self.assertTrue(m.voz_es)
        texto = "Primera frase del párrafo uno. Segunda frase algo más larga.\n\nSegundo párrafo con otra idea distinta."
        meta = voz.sintetizar_texto(m, texto, self.tmp / "n.wav", LogFalso(), "t", max_chars=45, por_parrafo=True)
        self.assertGreaterEqual(len(meta["trozos"]), 3)
        self.assertTrue(voz.wav_valido(self.tmp / "n.wav"))
        self.assertTrue(all(t["limites"] for t in meta["trozos"]))   # se guardaron las marcas de palabras
        pal = alineacion.desde_limites(meta["trozos"][0]["texto"], meta["trozos"][0]["limites"], 1.4)
        self.assertEqual(len(pal), len(meta["trozos"][0]["texto"].split()))

    def test_sin_el_paquete_cae_en_error_claro(self):
        from agente import voz_edge
        sys.modules["edge_tts"] = None                                 # simula "no instalado"
        with self.assertRaises(voz.VozError):
            voz_edge.MotorEdge(cargar_config(RAIZ / "config.json"), LogFalso(), FFMPEG)


class ComfyFalso(BaseHTTPRequestHandler):
    recibido = []

    def log_message(self, *a):
        pass

    def _send(self, cuerpo, tipo="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path.startswith("/system_stats"):
            return self._send(b"{}")
        if self.path.startswith("/history/"):
            return self._send(json.dumps({"abc": {"outputs": {"9": {"images": [
                {"filename": "x.png", "subfolder": "", "type": "output"}]}}}}).encode())
        if self.path.startswith("/view"):
            return self._send(b"\x89PNG\r\n\x1a\n" + b"0" * 2000, "image/png")
        self.send_error(404)

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        ComfyFalso.recibido.append(json.loads(self.rfile.read(n)))
        self._send(json.dumps({"prompt_id": "abc"}).encode())


class TestComfy(unittest.TestCase):
    def test_generar_con_servidor_falso(self):
        srv = HTTPServer(("127.0.0.1", 0), ComfyFalso)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        with tempfile.TemporaryDirectory() as d:
            wf = Path(d) / "wf.json"
            wf.write_text(json.dumps({"6": {"inputs": {"text": "", "clip": ["4", 1]}},
                                      "3": {"inputs": {"seed": 1, "steps": 20}}}), encoding="utf-8")
            cfgc = {"url": f"http://127.0.0.1:{srv.server_port}", "workflow": str(wf), "nodo_texto": "6",
                    "max_por_tema": 2, "timeout_imagen_seg": 10}
            rutas = comfy.generar_imagenes(cfgc, ["a quiet harbour at dawn", "an old observatory", "tercera"],
                                           Path(d) / "gen", LogFalso())
            self.assertEqual(len(rutas), 2)                            # respeta max_por_tema
            self.assertTrue(all(r.exists() and r.stat().st_size > 1000 for r in rutas))
            enviado = ComfyFalso.recibido[0]["prompt"]
            self.assertIn("a quiet harbour at dawn", enviado["6"]["inputs"]["text"])
            self.assertIn("no text", enviado["6"]["inputs"]["text"])
            self.assertEqual(enviado["6"]["inputs"]["clip"], ["4", 1])   # no tocó los enlaces entre nodos

    def test_no_disponible_no_rompe(self):
        with tempfile.TemporaryDirectory() as d:
            r = comfy.generar_imagenes({"url": "http://127.0.0.1:9", "workflow": str(Path(d) / "no.json"),
                                        "nodo_texto": "6"}, ["x"], Path(d) / "g", LogFalso())
            self.assertEqual(r, [])


class TestCalidad(unittest.TestCase):
    def test_filtra_logos_iconos_y_qr(self):
        rutas = ["a/app-store-cnn-app-qr-code.png", "a/facebook.png", "a/x.png", "a/instagram.png",
                 "a/google-play-cnn-app-qr-code.png", "a/gettyimages-2272964108.jpg", "a/p0pj4pmf.jpg.webp",
                 "a/foto-cabo-polonio.jpg", "a/logo-empresa.png", "a/luis-felipe-delgado-aparicio.jpg"]
        limpias, descartadas = calidad.filtrar_basura(rutas)
        self.assertEqual(len(descartadas), 6)
        self.assertIn("a/foto-cabo-polonio.jpg", limpias)
        self.assertIn("a/gettyimages-2272964108.jpg", limpias)         # esto es riesgo de licencia, no basura

    def test_riesgo_de_licencias_por_nombre(self):
        r = calidad.riesgo_licencias(["gettyimages-1.jpg", "p08g23jv.jpg.webp", "afp_2024_x.jpg", "nasa-titan.jpg",
                                      "mi-foto.jpg"])
        self.assertEqual(len(r["agencia"]), 3)
        self.assertEqual(r["nasa"], ["nasa-titan.jpg"])
        self.assertEqual(r["proporcion_agencia"], 0.6)

    def test_similitud_y_plantilla(self):
        base = " ".join(f"palabra{i}" for i in range(200))
        guiones = {"a": base + " cierre uno distinto", "b": base + " otro final diferente",
                   "c": " ".join(f"tema{i} solo{i}" for i in range(100))}
        sim = calidad.similitud_lote(guiones)
        self.assertGreater(sim["a"]["max_similitud"], 0.9)
        self.assertEqual(sim["a"]["con"], "b")
        self.assertLess(sim["c"]["max_similitud"], 0.05)
        rep = calidad.aperturas_repetidas({"a": "En este video vamos a ver x", "b": "En este video vamos a ver y",
                                           "c": "En este video vamos a ver z", "d": "Otra apertura distinta aquí"})
        self.assertEqual(len(rep), 1)

    def test_numeros_sin_fuente(self):
        r = calidad.numeros_sin_fuente("Ocurrió en 1998 y costó 4.500 millones; hubo 37 muertos y 2024 fue clave.",
                                       "El suceso de 1998 fue importante. En 2024 se reabrió.")
        self.assertEqual(r, ["4.500"])

    def test_evaluar_tema_niveles(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "t"
            out.mkdir()
            (out / "guion_largo.txt").write_text("En 1850 ocurrió algo importante que cambió todo.", encoding="utf-8")
            (out / "investigacion.json").write_text(json.dumps({"fuentes": [{"titulo": "A", "resumen": "algo en 1850"}]}),
                                                    encoding="utf-8")
            (out / "informe.json").write_text(json.dumps({
                "tema": "T", "carpeta_origen": str(Path(d) / "origen"),
                "calidad_medios": {"licencias": {"proporcion_agencia": 0.6}}, "video": {}}), encoding="utf-8")
            r = calidad.evaluar_tema({"descripcion_aviso": "IA"}, out)
            self.assertEqual(r["nivel"], "riesgo")
            self.assertTrue(any("agencias" in m for m in r["motivos"]))
            self.assertTrue((out / "informe_calidad.json").exists())
            (Path(d) / "origen").mkdir()
            (Path(d) / "origen" / "aporte.txt").write_text("Mi opinión original sobre el tema.", encoding="utf-8")
            (out / "informe.json").write_text(json.dumps({"tema": "T", "carpeta_origen": str(Path(d) / "origen"),
                                                          "calidad_medios": {"licencias": {"proporcion_agencia": 0.0}},
                                                          "video": {}}), encoding="utf-8")
            r2 = calidad.evaluar_tema({"descripcion_aviso": "IA"}, out)
            self.assertEqual(r2["nivel"], "ok", r2)


class TestEstructurasYAporte(unittest.TestCase):
    def test_estructuras_variadas_y_estables(self):
        cfg = cargar_config(RAIZ / "config.json")
        with tempfile.TemporaryDirectory() as d:
            nombres = set()
            for i in range(30):
                p = Path(d) / f"t{i}"
                p.mkdir()
                secs, g, nombre = guion.elegir_estructura(f"Tema número {i}", p, cfg)
                nombres.add(nombre)
                self.assertEqual(len(secs), 7)
                self.assertAlmostEqual(sum(s[1] for s in secs), 1.0, places=2)
                self.assertEqual(guion.elegir_estructura(f"Tema número {i}", p, cfg)[2], nombre)   # estable al reanudar
            self.assertGreaterEqual(len(nombres), 4)
            cfg["estructura_variable"] = False
            p = Path(d) / "fijo"
            p.mkdir()
            self.assertEqual(guion.elegir_estructura("cualquiera", p, cfg)[2], "clasica")

    def test_aporte_del_autor_se_incluye_antes_de_la_conclusion(self):
        cfg = cargar_config(RAIZ / "config.json")
        cfg.update(palabras_objetivo=1450)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)

            def llm(cfg, sis, prompt, mt=0, temp=0):
                import random
                n = int(__import__("re").search(r"unas (\d+) palabras", prompt).group(1)) if "unas" in prompt else 100
                voc = "de la el que en y los las un una se por con para es del historia mundo planeta ciencia tiempo".split()
                r = random.Random(len(prompt) + n)
                return " ".join(r.choice(voc) + (str(r.randint(0, 9999)) if r.random() < .2 else "") for _ in range(n)) + "."
            aporte = "Mi experiencia personal: yo viví esto de cerca y opino algo muy distinto."
            r = guion.generar_guion_largo(cfg, "Tema", out, "", LogFalso(), llm, aporte=aporte)
            texto = guion.ensamblar(out / "partes_guion")
            self.assertIn("Mi experiencia personal", texto)
            self.assertLess(texto.index("Mi experiencia personal"), texto.index(
                (out / "partes_guion" / "parte_07.txt").read_text(encoding="utf-8")[:40]))
            self.assertGreater(r.get("aporte_palabras", 0), 5)
            self.assertIn("estructura", r)



class TestCorreccionesUsuario(unittest.TestCase):
    """Problemas reportados: sin subtítulos en el largo, efectos poco perceptibles, voz sin verificar, vídeo antiguo."""

    def test_valores_por_defecto(self):
        cfg = cargar_config(RAIZ / "config.json")
        self.assertTrue(cfg["quemar_subtitulos_largo"])
        self.assertTrue(cfg["quemar_subtitulos_short"])
        self.assertEqual(cfg["transicion"], "xfade")
        self.assertGreaterEqual(cfg["zoom_intensidad"], 0.25)
        from agente.utils import CONFIG_DEFECTO
        self.assertTrue(CONFIG_DEFECTO["quemar_subtitulos_largo"])      # también sin config.json antiguo del usuario

    def test_foco_distinto_en_cada_aparicion_y_estable(self):
        a = render.foco_de_escena("img.jpg", 9.12, "zoom_in")
        self.assertEqual(a, render.foco_de_escena("img.jpg", 9.12, "zoom_in"))
        otros = {render.foco_de_escena("img.jpg", 5 + k * 0.37, "zoom_in") for k in range(10)}
        self.assertGreater(len(otros), 5)
        f = render.filtro_imagen(1280, 720, 24, 9.0, "zoom_in", (1920, 1080), {"zoom_intensidad": 0.3}, foco=(0.2, 0.7))
        self.assertIn("(iw-iw/zoom)*0.2", f)
        self.assertIn("1+0.3*on/216", f)

    @unittest.skipUnless(FFMPEG, "FFmpeg no instalado")
    def test_efectos_de_sonido_con_volumen_audible(self):
        with tempfile.TemporaryDirectory() as d:
            wavs = render.preparar_sfx(FFMPEG, Path(d) / "assets", Path(d), 24000)
            for nombre, minimo_seg in (("whoosh", 0.7), ("pop", 0.15)):
                with wave.open(str(wavs[nombre]), "rb") as w:
                    self.assertGreaterEqual(w.getnframes() / 24000.0, minimo_seg)
                    import array
                    a = array.array("h")
                    a.frombytes(w.readframes(w.getnframes()))
                self.assertGreaterEqual(max(abs(x) for x in a), 0.85 * 32767)   # pico alto: se oye sobre la voz

    def test_voz_distinta_no_reutiliza_trozos_de_otra(self):
        class Falso:
            def __init__(self, tipo, voz, falla_en=None):
                self.tipo, self.voz, self.llamadas, self.falla_en = tipo, voz, 0, falla_en
                self.voz_es = True

            def sintetizar(self, texto, destino):
                self.llamadas += 1
                if self.falla_en and self.llamadas == self.falla_en:
                    raise voz.VozError("corte a mitad")
                voz.MotorVoz._silencio(texto, destino)
        texto = "Frase uno de la prueba. Frase dos de la prueba. Frase tres de la prueba. Frase cuatro de la prueba."
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "n.wav"
            a = Falso("local", "voz básica", falla_en=3)
            with self.assertRaises(voz.VozError):
                voz.sintetizar_texto(a, texto, destino, LogFalso(), "t", max_chars=30, por_parrafo=True)
            b = Falso("edge-tts", "es-MX-JorgeNeural")
            meta = voz.sintetizar_texto(b, texto, destino, LogFalso(), "t", max_chars=30, por_parrafo=True)
            self.assertEqual(b.llamadas, len(meta["trozos"]))            # NINGÚN trozo de la voz básica se reutilizó
            self.assertEqual(meta["voz"], "es-MX-JorgeNeural")

    def test_lista_se_actualiza_aunque_el_video_se_reemplace(self):
        from agente import publicacion
        with tempfile.TemporaryDirectory() as d:
            origen, destino = Path(d) / "v.mp4", Path(d) / "lista.mp4"
            origen.write_bytes(b"A" * 1000)
            publicacion._poner_archivo(origen, destino, True)
            tmp = Path(d) / "nuevo.mp4"
            tmp.write_bytes(b"B" * 1000)                                 # mismo tamaño, distinto contenido
            os.replace(tmp, origen)                                      # lo que hace el render (inodo nuevo)
            publicacion._poner_archivo(origen, destino, True)
            self.assertEqual(destino.read_bytes()[:1], b"B")

    @unittest.skipUnless(FFMPEG, "FFmpeg no instalado")
    def test_probar_voz_genera_muestras(self):
        import main as principal
        mp3 = Path(tempfile.mkdtemp()) / "f.mp3"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=1", "-c:a", "libmp3lame",
                        str(mp3)], check=True)
        mod = types.ModuleType("edge_tts")
        exec(FAKE_EDGE, {"MP3": str(mp3), **mod.__dict__}, mod.__dict__)
        previo = sys.modules.get("edge_tts")
        sys.modules["edge_tts"] = mod
        cfg = cargar_config(RAIZ / "config.json")
        cfg["edge_pausa_seg"] = 0
        ctx = principal.Contexto(cfg, LogFalso(), Path("."))
        try:
            self.assertEqual(principal.probar_voz(cfg, ctx), 0)
        finally:
            if previo is None:
                sys.modules.pop("edge_tts", None)
            else:
                sys.modules["edge_tts"] = previo
            shutil.rmtree(RAIZ / "muestras_voz", ignore_errors=True)


class TestDuracionObjetivo(unittest.TestCase):
    def _partes(self, d):
        pd = d / "partes_guion"
        pd.mkdir(parents=True)
        (pd / "estructura.json").write_text(json.dumps({"nombre": "clasica", "n": 7}), encoding="utf-8")

        def parrafos(n_palabras, marca):
            frases = [f"{marca} frase {k} " + "palabra " * 17 + "final." for k in range(n_palabras // 20)]
            return "\n\n".join(" ".join(frases[i:i + 4]) for i in range(0, len(frases), 4))
        tam = {1: 200, 2: 300, 3: 300, 4: 300, 5: 300, 6: 300, 7: 200}
        for i, n in tam.items():
            (pd / f"parte_{i:02d}.txt").write_text(parrafos(n, f"S{i}"), encoding="utf-8")
        (pd / "ampliacion_01.txt").write_text(parrafos(160, "AMP"), encoding="utf-8")
        return pd

    def test_recorte_conserva_intro_y_conclusion_y_quita_ampliaciones_primero(self):
        with tempfile.TemporaryDirectory() as d:
            pd = self._partes(Path(d))
            total = contar_palabras(guion.ensamblar(pd))
            self.assertGreater(total, 1800)
            texto, info = guion.ensamblar_con_limite(pd, 1750, 1060)
            self.assertNotIn("AMP frase", texto)                                  # primero se descarta la ampliación
            self.assertLessEqual(contar_palabras(texto), 1750)
            texto2, info2 = guion.ensamblar_con_limite(pd, 1300, 1060)
            self.assertLessEqual(contar_palabras(texto2), 1300)
            self.assertIn("S1 frase 0", texto2)                                   # introducción intacta
            self.assertIn("S7 frase 9", texto2)                                   # conclusión completa
            self.assertGreater(info2["quitadas"], 400)
            self.assertTrue(texto2.rstrip().endswith("final."))                   # corta en frases completas
            texto3, _ = guion.ensamblar_con_limite(pd, 500, 1060)                 # nunca baja del piso de 1.000 palabras
            self.assertGreaterEqual(contar_palabras(texto3), 1000)
            self.assertEqual(contar_palabras(guion.ensamblar(pd)), total)         # las partes guardadas no se tocan

    def test_el_modelo_que_se_pasa_de_largo_se_corrige_solo(self):
        cfg = cargar_config(RAIZ / "config.json")
        cfg.update(duracion_objetivo_min=10, palabras_por_minuto=150)

        def llm(cfg_, sis, prompt, mt=0, temp=0):
            import random, re
            n = int(re.search(r"unas (\d+) palabras", prompt).group(1)) if "unas" in prompt else 100
            voc = "de la el que en y los las un una se por con para es del historia mundo planeta ciencia tiempo".split()
            r = random.Random(len(prompt))
            pal = [r.choice(voc) + (str(r.randint(0, 9999)) if r.random() < .25 else "") for _ in range(int(n * 1.7))]
            frases = [" ".join(pal[i:i + 15]).capitalize() + "." for i in range(0, len(pal), 15)]
            return "\n\n".join(" ".join(frases[i:i + 4]) for i in range(0, len(frases), 4))
        with tempfile.TemporaryDirectory() as d:
            r = guion.generar_guion_largo(cfg, "Tema", Path(d), "", LogFalso(), llm)
            self.assertTrue(r["completo"], r)
            self.assertLessEqual(r["palabras"], 1500)                              # 10 min x 150 palabras/min
            self.assertGreater(r["palabras"], 1000)

    def test_ritmo_medido_se_recuerda(self):
        cfg = cargar_config(RAIZ / "config.json")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "partes_guion").mkdir()
            self.assertEqual(guion.wpm_efectivo(cfg, d), 150.0)
            self.assertEqual(guion.limite_palabras(cfg, d)[1], 1500)
            guion.guardar_ritmo(d, 170)
            self.assertEqual(guion.limite_palabras(cfg, d)[1], 1700)
            cfg["duracion_objetivo_min"] = 0
            self.assertEqual(guion.limite_palabras(cfg, d), (1450, None))

if __name__ == "__main__":
    unittest.main(verbosity=2)

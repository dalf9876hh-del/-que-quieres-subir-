# -*- coding: utf-8 -*-
"""Pruebas de Kokoro, chat_voz.py y ollama_pptx.py con módulos FALSOS de kokoro_onnx, soundfile y ollama
(no se carga ningún modelo real). python-pptx sí es el real: se abre el .pptx generado para comprobarlo."""
import builtins
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["AGENTE_TEST_TTS"] = "1"

import numpy as np  # noqa: E402

import chat_voz  # noqa: E402
import ollama_pptx  # noqa: E402
from agente import ollama_cli, voz, voz_kokoro  # noqa: E402
from agente.utils import cargar_config  # noqa: E402


class LogFalso:
    def __getattr__(self, n):
        return lambda *a, **k: None


def modulo_kokoro(llamadas):
    mod = types.ModuleType("kokoro_onnx")

    class Kokoro:
        def __init__(self, modelo, voces):
            self.modelo, self.voces = modelo, voces

        def get_voices(self):
            return ["af_sarah", "ef_dora", "em_alex", "em_santa"]

        def create(self, texto, voice="af_sarah", speed=1.0, lang="en-us"):
            llamadas.append({"texto": texto, "voice": voice, "speed": speed, "lang": lang})
            n = int(24000 * (0.2 + 0.01 * len(texto)) / speed)
            t = np.arange(n) / 24000.0
            return (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 24000
    mod.Kokoro = Kokoro
    return mod


def modulo_soundfile():
    mod = types.ModuleType("soundfile")

    def write(ruta, datos, sr):
        pcm = (np.clip(np.asarray(datos), -1, 1) * 32767).astype("<i2")
        with wave.open(str(ruta), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
    mod.write = write
    return mod


def modulo_ollama(modelos=("llama3.2:3b",), respuestas=(), apagado=False):
    mod = types.ModuleType("ollama")
    mod.llamadas = []
    cola = list(respuestas)

    class ResponseError(Exception):
        def __init__(self, error, status_code=404):
            super().__init__(error)
            self.status_code = status_code
    mod.ResponseError = ResponseError

    def list_():
        if apagado:
            raise ConnectionError("Failed to connect to Ollama. Please check that Ollama is downloaded, running")
        return {"models": [{"model": m} for m in modelos]}

    def chat(model, messages, **kw):
        mod.llamadas.append({"model": model, "messages": [dict(m) for m in messages], **kw})
        if apagado:
            raise ConnectionError("Failed to connect to Ollama.")
        if model not in modelos:
            raise ResponseError(f"model '{model}' not found", 404)
        r = cola.pop(0) if cola else "Respuesta de prueba."
        return {"message": {"role": "assistant", "content": r}}
    mod.list, mod.chat = list_, chat
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kok = []
        self.previos = {k: sys.modules.get(k) for k in ("kokoro_onnx", "soundfile", "ollama", "pptx")}
        sys.modules["kokoro_onnx"] = modulo_kokoro(self.kok)
        sys.modules["soundfile"] = modulo_soundfile()
        self.addCleanup(self._restaurar)
        self.m = self.tmp / "kokoro-v1.0.onnx"
        self.v = self.tmp / "voices-v1.0.bin"
        self.m.write_bytes(b"x")
        self.v.write_bytes(b"x")
        for k in ("KOKORO_MODEL", "KOKORO_VOICES", "KOKORO_VOZ", "KOKORO_SPEED", "OLLAMA_MODEL", "KOKORO_LANG"):
            self._env(k, None)

    def _env(self, k, valor):
        previo = os.environ.get(k)
        if valor is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = valor
        self.addCleanup(lambda: os.environ.__setitem__(k, previo) if previo is not None else os.environ.pop(k, None))

    def _restaurar(self):
        for k, v in self.previos.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def kokoro_args(self):
        return ["--kokoro-modelo", str(self.m), "--kokoro-voces", str(self.v)]


class TestKokoro(Base):
    def test_archivos_faltantes_dan_mensaje_en_espanol(self):
        with self.assertRaises(voz_kokoro.KokoroError) as cm:
            voz_kokoro.localizar_archivos(str(self.tmp / "no.onnx"), str(self.tmp / "no.bin"))
        msg = str(cm.exception)
        self.assertIn("kokoro-v1.0.onnx", msg)
        self.assertIn("voices-v1.0.bin", msg)
        self.assertIn("KOKORO_MODEL", msg)
        self.assertNotIn("Traceback", msg)

    def test_variables_de_entorno_y_carpeta(self):
        self._env("KOKORO_MODEL", str(self.tmp))                      # una carpeta: se añade el nombre del archivo
        self._env("KOKORO_VOICES", str(self.v))
        m, v = voz_kokoro.localizar_archivos()
        self.assertEqual((m, v), (self.m, self.v))

    def test_genera_wav_con_voz_velocidad_e_idioma(self):
        tts = voz_kokoro.KokoroTTS(str(self.m), str(self.v), "em_alex", 1.2, "es")
        ruta = tts.guardar("Hola, esto es una prueba.", self.tmp / "salida" / "a.wav")
        self.assertTrue(voz.wav_valido(ruta))
        self.assertEqual(self.kok[0]["voice"], "em_alex")
        self.assertEqual(self.kok[0]["lang"], "es")
        self.assertEqual(self.kok[0]["speed"], 1.2)
        with wave.open(str(ruta), "rb") as w:
            self.assertEqual(w.getframerate(), 24000)

    def test_velocidad_se_limita_y_voz_invalida_da_error_claro(self):
        self.assertEqual(voz_kokoro.KokoroTTS(str(self.m), str(self.v), "ef_dora", 9, "es").velocidad, 2.0)
        self.assertEqual(voz_kokoro.KokoroTTS(str(self.m), str(self.v), "ef_dora", 0.1, "es").velocidad, 0.5)
        with self.assertRaises(voz_kokoro.KokoroError) as cm:
            voz_kokoro.KokoroTTS(str(self.m), str(self.v), "voz_rara", 1.0, "es")
        self.assertIn("ef_dora", str(cm.exception))
        with self.assertRaises(voz_kokoro.KokoroError):
            voz_kokoro.KokoroTTS(str(self.m), str(self.v), "ef_dora", "rápido", "es")

    def test_texto_largo_se_divide_y_se_une(self):
        tts = voz_kokoro.KokoroTTS(str(self.m), str(self.v), "ef_dora", 1.0, "es")
        texto = " ".join(f"Esta es la frase número {i} de un texto bastante largo." for i in range(30))
        muestras, sr = tts.audio(texto)
        self.assertGreater(len(self.kok), 3)
        self.assertTrue(all(len(c["texto"]) <= 300 for c in self.kok))
        self.assertGreater(len(muestras) / sr, 5)

    def test_limpieza_para_voz(self):
        t = voz_kokoro.limpiar_para_voz("## Título\n- **Hola** mundo 😀 visita https://x.com/a ahora\n`codigo`")
        for malo in ("#", "*", "😀", "http", "`"):
            self.assertNotIn(malo, t)
        self.assertIn("Hola mundo", t)

    def test_falta_el_paquete(self):
        sys.modules["kokoro_onnx"] = None
        with self.assertRaises(voz_kokoro.KokoroError) as cm:
            voz_kokoro.KokoroTTS(str(self.m), str(self.v))
        self.assertIn("pip install", str(cm.exception))

    def test_motor_para_el_agente_de_documentales(self):
        cfg = cargar_config(RAIZ / "config.json")
        cfg.update(kokoro_modelo=str(self.m), kokoro_voces=str(self.v), kokoro_voz="em_santa", motor_voz="kokoro")
        import main as principal
        ctx = principal.Contexto(cfg, LogFalso(), self.tmp)
        motor = principal.crear_motor(cfg, ctx)
        self.assertEqual((motor.tipo, motor.voz, motor.voz_es), ("kokoro", "em_santa", True))
        meta = voz.sintetizar_texto(motor, "Primera frase del documental. Segunda frase.\n\nOtro párrafo distinto.",
                                    self.tmp / "n.wav", LogFalso(), "t", max_chars=40, por_parrafo=True)
        self.assertTrue(voz.wav_valido(self.tmp / "n.wav"))
        self.assertEqual(meta["voz"], "em_santa")
        self.assertGreaterEqual(len(meta["trozos"]), 2)

    def test_modo_auto_usa_kokoro_si_edge_no_esta(self):
        sys.modules["edge_tts"] = None
        self.addCleanup(lambda: sys.modules.pop("edge_tts", None))
        cfg = cargar_config(RAIZ / "config.json")
        cfg.update(kokoro_modelo=str(self.m), kokoro_voces=str(self.v), motor_voz="auto")
        import main as principal
        motor = principal.crear_motor(cfg, principal.Contexto(cfg, LogFalso(), self.tmp))
        self.assertEqual(motor.tipo, "kokoro")
        cfg.update(kokoro_modelo=str(self.tmp / "no.onnx"))              # sin archivos -> voz local, sin error
        motor2 = principal.crear_motor(cfg, principal.Contexto(cfg, LogFalso(), self.tmp))
        self.assertNotEqual(motor2.tipo, "kokoro")



class TestInstruccionesLiterales(Base):
    """Rutas, voz, idioma y nombre de archivo EXACTOS pedidos por el usuario."""

    def test_constantes_exactas(self):
        self.assertEqual(voz_kokoro.RUTA_MODELO, "C:\\Users\\USER\\Desktop\\kokoro_test\\kokoro-v1.0.onnx")
        self.assertEqual(voz_kokoro.RUTA_VOCES, "C:\\Users\\USER\\Desktop\\kokoro_test\\voices-v1.0.bin")
        self.assertEqual((voz_kokoro.VOZ, voz_kokoro.LANG, voz_kokoro.VELOCIDAD), ("ef_dora", "es", 1.0))

    def test_la_ruta_exacta_tiene_prioridad_y_si_no_existe_se_busca(self):
        previos = (voz_kokoro.RUTA_MODELO, voz_kokoro.RUTA_VOCES)
        self.addCleanup(lambda: (setattr(voz_kokoro, "RUTA_MODELO", previos[0]), setattr(voz_kokoro, "RUTA_VOCES", previos[1])))
        carpeta = self.tmp / "kokoro_test"
        carpeta.mkdir()
        (carpeta / "kokoro-v1.0.onnx").write_bytes(b"x")
        (carpeta / "voices-v1.0.bin").write_bytes(b"x")
        voz_kokoro.RUTA_MODELO, voz_kokoro.RUTA_VOCES = str(carpeta / "kokoro-v1.0.onnx"), str(carpeta / "voices-v1.0.bin")
        self.assertEqual(voz_kokoro.localizar_archivos(), (carpeta / "kokoro-v1.0.onnx", carpeta / "voices-v1.0.bin"))
        voz_kokoro.RUTA_MODELO = str(self.tmp / "no_existe" / "kokoro-v1.0.onnx")      # cae a la búsqueda (carpeta actual)
        previo = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, previo)
        (self.tmp / "kokoro-v1.0.onnx").write_bytes(b"x")
        self.assertEqual(voz_kokoro.localizar_archivos()[0], Path.cwd() / "kokoro-v1.0.onnx")

    def test_llamada_exacta_a_create_y_a_sf_write(self):
        tts = voz_kokoro.KokoroTTS(str(self.m), str(self.v))                           # sin más argumentos: valores exactos
        tts.guardar("Hola.", self.tmp / "audio_generado.wav")
        self.assertEqual(self.kok[0], {"texto": "Hola.", "voice": "ef_dora", "speed": 1.0, "lang": "es"})
        self.assertTrue(voz.wav_valido(self.tmp / "audio_generado.wav"))

    def test_probar_kokoro_py_hace_lo_pedido(self):
        import runpy
        previo = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, previo)
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            runpy.run_path(str(RAIZ / "probar_kokoro.py"))
        self.assertTrue((self.tmp / "audio_generado.wav").exists())
        self.assertEqual(self.kok[0]["voice"], "ef_dora")
        self.assertEqual(self.kok[0]["lang"], "es")
        self.assertIn("[OK] audio_generado.wav creado", salida.getvalue())

    def test_auto_prefiere_kokoro_y_no_toca_edge(self):
        from agente import voz_edge
        import main as principal
        cfg = cargar_config(RAIZ / "config.json")
        cfg.update(motor_voz="auto", kokoro_modelo=str(self.m), kokoro_voces=str(self.v))
        previo = voz_edge.MotorEdge

        def prohibido(*a, **k):
            raise AssertionError("no debía intentarse edge-tts")
        voz_edge.MotorEdge = prohibido
        self.addCleanup(lambda: setattr(voz_edge, "MotorEdge", previo))
        motor = principal.crear_motor(cfg, principal.Contexto(cfg, LogFalso(), self.tmp))
        self.assertEqual((motor.tipo, motor.voz), ("kokoro", "ef_dora"))

    def test_si_kokoro_falla_en_auto_se_avisa_el_motivo(self):
        import main as principal
        sys.modules["kokoro_onnx"] = None                                              # simula "este Python no tiene kokoro_onnx"
        self.addCleanup(lambda: sys.modules.pop("edge_tts", None))
        sys.modules["edge_tts"] = None
        cfg = cargar_config(RAIZ / "config.json")
        cfg.update(motor_voz="auto", kokoro_modelo=str(self.m), kokoro_voces=str(self.v))
        motor = principal.crear_motor(cfg, principal.Contexto(cfg, LogFalso(), self.tmp))
        self.assertNotEqual(motor.tipo, "kokoro")
        self.assertTrue(any("Kokoro no se pudo usar" in a for a in motor.advertencias), motor.advertencias)

    def test_cambiar_de_voz_rehace_los_temas_ya_terminados(self):
        import main as principal
        out = self.tmp / "tema"
        out.mkdir()
        for f in principal.ENTREGABLES:
            (out / f).write_bytes(b"x")
        (out / "informe.json").write_text(json.dumps({"estado": "completado", "voz": {"motor": "edge-tts",
                                                       "voz": "es-MX-JorgeNeural"}}), encoding="utf-8")

        class M:
            tipo, voz = "kokoro", "ef_dora"
        self.assertTrue(principal.tema_completado(out))                                # sin motor: criterio anterior
        self.assertFalse(principal.tema_completado(out, M))                            # otra voz -> se rehace
        M.tipo, M.voz = "edge-tts", "es-MX-JorgeNeural"
        self.assertTrue(principal.tema_completado(out, M))                             # misma voz -> se omite

class TestChatVoz(Base):
    def _ejecutar(self, entradas, argv, ollama_mod):
        sys.modules["ollama"] = ollama_mod
        it = iter(entradas)
        original = builtins.input
        builtins.input = lambda *a, **k: next(it)
        reproducidos = []
        previo = voz_kokoro.reproducir_wav
        voz_kokoro.reproducir_wav = lambda ruta: reproducidos.append(str(ruta)) or True
        salida = io.StringIO()
        try:
            with contextlib.redirect_stdout(salida):
                rc = chat_voz.main(argv)
        finally:
            builtins.input = original
            voz_kokoro.reproducir_wav = previo
        return rc, salida.getvalue(), reproducidos

    def test_conversacion_por_turnos_hasta_salir(self):
        om = modulo_ollama(respuestas=["Hola, ¿en qué te ayudo?", "Estoy muy bien, gracias."])
        carpeta = self.tmp / "audio"
        rc, texto, reprod = self._ejecutar(["Hola", "", "¿Cómo estás?", "SALIR"],
                                           ["--carpeta-audio", str(carpeta), "--voz", "ef_dora", "--velocidad", "1.1"]
                                           + self.kokoro_args(), om)
        self.assertEqual(rc, 0)
        self.assertIn("Asistente: Hola, ¿en qué te ayudo?", texto)
        self.assertIn("Hasta luego.", texto)
        self.assertEqual(sorted(p.name for p in carpeta.glob("*.wav")), ["respuesta_0001.wav", "respuesta_0002.wav"])
        self.assertEqual(len(reprod), 2)                                  # reproduce cada respuesta
        self.assertEqual(len(om.llamadas), 2)                             # la línea vacía no llama al modelo
        sistema = om.llamadas[0]["messages"][0]
        self.assertEqual(sistema["role"], "system")
        self.assertIn("español", sistema["content"])
        self.assertIn("No uses emojis", sistema["content"])
        self.assertEqual([m["role"] for m in om.llamadas[1]["messages"]], ["system", "user", "assistant", "user"])
        self.assertEqual(self.kok[0]["voice"], "ef_dora")
        self.assertEqual(self.kok[0]["speed"], 1.1)

    def test_ollama_apagado_mensaje_claro(self):
        rc, texto, _ = self._ejecutar([], ["--sin-voz"], modulo_ollama(apagado=True))
        self.assertEqual(rc, 1)
        self.assertIn("No se pudo conectar con Ollama", texto)
        self.assertIn("ollama serve", texto)
        self.assertNotIn("Traceback", texto)

    def test_modelo_inexistente_mensaje_claro(self):
        rc, texto, _ = self._ejecutar([], ["--sin-voz", "--modelo", "no-existe:1b"], modulo_ollama())
        self.assertEqual(rc, 1)
        self.assertIn("'no-existe:1b' no está instalado", texto)
        self.assertIn("ollama pull no-existe:1b", texto)
        self.assertIn("llama3.2:3b", texto)                                # lista los modelos que sí hay

    def test_faltan_archivos_de_kokoro(self):
        rc, texto, _ = self._ejecutar([], ["--kokoro-modelo", str(self.tmp / "x.onnx"),
                                           "--kokoro-voces", str(self.tmp / "x.bin")], modulo_ollama())
        self.assertEqual(rc, 2)
        self.assertIn("No se encontraron los archivos de Kokoro", texto)
        self.assertIn("--sin-voz", texto)

    def test_variables_de_entorno_eligen_voz_y_velocidad(self):
        self._env("KOKORO_VOZ", "em_santa")
        self._env("KOKORO_SPEED", "0.9")
        self._env("OLLAMA_MODEL", "llama3.2:3b")
        om = modulo_ollama()
        rc, texto, _ = self._ejecutar(["hola", "salir"], ["--carpeta-audio", str(self.tmp / "a"), "--sin-reproducir"]
                                      + self.kokoro_args(), om)
        self.assertEqual(rc, 0)
        self.assertEqual((self.kok[0]["voice"], self.kok[0]["speed"]), ("em_santa", 0.9))
        self.assertIn("Voz: em_santa", texto)

    def test_un_error_del_modelo_no_corta_la_conversacion(self):
        om = modulo_ollama(respuestas=["Primera respuesta."])
        calls = {"n": 0}
        chat_real = om.chat

        def chat(model, messages, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise om.ResponseError("fallo temporal", 500)
            return chat_real(model, messages, **kw)
        om.chat = chat
        rc, texto, _ = self._ejecutar(["uno", "dos", "tres", "salir"],
                                      ["--sin-voz"], om)
        self.assertEqual(rc, 0)
        self.assertIn("[ERROR]", texto)
        self.assertIn("Primera respuesta.", texto)
        self.assertEqual(texto.count("Asistente:"), 2)                    # uno y tres respondieron

    def test_ctrl_c_o_fin_de_entrada_termina_limpio(self):
        sys.modules["ollama"] = modulo_ollama()
        original = builtins.input

        def input_(*a, **k):
            raise KeyboardInterrupt
        builtins.input = input_
        salida = io.StringIO()
        try:
            with contextlib.redirect_stdout(salida):
                rc = chat_voz.main(["--sin-voz"])
        finally:
            builtins.input = original
        self.assertEqual(rc, 0)
        self.assertIn("Conversación terminada", salida.getvalue())


class TestOllamaPptx(Base):
    JSON_OK = json.dumps({"slides": [
        {"title": f"Diapositiva número {i}", "bullets": [f"Primer punto importante {i}", "Segundo punto relevante", "Tercer punto clave"],
         "notes": "Estas son las notas del orador para la diapositiva, con varias frases en español para que se lean en voz alta "
                  "durante la presentación y el público pueda seguir las ideas principales del tema que se está explicando."}
        for i in range(1, 5)]}, ensure_ascii=False)

    def _ejecutar(self, argv, ollama_mod):
        sys.modules["ollama"] = ollama_mod
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            rc = ollama_pptx.main(argv)
        return rc, salida.getvalue()

    def test_genera_pptx_con_titulo_vinetas_y_notas(self):
        om = modulo_ollama(respuestas=[self.JSON_OK])
        destino = self.tmp / "p.pptx"
        rc, texto = self._ejecutar(["El ciclo del agua", "-n", "4", "-o", str(destino)], om)
        self.assertEqual(rc, 0, texto)
        self.assertEqual(om.llamadas[0]["format"], "json")                 # salida JSON de Ollama
        from pptx import Presentation
        prs = Presentation(str(destino))
        self.assertEqual(len(prs.slides), 4)
        s = prs.slides[1]
        self.assertEqual(s.shapes.title.text, "Diapositiva número 2")
        cuerpo = [p.text for p in s.placeholders[1].text_frame.paragraphs]
        self.assertEqual(cuerpo, ["Primer punto importante 2", "Segundo punto relevante", "Tercer punto clave"])
        self.assertIn("notas del orador", s.notes_slide.notes_text_frame.text)
        self.assertAlmostEqual(prs.slide_width / prs.slide_height, 16 / 9, places=2)   # 16:9

    def test_json_con_errores_se_repara_y_json_invalido_se_reintenta(self):
        roto = '```json\n' + self.JSON_OK.replace('"}]', '"},]') + '\n```'      # coma final + markdown
        om = modulo_ollama(respuestas=["esto no es json", roto])
        destino = self.tmp / "q.pptx"
        rc, texto = self._ejecutar(["Tema", "-n", "4", "-o", str(destino)], om)
        self.assertEqual(rc, 0, texto)
        self.assertEqual(len(om.llamadas), 2)
        self.assertIn("Intento 1/3", texto)
        self.assertIn("problemas", om.llamadas[1]["messages"][-1]["content"])  # se le explicó qué corregir
        self.assertTrue(destino.exists())

    def test_tres_respuestas_invalidas_dan_error_claro(self):
        om = modulo_ollama(respuestas=["no", "tampoco", '{"slides": []}'])
        rc, texto = self._ejecutar(["Tema", "-o", str(self.tmp / "z.pptx")], om)
        self.assertEqual(rc, 1)
        self.assertIn("no devolvió un JSON válido tras 3 intentos", texto)
        self.assertNotIn("Traceback", texto)
        self.assertFalse((self.tmp / "z.pptx").exists())

    def test_validacion(self):
        sl, err = ollama_pptx.validar({"slides": [{"title": "T 😀", "bullets": "uno; dos", "notes": "n"},
                                                  {"title": "", "bullets": [], "notes": ""}]}, 2)
        self.assertEqual(sl[0]["title"], "T")
        self.assertEqual(sl[0]["bullets"], ["uno", "dos"])
        self.assertTrue(any("falta el título" in e for e in err))
        sl, err = ollama_pptx.validar([{"title": "a", "bullets": ["b"], "notes": "c"}], 3)
        self.assertTrue(any("se pidieron 3" in e for e in err))

    def test_con_voz_genera_un_wav_por_diapositiva(self):
        om = modulo_ollama(respuestas=[self.JSON_OK])
        destino = self.tmp / "v.pptx"
        rc, texto = self._ejecutar(["Tema", "-n", "4", "-o", str(destino), "--voz", "--voz-nombre", "em_alex"]
                                   + self.kokoro_args(), om)
        self.assertEqual(rc, 0, texto)
        wavs = sorted(p.name for p in (self.tmp / "v_audio").glob("*.wav"))
        self.assertEqual(wavs, [f"diapositiva_0{i}.wav" for i in range(1, 5)])
        self.assertTrue(all(c["voice"] == "em_alex" for c in self.kok))
        self.assertTrue(all("notas del orador" in c["texto"] for c in self.kok))     # lee las NOTAS

    def test_con_voz_y_sin_archivos_de_kokoro_no_gasta_el_modelo(self):
        om = modulo_ollama(respuestas=[self.JSON_OK])
        rc, texto = self._ejecutar(["Tema", "--voz", "--kokoro-modelo", str(self.tmp / "no.onnx"),
                                    "--kokoro-voces", str(self.tmp / "no.bin"), "-o", str(self.tmp / "x.pptx")], om)
        self.assertEqual(rc, 2)
        self.assertIn("No se encontraron los archivos de Kokoro", texto)
        self.assertEqual(om.llamadas, [])

    def test_sin_python_pptx_dice_el_comando(self):
        sys.modules["pptx"] = None
        rc, texto = self._ejecutar(["Tema"], modulo_ollama())
        self.assertEqual(rc, 1)
        self.assertIn("pip install python-pptx", texto)

    def test_ollama_apagado_y_modelo_inexistente(self):
        rc, texto = self._ejecutar(["Tema"], modulo_ollama(apagado=True))
        self.assertEqual(rc, 1)
        self.assertIn("No se pudo conectar con Ollama", texto)
        rc, texto = self._ejecutar(["Tema", "--modelo", "nada:1b"], modulo_ollama())
        self.assertIn("ollama pull nada:1b", texto)


if __name__ == "__main__":
    unittest.main(verbosity=2)

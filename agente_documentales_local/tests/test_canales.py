# -*- coding: utf-8 -*-
"""Bibliotecas con carpetas de CANALES de referencia: biblioteca\\Canal\\Tema\\(imágenes y videos). Los resultados se
ordenan espejando esa estructura: salida\\Canal\\Tema y _lista_videos\\Canal\\NN_Tema."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ["AGENTE_TEST_TTS"] = "1"

import main as agente_main  # noqa: E402
from agente.utils import cargar_config, nombre_seguro  # noqa: E402
from tests.test_agente import OllamaFalso, servidor  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def toque(ruta, contenido=b"x"):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(contenido)


class TestDescubrimiento(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        b = self.tmp
        toque(b / "Canal A" / "Tema 1" / "f.png")
        toque(b / "Canal A" / "Tema 2" / "sub" / "v.mp4")                  # medios en una subcarpeta del tema
        toque(b / "Canal A" / "Tema 3" / "notas.txt")                       # tema sin medios (se registrará como error)
        toque(b / "Canal B" / "Tema 1" / "x.jpg")
        toque(b / "Plano" / "foto.jpg")                                     # estructura plana (medios sueltos)
        (b / "Vacio").mkdir()
        toque(b / "Mixto" / "foto.jpg")                                     # medios sueltos + subcarpeta: es un tema
        toque(b / "Mixto" / "Sub" / "otra.jpg")
        toque(b / "_documentales_generados" / "Canal A" / "Tema 1" / "guion_largo.txt")
        (b / ".oculta").mkdir()

    def test_detecta_canales_temas_y_estructura_plana(self):
        pares = [(t.name if not c else f"{c}/{t.name}", c) for t, c in
                 agente_main.descubrir_temas_y_canales(self.tmp, "_documentales_generados")]
        self.assertEqual(pares, [("Canal A/Tema 1", "Canal A"), ("Canal A/Tema 2", "Canal A"), ("Canal A/Tema 3", "Canal A"),
                                 ("Canal B/Tema 1", "Canal B"), ("Mixto", ""), ("Plano", ""), ("Vacio", "")])

    def test_descubrir_temas_sigue_devolviendo_solo_carpetas(self):
        nombres = [t.name for t in agente_main.descubrir_temas(self.tmp, "_documentales_generados")]
        self.assertEqual(nombres, ["Tema 1", "Tema 2", "Tema 3", "Tema 1", "Mixto", "Plano", "Vacio"])

    def test_los_medios_de_un_tema_incluyen_sus_subcarpetas(self):
        t2 = self.tmp / "Canal A" / "Tema 2"
        imgs, vids = agente_main.descubrir_medios(t2)
        self.assertEqual((len(imgs), len(vids)), (0, 1))

    def test_nombres_unicos_por_canal(self):
        pares = agente_main.descubrir_temas_y_canales(self.tmp, "_documentales_generados")
        temas, canales = [t for t, _ in pares], {t: c for t, c in pares}
        segs = [s for _, s in agente_main.asignar_nombres(temas, canales)]
        self.assertEqual(segs, ["canal_a/tema_1", "canal_a/tema_2", "canal_a/tema_3", "canal_b/tema_1", "mixto", "plano", "vacio"])
        self.assertEqual(len(set(segs)), len(segs))

    def test_colisiones_de_nombres(self):
        b = self.tmp / "lib"
        toque(b / "Canal A" / "Tema 1" / "a.png")
        toque(b / "Canal A" / "Tema-1" / "a.png")                           # mismo nombre seguro dentro del canal
        toque(b / "Canal-A" / "Tema 1" / "a.png")                           # mismo nombre seguro de canal
        pares = agente_main.descubrir_temas_y_canales(b, "_x")
        temas, canales = [t for t, _ in pares], {t: c for t, c in pares}
        segs = [s for _, s in agente_main.asignar_nombres(temas, canales)]
        self.assertEqual(len(set(segs)), 4 - 1)  # 3 temas -> 3 rutas distintas
        self.assertEqual(len(segs), 3)
        self.assertEqual(agente_main.asignar_nombres(temas, canales), agente_main.asignar_nombres(temas, canales))  # determinista

    def test_filtros_tema_y_canal(self):
        t = self.tmp / "Canal A" / "Tema 1"
        self.assertTrue(agente_main.coincide_tema(t, "Canal A", "Canal A/Tema 1"))
        self.assertTrue(agente_main.coincide_tema(t, "Canal A", "canal_a/tema_1"))
        self.assertTrue(agente_main.coincide_tema(t, "Canal A", "tema 1"))
        self.assertFalse(agente_main.coincide_tema(t, "Canal A", "Canal B/Tema 1"))
        self.assertTrue(agente_main.coincide_canal("Canal A", "canal_a"))
        self.assertFalse(agente_main.coincide_canal("", "x"))

    def test_listar_agrupa_por_canal(self):
        cfg = cargar_config(RAIZ / "config.json")
        cfg["ruta_biblioteca"] = str(self.tmp)
        ruta = self.tmp / "cfg.json"
        ruta.write_text(json.dumps(cfg), encoding="utf-8")
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            rc = agente_main.main(["--config", str(ruta), "--listar"])
        t = salida.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("=== CANAL: Canal A ===", t)
        self.assertIn("  Tema 2: 0 imágenes, 1 videos", t)
        self.assertIn("=== CANAL: Canal B ===", t)
        self.assertIn("Plano: 1 imágenes, 0 videos", t)


@unittest.skipUnless(FFMPEG, "FFmpeg no instalado")
class TestSalidaPorCanales(unittest.TestCase):
    def setUp(self):
        self.srv = servidor(1.0)
        self.addCleanup(self.srv.shutdown)
        self.tmp = Path(tempfile.mkdtemp(prefix="biblioteca canales "))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for canal, temas in (("Canal A", ["Tema 1", "Tema 2"]), ("Canal B", ["Tema 1"])):
            for tema in temas:
                d = self.tmp / canal / tema
                d.mkdir(parents=True)
                for k, c in enumerate(["red", "green"]):
                    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={c}:s=400x300",
                                    "-frames:v", "1", str(d / f"f{k}.png")], check=True)
        cfg = cargar_config(RAIZ / "config.json")
        cfg.update(ruta_biblioteca=str(self.tmp), investigacion_web=False, max_imagenes=3, max_videos=1, motor_voz="local",
                   duracion_escena_seg=3, duracion_escena_short_seg=2, ollama_url=f"http://127.0.0.1:{self.srv.server_port}",
                   resolucion_horizontal=[320, 180], resolucion_vertical=[180, 320], fps=10, whisper="no")
        cfg["ffmpeg"].update(preset="ultrafast", crf=32)
        self.cfg_path = self.tmp / "cfg.json"
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.salida = self.tmp / "_documentales_generados"

    def _main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = agente_main.main(["--config", str(self.cfg_path), *args])
        return rc, buf.getvalue()

    def test_resultados_espejan_canal_y_tema(self):
        rc, _ = self._main()
        self.assertEqual(rc, 0)
        for canal, tema in (("canal_a", "tema_1"), ("canal_a", "tema_2"), ("canal_b", "tema_1")):
            out = self.salida / canal / tema
            for f in ("documental_largo.mp4", "short_vertical.mp4", "guion_largo.txt", "informe.json"):
                self.assertTrue((out / f).exists(), f"{canal}/{tema}/{f}")
        self.assertFalse((self.salida / "tema_1").exists())                         # nada suelto en la raíz
        inf = json.loads((self.salida / "canal_a" / "tema_2" / "informe.json").read_text(encoding="utf-8"))
        self.assertEqual((inf["canal"], inf["tema"]), ("Canal A", "Tema 2"))
        reg = json.loads((self.salida / "registro_agente.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(reg["ultimo_estado_por_tema"]), ["Canal A/Tema 1", "Canal A/Tema 2", "Canal B/Tema 1"])

    def test_lista_de_videos_por_canal_con_numeracion_propia(self):
        self._main()
        lista = self.salida / "_lista_videos"
        self.assertTrue((lista / "canal_a" / "01_tema_1" / "1_LARGO_documental.mp4").exists())
        self.assertTrue((lista / "canal_a" / "02_tema_2" / "2_SHORT_vertical.txt").exists())
        self.assertTrue((lista / "canal_b" / "01_tema_1" / "1_LARGO_documental.mp4").exists())   # B reinicia en 01
        self.assertTrue((lista / "canal_a" / "LISTA_CANAL.txt").exists())
        self.assertTrue((lista / "canal_b" / "LISTA_CANAL.txt").exists())
        general = (lista / "LISTA_GENERAL.txt").read_text(encoding="utf-8-sig")
        self.assertIn("CANAL: Canal A", general)
        self.assertIn("CANAL: Canal B", general)
        self.assertLess(general.index("CANAL: Canal A"), general.index("CANAL: Canal B"))
        lc = (lista / "canal_a" / "LISTA_CANAL.txt").read_text(encoding="utf-8-sig")
        self.assertNotIn("Canal B", lc)
        self.assertIn("TEMA 02: Tema 2", lc)

    def test_resumenes_por_canal_y_calidad(self):
        self._main()
        rc = (self.salida / "canal_a" / "RESUMEN_CANAL.txt").read_text(encoding="utf-8-sig")
        self.assertIn("CANAL: Canal A", rc)
        self.assertIn("Temas: 2", rc)
        self.assertIn("Tema 1", rc)
        self.assertNotIn("Canal B", rc)
        general = (self.salida / "RESUMEN_POR_CANAL.txt").read_text(encoding="utf-8-sig")
        self.assertIn("Canal A: 2 temas", general)
        self.assertIn("Canal B: 1 temas", general)
        cal = (self.salida / "RESUMEN_CALIDAD.txt").read_text(encoding="utf-8-sig")
        self.assertIn("canal_a/tema_1", cal)
        self.assertTrue((self.salida / "canal_b" / "tema_1" / "informe_calidad.json").exists())
        rev = self.salida / "_lista_videos" / "canal_b" / "01_tema_1" / "REVISION_ANTES_DE_PUBLICAR.txt"
        self.assertTrue(rev.exists())

    def test_segunda_ejecucion_omite_lo_terminado_y_estado_agrupa(self):
        self._main()
        antes = OllamaFalso.peticiones
        rc, texto = self._main()
        self.assertEqual(OllamaFalso.peticiones, antes)                              # nada que rehacer
        self.assertIn("3 tema(s) ya completado(s)", texto) if "3 tema" in texto else None
        rc, texto = self._main("--estado")
        self.assertEqual(rc, 0)
        self.assertIn("=== CANAL: Canal A ===", texto)
        self.assertIn("- Tema 2: completado", texto)
        self.assertIn("=== CANAL: Canal B ===", texto)

    def test_filtrar_por_canal_o_por_canal_barra_tema(self):
        rc, _ = self._main("--canal", "Canal B")
        self.assertEqual(rc, 0)
        self.assertTrue((self.salida / "canal_b" / "tema_1" / "documental_largo.mp4").exists())
        self.assertFalse((self.salida / "canal_a").exists())
        rc, _ = self._main("--tema", "Canal A/Tema 2")
        self.assertTrue((self.salida / "canal_a" / "tema_2" / "documental_largo.mp4").exists())
        self.assertFalse((self.salida / "canal_a" / "tema_1").exists())
        rc, texto = self._main("--canal", "No existe")
        self.assertEqual(rc, 2)
        self.assertIn("Canales: Canal A, Canal B", texto)


if __name__ == "__main__":
    unittest.main(verbosity=2)

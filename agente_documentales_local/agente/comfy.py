# -*- coding: utf-8 -*-
"""Cliente mínimo de ComfyUI (local). Usa un workflow exportado con "Save (API Format)" y sustituye el texto del
nodo de prompt (y la semilla). Todo ocurre en 127.0.0.1: no descarga imágenes de Internet.

AVISO DE RENDIMIENTO: sin GPU NVIDIA, Stable Diffusion en CPU tarda varios minutos POR IMAGEN. Por eso está
desactivado por defecto y limitado a pocas imágenes por tema."""
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ComfyError(Exception):
    pass


class ComfyCliente:
    def __init__(self, cfg_comfy, base_dir="."):
        self.url = cfg_comfy.get("url", "http://127.0.0.1:8188").rstrip("/")
        ruta = Path(cfg_comfy.get("workflow", "assets/comfy/workflow_api.json"))
        if not ruta.is_absolute():
            ruta = Path(base_dir) / ruta
        self.workflow_ruta = ruta
        self.nodo_texto = str(cfg_comfy.get("nodo_texto", ""))
        self.campo_texto = cfg_comfy.get("campo_texto", "text")
        self.timeout = int(cfg_comfy.get("timeout_imagen_seg", 1200))
        self.sufijo = cfg_comfy.get("sufijo_prompt", ", cinematic, highly detailed, no text, no watermark")

    def _http(self, ruta, payload=None, raw=False, timeout=30):
        datos = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.url + ruta, data=datos, headers={"Content-Type": "application/json"},
                                     method="POST" if datos is not None else "GET")
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                cuerpo = r.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ComfyError(f"ComfyUI no responde en {self.url}: {e}")
        return cuerpo if raw else json.loads(cuerpo.decode("utf-8"))

    def disponible(self):
        try:
            self._http("/system_stats", timeout=5)
            return self.workflow_ruta.exists() and bool(self.nodo_texto)
        except ComfyError:
            return False

    def generar(self, prompt, destino, semilla=None):
        try:
            wf = json.loads(self.workflow_ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ComfyError(f"Workflow ilegible ({self.workflow_ruta}): {e}")
        nodo = wf.get(self.nodo_texto)
        if not nodo or self.campo_texto not in nodo.get("inputs", {}):
            raise ComfyError(f"El nodo {self.nodo_texto} no tiene el campo '{self.campo_texto}' en el workflow.")
        nodo["inputs"][self.campo_texto] = prompt + self.sufijo
        semilla = random.randint(0, 2 ** 31) if semilla is None else semilla
        for n in wf.values():
            ins = n.get("inputs", {})
            for k in ("seed", "noise_seed"):
                if k in ins and not isinstance(ins[k], list):
                    ins[k] = semilla
        r = self._http("/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})
        pid = r.get("prompt_id")
        if not pid:
            raise ComfyError(f"ComfyUI rechazó el workflow: {str(r)[:200]}")
        t0 = time.time()
        while time.time() - t0 < self.timeout:
            h = self._http(f"/history/{pid}")
            if pid in h:
                for salida in h[pid].get("outputs", {}).values():
                    for im in salida.get("images", []):
                        q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                                    "type": im.get("type", "output")})
                        datos = self._http(f"/view?{q}", raw=True, timeout=60)
                        Path(destino).write_bytes(datos)
                        return Path(destino)
                raise ComfyError("ComfyUI terminó sin devolver ninguna imagen.")
            time.sleep(2)
        raise ComfyError(f"Tiempo agotado ({self.timeout}s) esperando la imagen.")


def generar_imagenes(cfg_comfy, prompts, carpeta, log, base_dir="."):
    """Genera hasta max_por_tema imágenes. Devuelve rutas; los fallos se registran y no detienen el proceso."""
    cli = ComfyCliente(cfg_comfy, base_dir)
    if not cli.disponible():
        log.warning("ComfyUI no está disponible (¿abierto en la URL configurada, con workflow_api.json y nodo_texto?): "
                    "se usan solo las imágenes locales.")
        return []
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    maximo = int(cfg_comfy.get("max_por_tema", 6))
    res = []
    for i, p in enumerate(prompts[:maximo], 1):
        destino = carpeta / f"gen_{i:02d}.png"
        if destino.exists() and destino.stat().st_size > 1000:
            res.append(destino)
            continue
        try:
            log.info(f"ComfyUI: generando imagen {i}/{min(maximo, len(prompts))} (en CPU puede tardar minutos)...")
            res.append(cli.generar(p, destino, semilla=1000 + i))
        except ComfyError as e:
            log.warning(f"ComfyUI: {e}")
            break
    return res

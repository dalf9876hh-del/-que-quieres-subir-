# -*- coding: utf-8 -*-
"""Cliente mínimo de Ollama (solo biblioteca estándar). Nunca descarga modelos."""
import json
import logging
import re
import urllib.error
import urllib.request

from .texto import es_razonamiento

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost: sin proxy


class OllamaError(Exception):
    pass


class ModeloRazonaError(OllamaError):
    """El modelo devolvió su razonamiento interno en lugar del texto pedido."""


def _peticion(cfg, ruta, payload=None, timeout=None):
    url = cfg["ollama_url"].rstrip("/") + ruta
    datos = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=datos, headers={"Content-Type": "application/json"},
                                 method="POST" if datos is not None else "GET")
    try:
        with _OPENER.open(req, timeout=timeout or cfg["ollama_timeout_seg"]) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")[:300]
        raise OllamaError(f"HTTP {e.code} de Ollama: {cuerpo}")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise OllamaError(f"No se pudo conectar con Ollama en {url}: {e}")
    except ValueError as e:
        raise OllamaError(f"Respuesta JSON inválida de Ollama: {e}")


def modelos_instalados(cfg):
    r = _peticion(cfg, "/api/tags", timeout=15)
    return [m.get("name", "") for m in r.get("models", [])]


def modelo_disponible(cfg):
    """Devuelve (ok, lista_de_modelos). Lanza OllamaError si Ollama no responde."""
    lista = modelos_instalados(cfg)
    m = cfg["modelo_ollama"]
    ok = m in lista or (":" not in m and f"{m}:latest" in lista)
    return ok, lista


def _quitar_razonamiento(texto):
    """Elimina el razonamiento de qwen3: bloques <think>..</think>, o texto previo a un </think> huérfano."""
    if "</think>" in texto:
        texto = texto.rsplit("</think>", 1)[1]
    return re.sub(r"<think>.*?</think>", "", texto, flags=re.S).strip()


def generar(cfg, sistema, prompt, max_tokens=800, temperatura=0.7):
    """Genera texto. Si el modelo solo devuelve razonamiento, cambia al modelo de respaldo (si está instalado)."""
    try:
        return _generar(cfg, sistema, prompt, max_tokens, temperatura)
    except ModeloRazonaError:
        resp = str(cfg.get("modelo_respaldo") or "").strip()
        if resp and resp != cfg["modelo_ollama"] and resp in cfg.get("_modelos_instalados", []):
            logging.getLogger("agente").warning(
                f"El modelo '{cfg['modelo_ollama']}' devuelve razonamiento en vez de texto: se cambia "
                f"automáticamente a '{resp}' para el resto de la ejecución.")
            cfg["modelo_ollama"] = resp
            return _generar(cfg, sistema, prompt, max_tokens, temperatura)
        raise


def _generar(cfg, sistema, prompt, max_tokens, temperatura):
    modelo = cfg["modelo_ollama"]
    opciones = {"temperature": temperatura, "num_ctx": int(cfg["ollama_num_ctx"]),
                "num_predict": int(max_tokens), "repeat_penalty": 1.1}
    if "qwen3" in modelo.lower() and cfg.get("qwen3_sin_razonamiento", True):
        # Plantilla ChatML "a mano" con el bloque <think> ya vacío: obliga a qwen3 a responder sin razonar,
        # incluso si la versión de Ollama ignora 'think:false' o /no_think.
        cruda = (f"<|im_start|>system\n{sistema}<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n"
                 f"<|im_start|>assistant\n<think>\n\n</think>\n\n")
        opciones["stop"] = ["<|im_end|>"]
        r = _peticion(cfg, "/api/generate", {"model": modelo, "prompt": cruda, "raw": True, "stream": False,
                                             "keep_alive": "15m", "options": opciones})
        texto = r.get("response", "")
    else:
        if "qwen3" in modelo.lower() and not prompt.rstrip().endswith("/no_think"):
            prompt = prompt.rstrip() + " /no_think"
        r = _peticion(cfg, "/api/chat", {
            "model": modelo, "stream": False, "think": False, "keep_alive": "15m", "options": opciones,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": prompt}]})
        texto = (r.get("message") or {}).get("content", "")
    if "<think>" in texto and "</think>" not in texto:
        raise ModeloRazonaError("La respuesta quedó dentro de un bloque de razonamiento sin cerrar.")
    texto = _quitar_razonamiento(texto)
    if not texto:
        raise OllamaError("Ollama devolvió una respuesta vacía.")
    if es_razonamiento(texto):
        raise ModeloRazonaError("El modelo devolvió su razonamiento interno en inglés en lugar del guion.")
    return texto

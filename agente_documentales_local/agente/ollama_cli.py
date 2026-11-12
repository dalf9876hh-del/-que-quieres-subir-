# -*- coding: utf-8 -*-
"""Ayudas para los scripts de consola (chat_voz.py y ollama_pptx.py) que usan el paquete `ollama` de Python
(ollama.chat). Convierte los errores típicos en mensajes entendibles en español, sin traceback."""


class OllamaCliError(Exception):
    """Mensaje ya redactado en español."""


def importar_ollama():
    try:
        import ollama
        return ollama
    except ImportError:
        raise OllamaCliError("Falta el paquete de Python 'ollama'. Instálelo en el entorno virtual: pip install ollama")


def _es_conexion(e):
    nombre = type(e).__name__.lower()
    return isinstance(e, (ConnectionError, OSError)) or "connect" in nombre or "connect" in str(e).lower() \
        or "refused" in str(e).lower() or "10061" in str(e)


def nombres_modelos(lista):
    """Nombres de modelos instalados, con versiones nuevas (objetos) y antiguas (diccionarios) del paquete."""
    modelos = lista["models"] if isinstance(lista, dict) else getattr(lista, "models", [])
    res = []
    for m in modelos:
        n = m.get("model") or m.get("name") if isinstance(m, dict) else (getattr(m, "model", None) or getattr(m, "name", None))
        if n:
            res.append(n)
    return res


def mensaje_ollama_apagado():
    return ("No se pudo conectar con Ollama. Abra la aplicación Ollama (icono en la bandeja de Windows) o ejecute "
            "'ollama serve' en otra ventana de PowerShell, y vuelva a intentarlo.")


def mensaje_modelo_inexistente(modelo, instalados=()):
    extra = f" Modelos instalados: {', '.join(instalados)}." if instalados else ""
    return (f"El modelo '{modelo}' no está instalado en Ollama.{extra} Descárguelo con: ollama pull {modelo}  "
            "o elija otro con --modelo.")


def verificar(modelo):
    """Comprueba que Ollama responde y que el modelo existe. Lanza OllamaCliError con un mensaje claro."""
    ollama = importar_ollama()
    try:
        instalados = nombres_modelos(ollama.list())
    except Exception as e:  # noqa: BLE001
        if _es_conexion(e):
            raise OllamaCliError(mensaje_ollama_apagado())
        raise OllamaCliError(f"Ollama devolvió un error al consultar los modelos: {e}")
    if modelo not in instalados and f"{modelo}:latest" not in instalados:
        raise OllamaCliError(mensaje_modelo_inexistente(modelo, instalados))
    return instalados


def chat(modelo, mensajes, **kw):
    """ollama.chat con errores traducidos. Devuelve el texto de la respuesta."""
    ollama = importar_ollama()
    try:
        r = ollama.chat(model=modelo, messages=mensajes, **kw)
    except Exception as e:  # noqa: BLE001
        if getattr(e, "status_code", None) == 404 or "not found" in str(e).lower():
            raise OllamaCliError(mensaje_modelo_inexistente(modelo))
        if _es_conexion(e):
            raise OllamaCliError(mensaje_ollama_apagado())
        raise OllamaCliError(f"Ollama no pudo responder: {e}")
    try:
        return r["message"]["content"]
    except (KeyError, TypeError):
        return r.message.content

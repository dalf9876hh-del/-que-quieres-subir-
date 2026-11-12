AGENTE DOCUMENTAL LOCAL  (Windows 11, solo CPU, Ollama)
=========================================================

Convierte cada subcarpeta de su biblioteca multimedia en:
  - documental_largo.mp4  (16:9, 1280x720, guion de mas de 1.000 palabras)
  - short_vertical.mp4    (9:16, 720x1280, guion de 90-130 palabras)
con guion en espanol (Ollama), narracion local (pyttsx3), subtitulos SRT y montaje con FFmpeg.
Solo usa las imagenes y videos que usted tenga en cada carpeta. NUNCA descarga imagenes ni videos.
Internet se usa unicamente para investigar en TEXTO (OpenAlex, Crossref, PubMed, Wikipedia).


1. EXTRAER EL ZIP
-----------------
Clic derecho en agente_documentales_local.zip > "Extraer todo...". Elija una carpeta cuya ruta
sea corta (por ejemplo C:\Herramientas\agente_documentales_local). No ejecute nada desde dentro del ZIP.


2. CONFIGURAR LA RUTA DE LA BIBLIOTECA
--------------------------------------
Abra config.json con el Bloc de notas y escriba su carpeta en "ruta_biblioteca". En JSON las
barras invertidas se escriben dobles o se usan barras normales:
    "ruta_biblioteca": "D:\\Mi Biblioteca"
    "ruta_biblioteca": "D:/Mi Biblioteca"
Tambien puede pasarla al ejecutar:  ejecutar.bat --biblioteca "D:\Mi Biblioteca"

Estructura esperada (cada subcarpeta directa es un tema, descubierto automaticamente):
    Biblioteca\
        Inteligencia artificial\   (imagenes y/o videos)
        Exploracion espacial\
        Historia\ ...
Los resultados se guardan en Biblioteca\_documentales_generados\ (esa carpeta se ignora al recorrer).
Tambien se ignoran carpetas que empiecen por "." o "_". Las rutas con espacios y acentos son validas.

Parametros de config.json (todos editables sin tocar el codigo):
  modelo_ollama ............ modelo a usar (por defecto qwen3:4b; puede cambiarlo, p. ej. llama3.2:3b)
  qwen3_sin_razonamiento ... true: fuerza a qwen3 a responder sin razonar (si usa otro modelo, no afecta)
  voz_timeout_seg .......... segundos maximos por trozo de voz antes de abortar pyttsx3 y usar PowerShell (120)
  ollama_url ............... direccion de Ollama (http://localhost:11434)
  palabras_objetivo ........ objetivo del documental (1450). palabras_minimas = 1000 (se exige SUPERARLO)
  max_imagenes / max_videos  limite por tema (0 = sin limite)
  duracion_escena_seg ...... segundos por imagen en el documental; duracion_escena_short_seg en el Short
  resolucion_horizontal / resolucion_vertical, fps
  velocidad_voz ............ palabras por minuto aproximadas de pyttsx3 (165)
  voz_preferida ............ texto que debe contener el nombre de la voz (opcional)
  investigacion_web ........ true/false
  fondo_desenfocado ........ true: las imagenes que no encajan se rodean de fondo difuminado
  quemar_subtitulos ........ true: incrusta los subtitulos en el MP4 (mas lento); false: solo SRT
  ffmpeg ................... preset (veryfast), crf (26), audio_bitrate, threads (2)
  ruta_ffmpeg / ruta_ffprobe  solo si FFmpeg no esta en el PATH


3. COMPROBAR OLLAMA Y EL MODELO
-------------------------------
Ollama debe estar abierto (icono en la bandeja) o ejecutando "ollama serve". En una consola:
    ollama list
Debe aparecer el modelo de config.json (qwen3:4b). El agente NUNCA descarga modelos: si falta,
se lo indica. Comprobacion completa (Ollama, modelo, FFmpeg, voz):
    ejecutar.bat --comprobar


4. INSTALAR DEPENDENCIAS
------------------------
Necesita Python 3.9 o superior (https://www.python.org/downloads/, marcando "Add python.exe to PATH").
Haga doble clic en instalar.bat: crea el entorno .venv e instala pyttsx3 (y pywin32/comtypes).


5. INSTALAR Y VERIFICAR FFMPEG
------------------------------
  Opcion A (recomendada):  winget install Gyan.FFmpeg      (reinicie la consola despues)
  Opcion B: descargue un build de https://www.gyan.dev/ffmpeg/builds/ , descomprimalo y anada su
            carpeta "bin" al PATH, o escriba su ruta en "ruta_ffmpeg" de config.json.
Verifique en una consola nueva:   ffmpeg -version     y     ffprobe -version
Sin FFmpeg el agente genera guiones, narracion y subtitulos, pero NO los MP4 (lo indica como error).


6. EJECUTAR EL AGENTE
---------------------
    ejecutar.bat                     procesa todos los temas, uno tras otro
    ejecutar.bat --listar            muestra los temas y cuantos medios tiene cada uno
    ejecutar.bat --comprobar         revisa el entorno
    ejecutar.bat --solo-guion        genera solo guiones (util para probar el modelo)
El proceso es lento en CPU (decenas de minutos por tema). Se procesa todo secuencialmente.
Puede cerrar la ventana en cualquier momento; ver apartado 9.


7. PROCESAR UN SOLO TEMA
------------------------
    ejecutar.bat --tema "Exploracion espacial"
Use el nombre exacto de la carpeta (sin distinguir mayusculas) o su nombre seguro (exploracion_espacial).


8. DONDE ESTAN LOS RESULTADOS
-----------------------------
Biblioteca\_documentales_generados\
    registro_agente.json    estado de cada tema en cada ejecucion
    agente.log              registro detallado
    <tema_seguro>\
        investigacion.json  fuentes de texto recuperadas (titulo, anio, enlace, resumen)
        partes_guion\       parte_01..07.txt y ampliacion_NN.txt (guardado seccion a seccion)
        guion_largo_en_progreso.txt, guion_largo.txt, guion_short.txt
        audio_generado.wav, audio_generado_short.wav
        subtitulos_largos.srt, subtitulos_short.srt
        documental_largo.mp4, short_vertical.mp4
        informe.json        estado, palabras, advertencias, errores, medios usados
Estados: completado | completado_con_advertencias | incompleto | error.
Revise siempre las "advertencias" de informe.json.


9. REANUDAR UN TRABAJO INTERRUMPIDO
-----------------------------------
Vuelva a ejecutar el mismo comando. Se reutilizan: secciones del guion ya guardadas, ampliaciones,
trozos de narracion, escenas de video ya codificadas y MP4 ya validos. No se sobrescriben secciones
completas. Si apago el equipo a mitad, lo ya guardado se conserva (los JSON se escriben de forma
atomica). Para rehacer algo concreto, borre ese archivo (por ejemplo parte_03.txt o el MP4) y ejecute.


Si el log muestra textos que empiezan por "Okay, I need to..." el modelo esta devolviendo su razonamiento
interno. El agente lo detecta y se detiene en pocos intentos; cambie "modelo_ollama" (p. ej. llama3.2:3b).

10. LIMITACIONES
----------------
- Un modelo de 4B parametros en CPU puede cometer errores factuales. El prompt le prohibe inventar
  cifras, citas y enlaces, pero NO hay verificacion automatica de hechos. Revise cada guion antes de
  publicarlo. La investigacion aporta metadatos y resumenes; no equivale a evidencia para cada frase.
- Sin Internet, el guion usa conocimiento general del modelo (queda registrado como advertencia).
- Si faltan medios, se reutilizan en varias escenas (se registra en informe.json). No hay efectos
  ni transiciones: el montaje es secuencial.
- Subtitulos: sincronizacion APROXIMADA. Se anclan a la duracion real de cada trozo de audio (unas 350
  letras) y se reparten por caracteres dentro del trozo. No se verifica fonema a fonema.
- Voz: se busca una voz espanola instalada en Windows. Si no hay, usa la predeterminada y avisa.
  Instale una en Configuracion > Hora e idioma > Voz. Si pyttsx3 falla, se usa PowerShell/System.Speech
  (incluido en Windows, sin descargas).
- Solo CPU: un documental largo puede tardar bastante (guion + sintesis + codificacion). La estabilidad
  tiene prioridad sobre la velocidad.
- Las pruebas automaticas (pruebas.bat) usan un Ollama simulado y voz de silencio; NO sustituyen una
  primera ejecucion real en su equipo con un tema pequeno (pocas imagenes) para validar todo.

11. LISTA DE VIDEOS PARA SUBIR A MANO (YouTube)
----------------------------------------------
Al terminar cada tema, el agente crea (o actualiza) la carpeta:
    Biblioteca\_documentales_generados\_lista_videos\
        LISTA_GENERAL.txt            indice ordenado con casillas [ ] para marcar lo que ya subio
        01_<tema>\
            1_LARGO_documental.mp4          el video (enlace duro: no duplica espacio en disco)
            1_LARGO_documental.txt          FICHA: titulo, descripcion y hashtags para copiar y pegar
            1_LARGO_documental_subtitulos.srt
            2_SHORT_vertical.mp4
            2_SHORT_vertical.txt
            2_SHORT_vertical_subtitulos.srt (si existe)
        02_<otro tema>\ ...
Los temas se numeran por orden de creacion y el numero no cambia. Las fichas .txt estan listas para
abrir en el Bloc de notas: copie el TITULO, la DESCRIPCION y suba el video en YouTube Studio.
Las fichas que usted edite NO se sobrescriben al volver a ejecutar.

Para crear la lista con videos que YA generó (sin volver a procesar nada):
    ejecutar.bat --preparar-lista
    ejecutar.bat --preparar-lista --tema "Corriente del niño1"
Con --forzar-textos se rehacen tambien las fichas existentes (pisa sus ediciones).
Si Ollama no responde, las fichas se crean con un titulo y una descripcion de respaldo (con aviso).
Parametros en config.json (opcionales): carpeta_lista_videos, lista_enlazar_videos (false = copiar en vez de
enlazar), descripcion_aviso (frase que se anade al final de cada descripcion; editela o dejela vacia).
Revise siempre titulo y descripcion antes de publicar: los redacta una IA pequena y pueden fallar.


12. VARIOS TEMAS A LA VEZ (LOTES) Y NOMBRES DE CARPETA
------------------------------------------------------
- El TEMA del documental sale del nombre de la carpeta. Una carpeta llamada "2510" no dice nada al
  modelo y el guion saldra generico o inventado. Ponga nombres descriptivos ("La Gran Muralla China")
  o cree dentro de cada carpeta un archivo tema.txt con el titulo en la primera linea.
- Cada tema es independiente: el documental largo y el Short se crean por separado. Si falla el Short,
  igualmente se crea el largo (y viceversa). El estado y el motivo de cada tema quedan en informe.json
  y en el RESUMEN FINAL del registro.
- Un lote de 10 temas puede tardar muchas horas en CPU. En Windows el agente impide que el equipo se
  suspenda mientras trabaja y desactiva la "seleccion rapida" de la consola (hacer clic en la ventana
  negra pausaba el proceso). Aun asi, conecte el portatil a la corriente. Si se interrumpe, vuelva a
  ejecutar: se reanuda y se omite lo ya terminado.
- Modelo: por defecto llama3.2:3b. Si usa qwen3:4b y este devuelve su razonamiento en vez del guion, el
  agente cambia solo a "modelo_respaldo" (si esta instalado) y lo anota en informe.json.
- Si algun tema queda incompleto, abra _documentales_generados\registro_agente.json (campo "errores")
  o el final de agente.log: ahi esta el motivo exacto.

13. LOTES GRANDES (por ejemplo 40 CARPETAS)
-------------------------------------------
- Se procesan una tras otra. Los temas ya COMPLETADOS se omiten al volver a ejecutar (sin llamar a Ollama);
  use --rehacer para revisarlos de nuevo. Los incompletos se reanudan donde se quedaron.
- Para trabajar por tandas (por ejemplo por la noche):
      ejecutar.bat --max-temas 5
  procesa como maximo 5 temas pendientes y termina; vuelva a ejecutarlo para los 5 siguientes.
- Tiempo orientativo en un i3 con llama3.2:3b: el guion largo tarda unos 10 minutos por tema (medido en
  una ejecucion real); la voz, el Short y el montaje suman, estimado, otros 10-20 minutos. En total,
  del orden de 20-30 minutos por tema, es decir, 13-20 horas para 40 temas. Son estimaciones.
- Espacio en disco: cada tema ocupa unos cientos de MB (video largo + Short + audio WAV). Reserve varios GB.
- Cada tema tiene su carpeta, su informe.json y su estado; el RESUMEN FINAL muestra el motivo de los fallos.

14. VER EL ESTADO DE TODAS LAS CARPETAS
---------------------------------------
    ejecutar.bat --estado
Muestra, sin procesar nada, cada carpeta de la biblioteca: completado / incompleto / error / sin procesar,
las palabras de sus guiones, si ya tiene el video largo y el Short, y el MOTIVO si fallo.
Es lo primero que conviene mirar si algun tema no se genero.


15. PRODUCCIÓN POR ESCENAS (versión 2) Y MONETIZACIÓN
-----------------------------------------------------
IMPORTANTE (sinceridad): ningún programa puede garantizar que un video "pase el filtro de monetización". La decide
YouTube. Desde julio de 2025 su política de contenido "inauténtico" (antes "contenido repetitivo") excluye el
contenido producido en masa o repetitivo y el contenido reutilizado; usar IA no está prohibido, pero el video debe
aportar valor original. Lo que SÍ hace este programa es reducir las señales de riesgo y avisarle antes de subir:
  * Estructura narrativa distinta según el tema (5 esqueletos y 5 tipos de gancho): evita 40 videos idénticos.
  * aporte.txt: ponga en la carpeta del tema SU opinión/experiencia/análisis (se narra tal cual antes de la
    conclusión). Es lo más importante para demostrar valor propio. tema.txt: título del tema (1 línea).
  * El guion solo puede afirmar hechos que estén en las fuentes recuperadas; las cifras/años que no figuran en
    ellas se listan para verificarlas a mano (informe_calidad.json).
  * Se excluyen logos, iconos de redes, botones de tienda y códigos QR (aparecían en sus videos).
  * Se avisa si las imágenes parecen de agencias o medios (Getty, CNN, BBC, AFP...): son material con copyright
    y es el mayor riesgo de sus lotes. Use imágenes propias, de dominio público o con licencia.
  * La descripción incluye aviso de voz sintética/IA y la ficha le recuerda responder la pregunta de "contenido
    sintético o alterado" de YouTube Studio.
  * RESUMEN_CALIDAD.txt (en la carpeta de salida) y REVISION_ANTES_DE_PUBLICAR.txt (en cada carpeta de la lista)
    clasifican cada video como OK / REVISAR / RIESGO y dicen por qué. Para evaluar lo ya generado:
        ejecutar.bat --calidad
  * Compruebe que cada video aporta algo que no se obtiene leyendo la noticia original; si solo reescribe una
    noticia con imágenes de la noticia, es justo lo que la política describe como contenido reutilizado.

MONTAJE (modo_video = "escenas"):
  * Cada escena dura lo que dura su frase hablada (unos 10 s en el largo, 6 s en el Short) y lleva zoom suave o
    paneo (más ancha/alta que el encuadre -> paneo). Las imágenes no se repiten seguidas.
  * transicion: "corte", "fundido" (por defecto, barato) o "xfade" (fundido cruzado real, más lento).
  * Audio: voz al 100 %, música al 12 % con "ducking" automático bajo la voz, efectos whoosh/pop en los cortes y
    normalización de volumen (-14 LUFS). Música y efectos: ver assets\musica y assets\sfx (LEEME.txt).
  * Subtítulos ASS de alta legibilidad: Short con 2-3 palabras y la actual resaltada; largo en bloques.
    Incrustados en el Short ("quemar_subtitulos_short": true). En el largo van como .srt para subirlos a YouTube
    (poner "quemar_subtitulos_largo": true los incrusta, a costa de varios minutos de render por video).
  * Para volver al montaje anterior: "modo_video": "clasico".

VOZ: motor_voz = "auto" usa edge-tts (voz neuronal es-MX-JorgeNeural; cambie "edge_voz") y, si no hay Internet o
falla, la voz local de Windows. edge-tts es un servicio en línea NO oficial de Microsoft: puede dejar de funcionar y
conviene revisar sus condiciones de uso antes de monetizar. Instalación: instalar.bat (edge-tts va en requirements.txt).

SUBTÍTULOS Y WHISPER: cada escena tiene su audio real, así que el tiempo de cada frase es exacto y el reparto entre
palabras es una estimación. Opcional: pip install faster-whisper (requirements-opcional.txt) y "whisper": "short"
(por defecto) o "todo" para afinar los tiempos por palabra; el guion mostrado sigue siendo el original.

PLAN CON JSON (VOICEOVER / VISUAL_PROMPT / SFX): "escenas_con_llm": true pide al modelo, por lotes, VISUAL_PROMPT y SFX
de cada escena. Su JSON se repara automáticamente (comillas, comas, texto cortado, markdown...) y, si falla, se usa
un plan por defecto. La narración NUNCA la reescribe el modelo. Es más lento: déjelo desactivado salvo que use ComfyUI.

COMFYUI: opcional y desactivado (assets\comfy\LEEME.txt). Sin GPU NVIDIA tarda minutos por imagen: no es práctico
para 40 videos. Solo genera en local, no descarga nada de Internet.

TIEMPOS: el montaje por escenas es más lento que el anterior (zoom, mezcla de audio, subtítulos incrustados en el
Short). Estimación orientativa en su equipo: 25-40 minutos por tema (no medido); use --max-temas para tandas.


16. COMPROBAR QUE SE USA LA VOZ NEURONAL, LOS SUBTÍTULOS Y LOS EFECTOS
----------------------------------------------------------------------
- Voz: ejecute  ejecutar.bat --probar-voz . Crea muestras_voz\muestra_edge.wav y muestra_local.wav para que las
  ESCUCHE y compare (la neuronal suena natural; la local es la voz básica de Windows) y dice cuál usará el agente.
  Si edge-tts "no está disponible": ejecute instalar.bat (instala el paquete edge-tts) y revise su Internet.
  Además, el registro, el informe.json y las propiedades del MP4 (campo "comentario") indican la voz usada:
  "voz: edge-tts es-MX-JorgeNeural" o "voz: pyttsx3 ...". Los trozos de audio ya no se reutilizan entre voces distintas.
- Subtítulos: ahora van incrustados en el Short Y en el documental largo (quemar_subtitulos_largo = true por defecto).
  Para quitarlos del largo: false (se sigue creando el .srt para subirlo a YouTube).
- Efectos: zoom más marcado (zoom_intensidad 0.30) hacia un punto distinto en cada escena, paneo, fundido cruzado real
  entre escenas (transicion = "xfade") y efectos de sonido whoosh/pop más fuertes en cada párrafo nuevo y en datos.
  El registro muestra por cada video: voz, subtítulos, música, nº de efectos, transición y volumen medio.
- Imágenes: se usan SOLO las de su PC. Con pocas imágenes el video se verá repetitivo: el programa avisa cuando hay
  menos de 20 para el documental largo; lo ideal son 30-60 imágenes originales por tema. Cada repetición sale con un
  encuadre y movimiento distintos, pero 8 imágenes para 128 escenas se notarán igualmente.
- La lista de videos se actualiza siempre con la versión nueva (antes podía quedar enlazada al video antiguo).


17. DURACIÓN DEL DOCUMENTAL LARGO (máximo 10 minutos)
-----------------------------------------------------
"duracion_objetivo_min": 10 en config.json. El agente calcula cuántas palabras caben (palabras_por_minuto = 150 al
principio), pide secciones más cortas si el modelo se pasa de largo y, antes de narrar, recorta frases completas del
final de las secciones centrales si el guion excede (se conservan la introducción, su aporte.txt y la conclusión; las
partes originales quedan guardadas). Después MIDE la duración real del audio: si supera el máximo, recorta de nuevo con
el ritmo real de la voz y vuelve a generar solo los trozos de audio que cambiaron. Nunca baja de 1.000 palabras (por eso
no admite duraciones menores de unos 7 minutos). Para otro límite: cambie "duracion_objetivo_min" (0 = usar palabras_objetivo).

18. KOKORO (VOZ LOCAL), CHAT POR VOZ Y PRESENTACIONES
-----------------------------------------------------
Archivos de Kokoro: kokoro-v1.0.onnx y voices-v1.0.bin. Se buscan en la carpeta actual, junto a main.py, en kokoro\ y
en assets\kokoro\, o se indican con las variables KOKORO_MODEL / KOKORO_VOICES (o "kokoro_modelo"/"kokoro_voces").
Voces en español: ef_dora, em_alex, em_santa. Velocidad: 0.5 a 2.0 (por defecto 1.0). Variables: KOKORO_VOZ, KOKORO_SPEED.

a) Agente de documentales con Kokoro:  "motor_voz": "kokoro"  en config.json (o "auto": edge-tts -> Kokoro -> voz local).
b) Chat por turnos (escribe, Llama responde, Kokoro lo lee; "salir" termina):
       .venv\Scripts\python chat_voz.py
       .venv\Scripts\python chat_voz.py --voz em_alex --velocidad 1.1
       .venv\Scripts\python chat_voz.py --elegir        (elegir voz y velocidad en pantalla)
       .venv\Scripts\python chat_voz.py --sin-voz        (solo texto)   --sin-reproducir (solo guarda los .wav)
   Los audios se guardan en audio_chat\respuesta_0001.wav, ... y se reproducen con winsound (incluido en Windows).
c) Presentaciones .pptx (necesita:  pip install python-pptx ):
       .venv\Scripts\python ollama_pptx.py "El ciclo del agua" -n 8
       .venv\Scripts\python ollama_pptx.py "El ciclo del agua" -n 8 --voz --voz-nombre em_alex
   Genera presentaciones\<tema>.pptx (título, viñetas y notas del orador) y, con --voz, presentaciones\<tema>_audio\
   diapositiva_01.wav ... leyendo las notas.


19. NARRAR CON KOKORO (solo cambiar la voz del agente de documentales)
---------------------------------------------------------------------
Valores EXACTOS usados (están en agente\voz_kokoro.py y en ejecutar_kokoro.bat):
    Modelo : C:\Users\USER\Desktop\kokoro_test\kokoro-v1.0.onnx
    Voces  : C:\Users\USER\Desktop\kokoro_test\voices-v1.0.bin
    Llamada: kokoro.create(texto, voice="ef_dora", speed=1.0, lang="es")  ->  sf.write(archivo.wav, samples, sample_rate)
PASO 1 - comprobar Kokoro solo, sin el resto del agente (con el Python que tenga kokoro-onnx instalado):
    C:\Users\USER\kokoro-llama\.venv\Scripts\python.exe probar_kokoro.py
    -> crea y reproduce audio_generado.wav. Si suena bien, el agente también funcionará.
PASO 2 - ejecutar el agente con Kokoro:   ejecutar_kokoro.bat   (acepta --tema "X", --max-temas 5, --comprobar, ...)
    El .bat busca solo un Python que tenga kokoro-onnx y soundfile (el venv de este programa, kokoro-llama\.venv,
    kokoro_test\.venv o el del sistema) y lo muestra: "Usando Python: ...". Para forzar uno:  set PYTHON_KOKORO=C:\ruta\python.exe
    Con ejecutar.bat normal (sin Kokoro instalado en su venv) el modo "auto" lo avisa y usa otra voz: no es un fallo silencioso.
El audio de cada video se guarda en la carpeta de su tema como  audio_generado.wav  (documental largo) y
audio_generado_short.wav (Short); el montaje de video usa exactamente esos archivos.
CAMBIO DE VOZ: si un tema ya estaba terminado con otra voz (p. ej. edge-tts), al ejecutar con Kokoro SE REHACE (antes se
omitía y por eso la voz no cambiaba). El guion no se vuelve a escribir: solo se regenera audio, subtítulos y video.
Otra voz o velocidad: KOKORO_VOZ (ef_dora, em_alex, em_santa) y KOKORO_SPEED (0.5-2.0) en el .bat, o en config.json
"kokoro_voz" / "kokoro_velocidad". La voz se prepara AL PRINCIPIO: si Kokoro no carga, lo dice enseguida (código 4).
Rendimiento: Kokoro corre en CPU (no medido en su equipo); el registro muestra el avance por trozos. El tope de 10 minutos del
documental se mantiene (mide la duración real del audio).


20. BIBLIOTECA POR CANALES DE REFERENCIA (carpeta del canal -> carpetas de lotes)
---------------------------------------------------------------------------------
Estructura que el agente reconoce SOLO (la lógica de cada tema no cambia):
    Biblioteca\
        Canal de referencia A\
            Lote 1\   (imágenes y videos; pueden estar también en subcarpetas)
            Lote 2\
        Canal de referencia B\
            Lote 1\
        Tema suelto\  (estructura plana de siempre: sigue funcionando igual)
Una carpeta es un CANAL cuando no tiene imágenes/videos sueltos y sus subcarpetas sí. Cada subcarpeta del canal es un tema
(un lote). Si una carpeta tiene medios sueltos, se trata como un tema normal.
Los resultados espejan esa estructura, así cada video queda junto a los de su canal:
    Biblioteca\_documentales_generados\
        canal_de_referencia_a\lote_1\   documental_largo.mp4, short_vertical.mp4, guiones, audio, subtítulos, informe.json
        canal_de_referencia_a\RESUMEN_CANAL.txt        estado de cada lote del canal (duración, motivo de fallo)
        canal_de_referencia_b\lote_1\ ...
        RESUMEN_POR_CANAL.txt   RESUMEN_CALIDAD.txt   registro_agente.json
        _lista_videos\          (para subir a mano)
            canal_de_referencia_a\01_lote_1\  1_LARGO_documental.mp4 / .txt (ficha) / .srt, 2_SHORT_vertical.mp4 ...
            canal_de_referencia_a\02_lote_2\  (la numeración se reinicia en cada canal)
            canal_de_referencia_a\LISTA_CANAL.txt   lista de subida solo de ese canal
            LISTA_GENERAL.txt                        todos, agrupados por "CANAL: ..."
Comandos útiles:
    ejecutar_kokoro.bat --listar                       muestra el árbol: canales, lotes y cuántas imágenes/videos tiene cada uno
    ejecutar_kokoro.bat --canal "Canal de referencia A"      procesa solo ese canal (ideal para tandas nocturnas)
    ejecutar_kokoro.bat --tema "Canal de referencia A/Lote 2" un lote concreto (también vale "canal_de_referencia_a/lote_2")
    ejecutar_kokoro.bat --estado                       estado de todos, agrupado por canal
    ejecutar_kokoro.bat --max-temas 5                  tandas de 5 lotes pendientes (los ya terminados con esta voz se omiten)
Un mismo nombre de lote puede repetirse en canales distintos sin que se mezclen. Las bibliotecas planas (sin canales)
siguen guardando todo como antes: _documentales_generados\tema\...

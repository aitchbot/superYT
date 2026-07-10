# -*- coding: utf-8 -*-
"""SuperYT - Descargador local de videos de YouTube y Odysee (videos individuales o listas)."""

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import yt_dlp
import imageio_ffmpeg
from yt_dlp.postprocessor.common import PostProcessor
from deep_translator import GoogleTranslator
import srt as libsrt

CARPETA_DEFECTO = os.path.join(os.path.expanduser("~"), "Downloads", "SuperYT")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
IDIOMAS_ES = ["es", "es-419", "es-ES", "es-MX", "es-AR"]
IDIOMAS_EN = ["en", "en-US", "en-GB", "en-orig"]
SEPARADOR_LINEA = " ¦ "  # reemplaza saltos de linea internos de un subtitulo al armar el lote a traducir


def _detectar_deno():
    """Ubica el ejecutable de Deno (requerido por YouTube para resolver retos de JS).

    Se busca primero en el PATH y, si todavía no está disponible ahí (winget recién
    actualiza el PATH en una sesión nueva de Windows), en la carpeta típica de WinGet.
    """
    ruta = shutil.which("deno")
    if ruta:
        return ruta
    paquetes = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    if os.path.isdir(paquetes):
        for nombre in os.listdir(paquetes):
            if nombre.lower().startswith("denoland.deno"):
                posible = os.path.join(paquetes, nombre, "deno.exe")
                if os.path.isfile(posible):
                    return posible
    return None


def _traducir_srt(ruta_entrada, ruta_salida, cola_msgs):
    """Traduce un .srt del inglés al español, línea por línea, en lotes para no hacer
    una request por cada subtítulo. Si un lote sale desalineado, reintenta ese lote
    subtítulo por subtítulo."""
    with open(ruta_entrada, encoding="utf-8", errors="ignore") as f:
        subs = list(libsrt.parse(f.read()))
    if not subs:
        return False

    traductor = GoogleTranslator(source="en", target="es")
    textos = [s.content.replace("\n", SEPARADOR_LINEA) for s in subs]
    traducidos = [None] * len(textos)

    LOTE_MAX_CARACTERES = 3500

    def traducir_lote(indices, lote):
        try:
            partes = traductor.translate("\n".join(lote)).split("\n")
            if len(partes) != len(lote):
                raise ValueError("el lote traducido no tiene la misma cantidad de líneas")
            for i, parte in zip(indices, partes):
                traducidos[i] = parte
        except Exception:
            for i, texto in zip(indices, lote):
                try:
                    traducidos[i] = traductor.translate(texto)
                except Exception:
                    traducidos[i] = texto  # si falla, se deja el original en inglés

    cola_msgs.put(("log", f"Traduciendo {len(textos)} subtítulos de inglés a español..."))
    indices_lote, lote, largo = [], [], 0
    for i, texto in enumerate(textos):
        if lote and largo + len(texto) > LOTE_MAX_CARACTERES:
            traducir_lote(indices_lote, lote)
            indices_lote, lote, largo = [], [], 0
        indices_lote.append(i)
        lote.append(texto)
        largo += len(texto) + 1
    if lote:
        traducir_lote(indices_lote, lote)

    for s, traduccion in zip(subs, traducidos):
        s.content = (traduccion or "").replace(SEPARADOR_LINEA, "\n")

    with open(ruta_salida, "w", encoding="utf-8") as f:
        f.write(libsrt.compose(subs))
    return True


class _TraductorSubtitulosPP(PostProcessor):
    """Postprocesador de yt-dlp: se queda solo con el subtítulo en español (traduciendo
    desde inglés si hace falta) y lo entrega como pidió el usuario: como archivo .srt
    aparte, o quemado (incrustado en la imagen) en el video ya remuxeado."""

    def __init__(self, cola_msgs, modo):
        super().__init__(None)
        self._cola_msgs = cola_msgs
        self._modo = modo  # "srt" o "hardsub"

    def run(self, info):
        subs = info.get("requested_subtitles") or {}
        if not subs:
            self._cola_msgs.put(("log", "El video no tiene subtítulos en español ni en inglés; se descarga sin subtítulos."))
            return [], info

        lang_conservar = next((l for l in IDIOMAS_ES if l in subs), None)
        if lang_conservar is not None:
            self._cola_msgs.put(("log", f"Subtítulos en español encontrados ({lang_conservar})."))

        if lang_conservar is None:
            lang_en = next((l for l in IDIOMAS_EN if l in subs), None)
            if lang_en is not None:
                ruta_en = subs[lang_en].get("filepath")
                if ruta_en and os.path.exists(ruta_en):
                    sin_ext, _srt = os.path.splitext(ruta_en)
                    sin_lang, _lang = os.path.splitext(sin_ext)
                    ruta_es = sin_lang + ".es.srt"
                    try:
                        _traducir_srt(ruta_en, ruta_es, self._cola_msgs)
                        subs["es"] = {"ext": "srt", "filepath": ruta_es, "name": "Español (traducido)"}
                        os.remove(ruta_en)
                        del subs[lang_en]
                        lang_conservar = "es"
                        self._cola_msgs.put(("log", "Subtítulos traducidos."))
                    except Exception as e:
                        self._cola_msgs.put(("log", f"⚠ No se pudieron traducir los subtítulos ({e}); se deja el original en inglés."))
                        lang_conservar = lang_en

        # nos quedamos solo con el idioma elegido; el resto de los subtitulos bajados se descartan
        for lang in list(subs.keys()):
            if lang != lang_conservar:
                ruta = subs[lang].get("filepath")
                if ruta and os.path.exists(ruta):
                    os.remove(ruta)
                del subs[lang]

        if not subs:
            return [], info

        ruta_srt = subs[lang_conservar].get("filepath")
        if not ruta_srt or not os.path.exists(ruta_srt):
            ruta_srt = self._reintentar_descarga_subtitulo(subs[lang_conservar], info)
        if not ruta_srt or not os.path.exists(ruta_srt):
            self._cola_msgs.put(("log", "⚠ No se pudo descargar el archivo de subtítulos (probable límite temporal de YouTube); el video queda sin subtítulos."))
            return [], info
        if self._modo == "hardsub":
            self._quemar_subtitulos(info, ruta_srt)
        else:
            self._cola_msgs.put(("log", f"Subtítulos guardados como archivo aparte: {os.path.basename(ruta_srt)}"))
        return [], info

    def _reintentar_descarga_subtitulo(self, sub_info, info, intentos=3, espera_inicial=3):
        """Si yt-dlp no pudo bajar el subtítulo (típicamente un 429 pasajero de YouTube),
        reintenta unas pocas veces con espera creciente usando la URL que ya tenemos."""
        url = sub_info.get("url")
        if not url:
            return None
        sin_ext, _ext = os.path.splitext(info["filepath"])
        destino = f"{sin_ext}.srt"
        for intento in range(1, intentos + 1):
            time.sleep(espera_inicial * intento)
            self._cola_msgs.put(("log", f"Reintentando descarga de subtítulos ({intento}/{intentos})..."))
            try:
                contenido = self._downloader.urlopen(url).read()
                with open(destino, "wb") as f:
                    f.write(contenido)
                return destino
            except Exception:
                continue
        return None

    def _quemar_subtitulos(self, info, ruta_srt):
        """Recodifica el video con el subtítulo dibujado sobre la imagen (permanente).

        Recodificar todo el video puede tardar varios minutos; reportamos el avance real
        (leído del propio ffmpeg) para que no parezca que la app se colgó."""
        ruta_video = info["filepath"]
        ffmpeg = self._downloader.params.get("ffmpeg_location") or "ffmpeg"
        ruta_filtro = ruta_srt.replace("\\", "/").replace(":", "\\:")
        temporal = f"{ruta_video}.quemado{os.path.splitext(ruta_video)[1]}"
        duracion = info.get("duration") or 0

        self._cola_msgs.put(("log", "Quemando subtítulos en el video (puede tardar varios minutos: se recodifica todo el video)..."))

        # stderr va a un archivo (no a un pipe) para evitar que ffmpeg se bloquee escribiendo
        # ahi si el buffer se llena mientras nosotros solo leemos stdout (deadlock clasico de subprocess).
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="ignore") as archivo_stderr:
            proceso = subprocess.Popen(
                [
                    ffmpeg, "-y", "-i", ruta_video,
                    "-vf", f"subtitles='{ruta_filtro}'",
                    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-c:a", "copy",
                    "-progress", "pipe:1", "-nostats",
                    temporal,
                ],
                stdout=subprocess.PIPE, stderr=archivo_stderr,
                universal_newlines=True, encoding="utf-8", errors="ignore",
            )
            for linea in proceso.stdout:
                linea = linea.strip()
                if linea.startswith("out_time=") and duracion:
                    try:
                        h, m, s = linea.split("=", 1)[1].split(":")
                        segundos = int(h) * 3600 + int(m) * 60 + float(s)
                        pct = max(0.0, min(100.0, segundos / duracion * 100))
                        self._cola_msgs.put(("progreso", pct, f"Quemando subtítulos... {pct:.0f}%"))
                    except (ValueError, IndexError):
                        pass
            codigo = proceso.wait()

            if codigo != 0:
                archivo_stderr.seek(0)
                ultimas_lineas = "\n".join(archivo_stderr.read().strip().splitlines()[-5:])
                self._cola_msgs.put(("log", f"⚠ No se pudieron quemar los subtítulos (ffmpeg terminó con error); se deja el .srt aparte.\n{ultimas_lineas}"))
                if os.path.exists(temporal):
                    os.remove(temporal)
                return

        os.replace(temporal, ruta_video)
        os.remove(ruta_srt)
        self._cola_msgs.put(("log", "Subtítulos quemados en el video."))


class Cancelado(Exception):
    pass


class SuperYT(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SuperYT - Descargador de YouTube y Odysee (by aitchbot)")
        self.geometry("760x600")
        self.minsize(620, 500)

        self.cola_msgs = queue.Queue()
        self.cancelar = False
        self.descargando = False

        self._crear_widgets()
        self.after(100, self._procesar_cola)

    def _crear_widgets(self):
        cont = ttk.Frame(self, padding=12)
        cont.pack(fill="both", expand=True)

        ttk.Label(cont, text="Pegá las URLs de YouTube u Odysee (videos o listas de reproducción), una por línea:").pack(anchor="w")

        self.txt_urls = tk.Text(cont, height=6, wrap="none")
        self.txt_urls.pack(fill="x", pady=(4, 10))
        self._agregar_menu_contextual(self.txt_urls)

        fila_carpeta = ttk.Frame(cont)
        fila_carpeta.pack(fill="x", pady=(0, 10))
        ttk.Label(fila_carpeta, text="Guardar en:").pack(side="left")
        self.var_carpeta = tk.StringVar(value=CARPETA_DEFECTO)
        entrada_carpeta = ttk.Entry(fila_carpeta, textvariable=self.var_carpeta)
        entrada_carpeta.pack(side="left", fill="x", expand=True, padx=6)
        self._agregar_menu_contextual(entrada_carpeta)
        ttk.Button(fila_carpeta, text="Elegir...", command=self._elegir_carpeta).pack(side="left")

        fila_ops = ttk.Frame(cont)
        fila_ops.pack(fill="x", pady=(0, 10))
        self.var_modo = tk.StringVar(value="video")
        ttk.Radiobutton(fila_ops, text="Mejor calidad (video + audio)", variable=self.var_modo, value="video").pack(side="left")
        ttk.Radiobutton(fila_ops, text="Solo audio (MP3)", variable=self.var_modo, value="audio").pack(side="left", padx=12)

        fila_formato = ttk.Frame(cont)
        fila_formato.pack(fill="x", pady=(0, 10))
        ttk.Label(fila_formato, text="Formato de archivo de video:").pack(side="left")
        self.var_formato = tk.StringVar(value="mkv")
        ttk.Radiobutton(fila_formato, text="MKV (recomendado)", variable=self.var_formato, value="mkv").pack(side="left", padx=(6, 0))
        ttk.Radiobutton(fila_formato, text="MP4", variable=self.var_formato, value="mp4").pack(side="left", padx=12)

        fila_listas = ttk.Frame(cont)
        fila_listas.pack(fill="x", pady=(0, 10))
        self.var_elegir = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            fila_listas,
            text="Elegir qué videos bajar de cada lista de reproducción (si no, baja todos)",
            variable=self.var_elegir,
        ).pack(side="left")

        fila_subs = ttk.Frame(cont)
        fila_subs.pack(fill="x", pady=(0, 10))
        ttk.Label(fila_subs, text="Subtítulos en español (si están disponibles):").pack(side="left")
        self.var_subtitulos = tk.StringVar(value="no")
        ttk.Radiobutton(fila_subs, text="No bajar", variable=self.var_subtitulos, value="no").pack(side="left", padx=(6, 0))
        ttk.Radiobutton(fila_subs, text="Como archivo .srt aparte", variable=self.var_subtitulos, value="srt").pack(side="left", padx=12)
        ttk.Radiobutton(fila_subs, text="Quemados en el video", variable=self.var_subtitulos, value="hardsub").pack(side="left")

        fila_btn = ttk.Frame(cont)
        fila_btn.pack(fill="x", pady=(0, 10))
        self.btn_descargar = ttk.Button(fila_btn, text="⬇  Descargar", command=self._iniciar)
        self.btn_descargar.pack(side="left")
        self.btn_cancelar = ttk.Button(fila_btn, text="Cancelar", command=self._cancelar, state="disabled")
        self.btn_cancelar.pack(side="left", padx=8)

        self.var_estado = tk.StringVar(value="Listo.")
        ttk.Label(cont, textvariable=self.var_estado).pack(anchor="w")

        self.barra = ttk.Progressbar(cont, maximum=100)
        self.barra.pack(fill="x", pady=(4, 10))

        ttk.Label(cont, text="Registro:").pack(anchor="w")
        marco_log = ttk.Frame(cont)
        marco_log.pack(fill="both", expand=True)
        self.txt_log = tk.Text(marco_log, state="disabled", wrap="word", background="#111", foreground="#ddd")
        scroll = ttk.Scrollbar(marco_log, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.txt_log.pack(side="left", fill="both", expand=True)

    @staticmethod
    def _agregar_menu_contextual(widget):
        """Agrega Cortar/Copiar/Pegar/Seleccionar todo al clic derecho (Tkinter no lo trae por defecto)."""
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Cortar", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Copiar", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Pegar", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_separator()
        if isinstance(widget, tk.Text):
            menu.add_command(label="Seleccionar todo", command=lambda: widget.tag_add("sel", "1.0", "end"))
        else:
            menu.add_command(label="Seleccionar todo", command=lambda: widget.select_range(0, "end"))
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def _elegir_carpeta(self):
        carpeta = filedialog.askdirectory(initialdir=self.var_carpeta.get() or os.path.expanduser("~"))
        if carpeta:
            self.var_carpeta.set(carpeta)

    def _cancelar(self):
        self.cancelar = True
        self.var_estado.set("Cancelando después del archivo actual...")

    def _iniciar(self):
        if self.descargando:
            return
        urls = [u.strip() for u in self.txt_urls.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning("SuperYT", "Pegá al menos una URL de YouTube u Odysee.")
            return
        carpeta = self.var_carpeta.get().strip() or CARPETA_DEFECTO
        os.makedirs(carpeta, exist_ok=True)

        self.cancelar = False
        self.descargando = True
        self.btn_descargar.config(state="disabled")
        self.btn_cancelar.config(state="normal")
        self.barra["value"] = 0

        hilo = threading.Thread(
            target=self._descargar,
            args=(urls, carpeta, self.var_modo.get(), self.var_formato.get(), self.var_elegir.get(), self.var_subtitulos.get()),
            daemon=True,
        )
        hilo.start()

    # ---------- lógica de descarga (corre en hilo aparte) ----------

    def _descargar(self, urls, carpeta, modo, formato, elegir, subtitulos):
        def hook(d):
            if self.cancelar:
                raise Cancelado()
            if d["status"] == "downloading":
                pct = ANSI.sub("", d.get("_percent_str", "0%")).strip()
                vel = ANSI.sub("", d.get("_speed_str", "")).strip()
                nombre = os.path.basename(d.get("filename", ""))
                try:
                    self.cola_msgs.put(("progreso", float(pct.replace("%", "")), f"{nombre}  {pct}  {vel}"))
                except ValueError:
                    pass
            elif d["status"] == "finished":
                self.cola_msgs.put(("log", f"✔ Completado: {os.path.basename(d.get('filename', ''))}"))

        class Logger:
            def debug(s, msg):
                prefijos = (
                    "[download] Destination", "[Merger]", "[ExtractAudio]",
                    "[VideoRemuxer]", "[EmbedSubtitle]", "[info] There are no subtitles",
                    "[info] Writing video subtitles", "Embedding subtitles",
                )
                if msg.startswith(prefijos):
                    self.cola_msgs.put(("log", ANSI.sub("", msg)))
            def info(s, msg):
                self.cola_msgs.put(("log", ANSI.sub("", msg)))
            def warning(s, msg):
                self.cola_msgs.put(("log", "⚠ " + ANSI.sub("", msg)))
            def error(s, msg):
                self.cola_msgs.put(("log", "✖ " + ANSI.sub("", msg)))

        # Si la URL es una lista, crea una subcarpeta con el nombre de la lista y numera los videos.
        plantilla = os.path.join(carpeta, "%(playlist_title|)s", "%(playlist_index&{} - |)s%(title)s [%(id)s].%(ext)s")

        opciones = {
            "outtmpl": plantilla,
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
            "progress_hooks": [hook],
            "logger": Logger(),
            "ignoreerrors": True,      # si un video de la lista falla, sigue con el resto
            "retries": 5,
            "noprogress": True,
            "windowsfilenames": True,
        }
        deno = _detectar_deno()
        if deno:
            opciones["js_runtimes"] = {"deno": {"path": deno}}
        if modo == "audio":
            opciones.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }],
            })
        else:
            opciones["format"] = "bestvideo+bestaudio/best"
            opciones["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": formato}]
            if subtitulos != "no":
                opciones["writesubtitles"] = True
                opciones["writeautomaticsub"] = True
                opciones["subtitleslangs"] = IDIOMAS_ES + IDIOMAS_EN
                opciones["subtitlesformat"] = "srt/best"

        try:
            for i, url in enumerate(urls, 1):
                if self.cancelar:
                    break
                self.cola_msgs.put(("log", f"\n▶ ({i}/{len(urls)}) Procesando: {url}"))

                ops = dict(opciones)
                if elegir:
                    info_lista = self._info_lista(url)
                    if info_lista is not None:
                        indices = self._pedir_seleccion(info_lista)
                        if indices is None:
                            self.cola_msgs.put(("log", "⏭ Lista omitida."))
                            continue
                        total = len(info_lista["entries"])
                        if len(indices) < total:
                            ops["playlist_items"] = self._compactar(indices)
                            self.cola_msgs.put(("log", f"Seleccionados {len(indices)} de {total} videos."))

                with yt_dlp.YoutubeDL(ops) as ydl:
                    if subtitulos != "no" and modo != "audio":
                        ydl.add_post_processor(_TraductorSubtitulosPP(self.cola_msgs, subtitulos), when="post_process")
                    ydl.download([url])
            if self.cancelar:
                self.cola_msgs.put(("fin", "Descarga cancelada."))
            else:
                self.cola_msgs.put(("fin", f"¡Listo! Archivos guardados en: {carpeta}"))
        except Cancelado:
            self.cola_msgs.put(("fin", "Descarga cancelada."))
        except Exception as e:
            self.cola_msgs.put(("log", f"✖ Error: {e}"))
            self.cola_msgs.put(("fin", "Terminó con errores. Revisá el registro."))

    def _info_lista(self, url):
        """Devuelve la info de la lista (con sus videos) o None si la URL es un video suelto."""
        self.cola_msgs.put(("estado", "Obteniendo videos de la lista..."))
        opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True}
        deno = _detectar_deno()
        if deno:
            opts["js_runtimes"] = {"deno": {"path": deno}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            return None
        if info and info.get("_type") == "playlist":
            entradas = list(info.get("entries") or [])
            if entradas:
                info["entries"] = entradas
                return info
        return None

    def _pedir_seleccion(self, info):
        """Pide al hilo de la interfaz que muestre el diálogo de selección y espera la respuesta.

        Devuelve una lista de índices (base 1) o None si se omitió la lista."""
        resultado = {}
        evento = threading.Event()
        self.cola_msgs.put(("seleccion", info, resultado, evento))
        evento.wait()
        return resultado.get("indices")

    @staticmethod
    def _compactar(indices):
        """Convierte [1,2,3,5,7,8] en '1-3,5,7-8' para playlist_items de yt-dlp."""
        rangos = []
        ini = fin = indices[0]
        for n in indices[1:]:
            if n == fin + 1:
                fin = n
            else:
                rangos.append((ini, fin))
                ini = fin = n
        rangos.append((ini, fin))
        return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in rangos)

    @staticmethod
    def _fmt_duracion(segundos):
        if not segundos:
            return ""
        segundos = int(segundos)
        h, resto = divmod(segundos, 3600)
        m, s = divmod(resto, 60)
        return f"  ({h}:{m:02d}:{s:02d})" if h else f"  ({m}:{s:02d})"

    # ---------- diálogo de selección (corre en el hilo de la interfaz) ----------

    def _dialogo_seleccion(self, info, resultado, evento):
        dlg = tk.Toplevel(self)
        dlg.title("Elegir videos para descargar")
        dlg.geometry("680x520")
        dlg.transient(self)
        dlg.grab_set()

        entradas = info["entries"]
        titulo = info.get("title") or "Lista de reproducción"
        ttk.Label(dlg, text=f"{titulo}  —  {len(entradas)} videos", padding=(10, 8),
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w")

        fila_sel = ttk.Frame(dlg, padding=(10, 0))
        fila_sel.pack(fill="x")
        variables = []
        ttk.Button(fila_sel, text="Todos", command=lambda: [v.set(True) for v in variables]).pack(side="left")
        ttk.Button(fila_sel, text="Ninguno", command=lambda: [v.set(False) for v in variables]).pack(side="left", padx=6)

        marco = ttk.Frame(dlg, padding=10)
        marco.pack(fill="both", expand=True)
        canvas = tk.Canvas(marco, highlightthickness=0)
        scroll = ttk.Scrollbar(marco, orient="vertical", command=canvas.yview)
        interior = ttk.Frame(canvas)
        interior.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=interior, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        dlg.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        for n, ent in enumerate(entradas, 1):
            v = tk.BooleanVar(value=True)
            variables.append(v)
            texto = f"{n:>3}.  {ent.get('title') or ent.get('id') or '(sin título)'}{self._fmt_duracion(ent.get('duration'))}"
            ttk.Checkbutton(interior, text=texto, variable=v).pack(anchor="w")

        def cerrar(indices):
            resultado["indices"] = indices
            dlg.unbind_all("<MouseWheel>")
            dlg.grab_release()
            dlg.destroy()

        def confirmar():
            marcados = [n for n, v in enumerate(variables, 1) if v.get()]
            if not marcados:
                messagebox.showwarning("SuperYT", "No marcaste ningún video. Usá «Omitir lista» si no querés bajar nada.", parent=dlg)
                return
            cerrar(marcados)

        fila_btn = ttk.Frame(dlg, padding=10)
        fila_btn.pack(fill="x")
        ttk.Button(fila_btn, text="⬇  Descargar seleccionados", command=confirmar).pack(side="left")
        ttk.Button(fila_btn, text="Omitir lista", command=lambda: cerrar(None)).pack(side="left", padx=8)
        dlg.protocol("WM_DELETE_WINDOW", lambda: cerrar(None))

        dlg.wait_window()
        evento.set()

    # ---------- actualización de la interfaz ----------

    def _procesar_cola(self):
        try:
            while True:
                tipo, *datos = self.cola_msgs.get_nowait()
                if tipo == "progreso":
                    pct, texto = datos
                    self.barra["value"] = pct
                    self.var_estado.set(texto)
                elif tipo == "log":
                    self._log(datos[0])
                elif tipo == "estado":
                    self.var_estado.set(datos[0])
                elif tipo == "seleccion":
                    self._dialogo_seleccion(*datos)
                elif tipo == "fin":
                    self._log("\n" + datos[0])
                    self.var_estado.set(datos[0])
                    self.barra["value"] = 0
                    self.descargando = False
                    self.btn_descargar.config(state="normal")
                    self.btn_cancelar.config(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._procesar_cola)

    def _log(self, texto):
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", texto + "\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")


if __name__ == "__main__":
    app = SuperYT()
    app.mainloop()

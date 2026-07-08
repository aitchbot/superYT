# SuperYT — Descargador local de YouTube y Odysee

Aplicación de escritorio para descargar videos de YouTube u Odysee en la mejor calidad disponible.
Soporta videos individuales y listas de reproducción completas (en lotes).

## Primeros pasos (solo una vez)

1. Descomprimí el zip en una carpeta cualquiera.
2. **Doble clic en `Instalar.bat`.**
   - Windows puede mostrar una advertencia azul de SmartScreen ("Windows protegió su PC")
     porque el archivo viene de internet. Hacé clic en **"Más información"** y después
     en **"Ejecutar de todas formas"**. Es un script normal, no un antivirus falso positivo raro:
     pasa con cualquier `.bat`/`.ps1` descargado de un lugar que Windows no reconoce.
   - Se va a abrir una ventana negra que instala automáticamente todo lo necesario
     (Python, el motor de descarga y demás). Puede tardar varios minutos según la conexión.
   - Al final va a decir **"Instalación completa"**. Presioná ENTER para cerrar esa ventana.
3. Necesitás conexión a internet durante este paso (no después, para usar la app).

Este paso se hace **una sola vez** por computadora.

## Cómo ejecutarla (después de instalar)

**Doble clic en `SuperYT.bat`.** Eso es todo.

Si preferís la terminal:

```
python app.py
```

## Cómo usarla

1. Pegá una o varias URLs de YouTube u Odysee en el cuadro de texto, **una por línea**.
   - Video de YouTube: `https://www.youtube.com/watch?v=...`
   - Lista de YouTube: `https://www.youtube.com/playlist?list=...`
   - Video de Odysee: `https://odysee.com/@canal/nombre-del-video`
   - Canal de Odysee (se trata como lista): `https://odysee.com/@canal`
2. Elegí la carpeta de destino (por defecto: `Descargas\SuperYT`).
3. Elegí el modo:
   - **Mejor calidad (video + audio)**: descarga la máxima resolución disponible (4K si existe).
   - **Solo audio (MP3)**: extrae únicamente el audio en la mejor calidad.
4. (Opcional) Marcá **"Elegir qué videos bajar de cada lista de reproducción"**:
   antes de descargar una lista, se abre una ventana con todos sus videos para que
   marques cuáles querés (con botones *Todos* / *Ninguno*, o podés omitir la lista entera).
   Si la casilla está desmarcada, se baja la lista completa.
5. Presioná **Descargar**.

Las listas de reproducción se guardan en una subcarpeta con el nombre de la lista,
con los videos numerados según su posición en la lista. Si un video de la lista
falla, la descarga continúa con el resto.

## Notas

- Para lograr la máxima calidad, algunos videos se guardan como `.mkv` o `.webm`
  (YouTube publica las resoluciones altas en esos formatos). Windows 11 los
  reproduce nativamente, igual que VLC.
- Si YouTube cambia algo y las descargas empiezan a fallar, actualizá yt-dlp:
  ```
  python -m pip install --upgrade yt-dlp
  ```

## ¿Qué instala `Instalar.bat`?

- **Python 3.12** (si no lo tenías).
- **yt-dlp** y **ffmpeg** (el motor de descarga y el que une video+audio).
- **Deno** (un motor de JavaScript que YouTube exige para resolver ciertos videos).

Todo se instala en tu usuario de Windows, sin tocar nada del sistema ni pedir
permisos de administrador raros.

## Instalación manual (por si `Instalar.bat` falla)

1. Instalar Python 3.12+ desde https://www.python.org (marcar "Add to PATH").
2. Instalar Deno desde https://deno.com (o `winget install DenoLand.Deno`).
3. En la carpeta del proyecto: `python -m pip install -r requirements.txt`
4. Ejecutar con `SuperYT.bat` o `python app.py`.

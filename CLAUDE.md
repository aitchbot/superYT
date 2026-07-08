# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SuperYT is a single-file Windows desktop app (Tkinter GUI) that wraps `yt-dlp` to download
videos or full playlists in the best available quality, individually or in batch. It has no
site-specific logic: any URL `yt-dlp` can extract works, which today in practice means YouTube
(videos/playlists) and Odysee (videos/channels, since yt-dlp's `lbry` extractor treats an Odysee
channel as a playlist). Don't add per-site branching to `app.py` — new site support should come
for free from yt-dlp unless something breaks that assumption. It's built for a non-technical end
user (an installer script does all setup), so all UI text, identifiers, and comments are in
Spanish — keep new code consistent with that.

## Commands

Run the app:
```
python app.py
```
(On a machine where Python isn't on PATH, use the full path installed by the setup script:
`%LOCALAPPDATA%\Programs\Python\Python312\python.exe app.py`.)

Install/update dependencies:
```
python -m pip install -r requirements.txt
```

There is no build step, lint config, or test suite in this repo.

## Architecture

Everything lives in [app.py](app.py), a single `tk.Tk` subclass (`SuperYT`). There is no
separate backend/frontend split — the GUI class also owns the download logic.

**Threading model.** Tkinter is not thread-safe, so downloads never touch widgets directly:
- `_iniciar` spawns a daemon thread running `_descargar`, which does all `yt_dlp.YoutubeDL` work.
- That thread only communicates by putting tuples onto `self.cola_msgs` (a `queue.Queue`):
  `("progreso", pct, texto)`, `("log", texto)`, `("estado", texto)`, `("seleccion", info, resultado, evento)`,
  `("fin", texto)`.
- `_procesar_cola`, scheduled via `self.after(100, ...)`, drains the queue on the main thread and
  updates widgets. Any new message type must be handled there.

**Cross-thread playlist picker.** When the "elegir qué videos bajar" checkbox is on, the worker
thread calls `_info_lista` (flat playlist extraction) then `_pedir_seleccion`, which posts a
`"seleccion"` message carrying a shared `dict` and a `threading.Event`, then blocks on
`evento.wait()`. The main thread's queue loop calls `_dialogo_seleccion` to build the `Toplevel`
checklist; closing it (confirm or "Omitir lista") writes the chosen indices into the shared dict
and sets the event, unblocking the worker. Selected indices get compacted into a yt-dlp
`playlist_items` range string via `_compactar` (e.g. `[1,2,3,5,7,8]` → `"1-3,5,7-8"`).

**Output template.** `outtmpl` uses yt-dlp field replacement so a lone video lands directly in
the destination folder, while a playlist gets its own subfolder named after the playlist title,
with entries prefixed by playlist index. Don't simplify this to a plain f-string — the
conditional `%(playlist_title|)s` syntax is what avoids creating an empty subfolder for
non-playlist URLs.

**ffmpeg** is not a system dependency — it ships via the `imageio_ffmpeg` package and its path is
passed to yt-dlp as `ffmpeg_location`.

**Deno requirement.** YouTube requires solving a JS challenge for some formats, and yt-dlp needs
a JS runtime (Deno) to do that. `_detectar_deno()` looks for `deno` on PATH, and if not found,
falls back to scanning the WinGet packages folder directly — this matters because a script that
just installed Deno via `winget` won't see it on PATH until a new shell/session starts, so PATH
alone can't be trusted right after install. This same lookup is duplicated in `_descargar` and
`_info_lista`, both passing `js_runtimes` to `YoutubeDL`.

## Installer scripts

`Instalar.bat` → `Instalar.ps1` is a separate, idempotent setup flow aimed at non-technical
end users (distributed as a zip alongside the app). It checks for `winget`, then Python 3.12,
then installs `requirements.txt` deps, then Deno — skipping anything already present rather than
reinstalling. `SuperYT.bat` is the everyday launcher; it looks for `pythonw.exe` at the same
WinGet-installed path first, falling back to `pythonw` on PATH.

When editing installer logic, keep it non-destructive and safe to re-run.

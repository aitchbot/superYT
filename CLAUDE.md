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

A JS runtime alone isn't enough, though: yt-dlp also needs the actual challenge-solving script
(EJS). Rather than passing `--remote-components ejs:github`/`ejs:npm` (which makes yt-dlp fetch
and run arbitrary code from the internet at download time — not something to enable silently in
an app handed to non-technical users), the `yt-dlp-ejs` PyPI package is a `requirements.txt`
dependency instead, so the solver ships locally with a known, pinned version.

**Subtitle handling via a custom yt-dlp postprocessor.** yt-dlp can download subtitles but can't
translate or burn them in, and there's no way to conditionally pick "Spanish if present, else
translated English" using yt-dlp's declarative `postprocessors` option list alone (subtitle files
land on disk before any `post_process`-stage postprocessor runs, but you can't inspect/rewrite
them from there without a custom class). `_TraductorSubtitulosPP` (subclasses yt-dlp's
`PostProcessor`) is registered via `ydl.add_post_processor(..., when="post_process")` *after*
the instance is created, so it runs after the declarative `FFmpegVideoRemuxer` entry (which was
registered earlier, during `YoutubeDL.__init__` from the `postprocessors` option) — this ordering
is what guarantees translation/burning happens on the already-remuxed final container. Its
`run()` picks one subtitle language to keep (prefers anything in `IDIOMAS_ES`; otherwise
translates the first `IDIOMAS_EN` match via `_traducir_srt` and synthesizes an `"es"` entry),
deletes every other downloaded subtitle file, then either leaves the `.srt` on disk as-is
(`modo == "srt"`) or burns it into the video pixels via its own `subprocess` call to ffmpeg's
`subtitles` filter (`modo == "hardsub"`, re-encodes with `libx264` since burning requires
pixel-level compositing — can't be a stream copy like the rest of the pipeline). There is
deliberately no "soft-embed as a selectable track" option: the user was offered that choice and
picked exactly two outcomes (plain `.srt` file, or permanently burned-in), so don't reintroduce
`FFmpegEmbedSubtitlePP` without checking that's actually wanted. `_traducir_srt` batches cues into
one translation call per ~3500 characters (falls back to per-cue translation if a batch comes
back misaligned) to avoid one network round-trip per subtitle line.

**Subtitle language codes are an intentional allowlist, not a regex.** `IDIOMAS_ES`/`IDIOMAS_EN`
list exact codes (`es`, `es-419`, `es-ES`, ...) instead of a `"es.*"` pattern. YouTube's automatic
captions expose a chain-translation matrix where e.g. `es-ar` means "Spanish translated from
Arabic" — a broad regex would match and embed several redundant/lower-quality auto-translated
tracks alongside the real one.

## Installer scripts

`Instalar.bat` → `Instalar.ps1` is a separate, idempotent setup flow aimed at non-technical
end users (distributed as a zip alongside the app). It checks for `winget`, then Python 3.12,
then installs `requirements.txt` deps, then Deno — skipping anything already present rather than
reinstalling. `SuperYT.bat` is the everyday launcher; it looks for `pythonw.exe` at the same
WinGet-installed path first, falling back to `pythonw` on PATH.

When editing installer logic, keep it non-destructive and safe to re-run.

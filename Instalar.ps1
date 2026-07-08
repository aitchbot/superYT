# Instalador de SuperYT: deja la PC lista para correr la app (Python, yt-dlp, ffmpeg y Deno).
$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot

function Existe-Comando($nombre) {
    return [bool](Get-Command $nombre -ErrorAction SilentlyContinue)
}

Write-Host "=== Instalador de SuperYT ===" -ForegroundColor Cyan
Write-Host ""

# 1) winget (Administrador de paquetes de Windows)
if (-not (Existe-Comando "winget")) {
    Write-Host "ERROR: no se encontro 'winget' (Administrador de paquetes de Windows)." -ForegroundColor Red
    Write-Host "Instala 'Instalador de aplicaciones' desde la Microsoft Store, o actualiza Windows, y volve a correr este script." -ForegroundColor Yellow
    Read-Host "Presiona ENTER para salir"
    exit 1
}

# 2) Python
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}
if (-not (Test-Path $python)) {
    Write-Host "Instalando Python 3.12 (puede tardar unos minutos)..." -ForegroundColor Cyan
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements --silent
    $python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (-not (Test-Path $python)) {
        Write-Host "ERROR: Python se instalo pero no se encontro en la ruta esperada." -ForegroundColor Red
        Write-Host "Cerra esta ventana, abri una terminal nueva y volve a correr Instalar.bat." -ForegroundColor Yellow
        Read-Host "Presiona ENTER para salir"
        exit 1
    }
    Write-Host "Python instalado correctamente." -ForegroundColor Green
} else {
    Write-Host "Python ya esta instalado: $python" -ForegroundColor Green
}

# 3) Dependencias de Python (yt-dlp + ffmpeg incluido)
Write-Host ""
Write-Host "Instalando yt-dlp y ffmpeg (via pip)..." -ForegroundColor Cyan
& $python -m pip install --upgrade pip --quiet
& $python -m pip install --upgrade -r "$raiz\requirements.txt" --quiet
Write-Host "Listo." -ForegroundColor Green

# 4) Deno (necesario para que YouTube funcione correctamente)
Write-Host ""
$tieneDeno = Existe-Comando "deno"
if (-not $tieneDeno) {
    $paquetesWinget = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
    if (Test-Path $paquetesWinget) {
        $tieneDeno = [bool](Get-ChildItem $paquetesWinget -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "DenoLand.Deno*" })
    }
}
if (-not $tieneDeno) {
    Write-Host "Instalando Deno (motor de JavaScript que necesita YouTube)..." -ForegroundColor Cyan
    winget install --id DenoLand.Deno -e --accept-source-agreements --accept-package-agreements --silent
    Write-Host "Deno instalado correctamente." -ForegroundColor Green
} else {
    Write-Host "Deno ya esta instalado." -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Instalacion completa ===" -ForegroundColor Cyan
Write-Host "Ya podes usar SuperYT.bat (doble clic) para abrir el programa." -ForegroundColor Green
Read-Host "Presiona ENTER para cerrar"

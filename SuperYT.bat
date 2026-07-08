@echo off
rem Lanzador de SuperYT (doble clic para abrir la aplicacion)
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0app.py"
) else (
    start "" pythonw "%~dp0app.py"
)

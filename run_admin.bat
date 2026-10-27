@echo off
:: XZ Tour Videos admin launcher: paste a video link, pick the song, save it to data\shows\<show>.json.
:: Starts tools\admin_server.py on port 5041 (reuses it if already running), then opens the page.
:: Normally launched hidden via run_admin.vbs. Run this .bat directly for a visible console.
setlocal
cd /d "%~dp0"
set "PORT=5041"

netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo Starting admin server on port %PORT% ...
  start "XZ Tour admin server" /B cmd /c python tools\admin_server.py
  ping -n 3 127.0.0.1 >nul
) else (
  echo Reusing server already listening on port %PORT%.
)

start "" "http://localhost:%PORT%/"
endlocal

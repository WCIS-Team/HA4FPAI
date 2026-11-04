@echo off
REM 5th stage: icraft-generate must run in the local Windows CLI (ZG codegen)
setlocal
set V=S:\work\WCIS\workenv\hifigan_prelu\icraft
set GEN=%V%\gen
if not exist "%GEN%" mkdir "%GEN%"

"C:\Icraft\CLI v3.39.0\bin\icraft-generate.exe" ^
  --json  "%V%\adapt\hifigan_v2_prelu_adapted.json" ^
  --raw   "%V%\adapt\hifigan_v2_prelu_adapted.raw" ^
  --network hifigan_v2_prelu ^
  --jr_path "%GEN%" ^
  --log_path "%GEN%"
echo generate rc=%ERRORLEVEL%
dir /b "%GEN%"
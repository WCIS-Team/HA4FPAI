@echo off
setlocal
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.8 or newer is required on this workstation.
  exit /b 1
)
python -X utf8 "%~dp0deploy.py" --apply --target board --profile online --models all --ask-password --work-dir "%~dp0..\..\..\workenv\lang-deploy" %*
set "deploy_result=%ERRORLEVEL%"
if not "%deploy_result%"=="0" echo Deployment failed. See the diagnostic output above.
pause
exit /b %deploy_result%

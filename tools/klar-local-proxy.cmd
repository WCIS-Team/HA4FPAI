@echo off
REM ============================================================
REM  klar-local-proxy.cmd
REM  在你自己的电脑上跑 klar 免 401 代理（token 只存本机）
REM
REM  用法：双击本文件。窗口别关，浏览器开 http://127.0.0.1:10521/
REM
REM  前置：token 已抄到 %USERPROFILE%\.klar-token
REM  抄取命令（只需一次）：
REM    plink -ssh -batch -pw fmsh root@192.168.1.113 "cat /mnt/data/klar/.klar-token" > "%USERPROFILE%\.klar-token"
REM ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist "%USERPROFILE%\.klar-token" (
  echo [错误] 找不到 %USERPROFILE%\.klar-token
  echo.
  echo 请先执行一次：
  echo   plink -ssh -batch -pw fmsh root@192.168.1.113 "cat /mnt/data/klar/.klar-token" ^> "%USERPROFILE%\.klar-token"
  echo.
  pause
  exit /b 2
)

echo.
echo  klar 本地代理启动中...
echo  浏览器请开： http://127.0.0.1:10521/
echo  （这个窗口保持打开；Ctrl+C 或关窗口即停止）
echo.

python "%~dp0klar_local_proxy.py" --port 10521 --board 192.168.1.113 --board-port 10520

if errorlevel 1 (
  echo.
  echo [失败] 代理退出。若提示找不到 python，请改用完整路径，例如：
  echo   "%%LOCALAPPDATA%%\Programs\Python\Python313\python.exe" klar_local_proxy.py
  pause
)
endlocal

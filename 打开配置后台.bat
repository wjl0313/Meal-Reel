@echo off
REM ============================================================
REM  每日饮食 · 一键打开配置后台
REM
REM  双击本文件即可：自动停掉占用 8765 的旧进程 → 启动 serve.py
REM  → 打开浏览器。关掉这个黑窗口就停服务。
REM
REM  为什么要有这个脚本：HANDOFF §6 记录过一个坑 —— 上次残留的
REM  serve.py 还占着端口时，新进程会直接退出（表现为 exit code 127
REM  或"起不来"），看起来像服务崩了，其实只是端口没释放。
REM ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set PORT=8765
if not "%~1"=="" set PORT=%~1

echo [1/3] 释放端口 %PORT% ...
for /f "tokens=*" %%P in ('powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue).OwningProcess"') do (
    echo       停止旧进程 PID %%P
    taskkill /PID %%P /F >nul 2>&1
)

echo [2/3] 启动配置后台 ...
start "" http://127.0.0.1:%PORT%/
python serve.py --port %PORT%

echo.
echo 服务已停止。
pause

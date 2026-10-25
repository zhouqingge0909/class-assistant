@echo off
rem 班级助手（局域网版）启动脚本。打包时会转成 GBK 编码，中文 Windows 的命令行才认得。
title 班级助手（这个窗口不要关，可以最小化）
cd /d "%~dp0app"
if not exist "python\python.exe" goto broken
if "%~1"=="firewall" goto firewall

rem 第一次运行：在防火墙里放行班级助手，手机和一体机才连得上（要点一次"是"）
netsh advfirewall firewall show rule name=ClassAssistant >nul 2>&1
if not errorlevel 1 goto run
echo.
echo  第一次运行：要让手机和一体机能连上这台电脑。
echo  如果弹出"你要允许此应用对你的设备进行更改吗"，请点"是"。
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'firewall' -Verb RunAs -Wait" >nul 2>&1

:run
python\python.exe server.py
if errorlevel 3 goto stopped
echo.
echo  班级助手意外停止了，5 秒后自动重新启动……
timeout /t 5 /nobreak >nul
goto run

:stopped
echo.
echo  按任意键关闭窗口……
pause >nul
exit /b

:firewall
netsh advfirewall firewall delete rule name=ClassAssistant >nul 2>&1
netsh advfirewall firewall add rule name=ClassAssistant dir=in action=allow program="%~dp0app\python\python.exe" enable=yes profile=any >nul
exit /b

:broken
echo  找不到 app\python\python.exe：请先把压缩包"全部解压"，再双击解压出来的文件夹里的「启动班级助手」。
pause

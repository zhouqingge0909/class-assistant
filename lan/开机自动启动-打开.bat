@echo off
rem 在"启动"文件夹里放一个小脚本：登录 Windows 后自动最小化启动班级助手
set "S=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ClassAssistant.bat"
> "%S%" echo @start "" /min "%~dp0启动班级助手.bat"
if exist "%S%" (echo  已设置：这台电脑开机、登录 Windows 后会自动启动班级助手。) else (echo  设置失败，请把这个文件夹放到 D 盘等普通位置后再试。)
echo  以后如果移动了班级助手的文件夹，要重新双击一次这个文件。
echo.
pause

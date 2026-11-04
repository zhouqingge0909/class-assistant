@echo off
rem 打开公共模式：老师可以用注册码自己建班。打包时会转成 GBK 编码，中文 Windows 的命令行才认得。
title 开启老师自助注册
cd /d "%~dp0app"
if not exist "python\python.exe" goto broken
if not exist "config.json" goto notstarted

python\python.exe -c "import json,sys;c=json.load(open('config.json',encoding='utf-8-sig'));sys.exit(3 if c.get('public') else 0)"
if errorlevel 4 goto failed
if errorlevel 3 goto already
if errorlevel 1 goto failed

echo.
echo  开启后，老师可以用注册码自己建班，自动成为这个班的班主任。
echo.
echo  注意：每个班会换成新的教室密钥，已经装好的教室一体机会连不上，
echo        要到工作台「班级管理」复制新的安装命令，每台重装一次。
echo.
set "ANS="
set /p "ANS= 确定开启吗？输入 Y 后按回车（其他键取消）："
if /i not "%ANS%"=="Y" goto cancelled

copy /y config.json config.json.bak >nul
python\python.exe -c "import json;p='config.json';c=json.load(open(p,encoding='utf-8-sig'));c['public']=True;f=open(p,'w',encoding='utf-8');json.dump(c,f,ensure_ascii=False,indent=2);f.close()"
if errorlevel 1 goto failed

echo.
echo  已开启。接下来：
echo.
echo  1. 如果班级助手的黑色窗口开着，把它关掉，再双击「启动班级助手」。
echo     不重新启动的话，开启不会生效。
echo  2. 用管理员密码登录工作台，到「记录」里找「网友注册」，设一个注册码。
echo  3. 把那里的注册链接和注册码发给老师。
echo  4. 已经装好的教室一体机，每台重装一次。
goto end

:already
echo.
echo  老师自助注册已经开启过了，不用再开。
echo.
echo  如果工作台的「记录」里还没有「网友注册」，请关掉黑色窗口，再双击「启动班级助手」。
goto end

:cancelled
echo.
echo  已取消，什么都没有改。
goto end

:notstarted
echo.
echo  还没有设置文件：请先双击「启动班级助手」启动一次，再回来双击这个文件。
goto end

:failed
echo.
echo  开启失败，设置文件可能被改坏了。请把这个窗口拍照发给帮你安装的人。
goto end

:broken
echo.
echo  找不到 app\python\python.exe：请先把压缩包"全部解压"，再双击解压出来的文件夹里的这个文件。

:end
echo.
pause

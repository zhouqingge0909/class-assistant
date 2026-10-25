# 班级助手 - Windows 一体机安装（安装 + 立即启动 + 开机自启）
# 用法：按 Win+R，粘贴工作台「班级管理」里的 Windows 安装命令，回车。
# 服务器下发时会填好 __SERVER__ / __CLASS__ / __KEY__；不需要管理员权限，全部装在当前用户下。
$ErrorActionPreference = 'Stop'
$Server = '__SERVER__'; $Class = '__CLASS__'; $Key = '__KEY__'
$Host.UI.RawUI.WindowTitle = '班级助手 - 安装一体机接收端'
function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Finish { Write-Host ''; Read-Host '按回车关闭这个窗口' | Out-Null; exit }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch {}  # 老系统默认不开 TLS1.2
$wc = New-Object Net.WebClient
$wc.Encoding = [Text.Encoding]::UTF8
$Dir = Join-Path $env:USERPROFILE '.classcall'

try {
  if (-not $Class -or $Class.StartsWith('__') -or -not $Key -or $Key.StartsWith('__')) {
    Say '× 安装命令不完整，请到工作台「班级管理」重新复制 Windows 安装命令' Red; Finish
  }
  Say "班级助手 · $Class" Cyan
  Say '== 1/4 检查班级和密钥'
  try { $null = $wc.DownloadString("$Server/api/poll?class=$([Uri]::EscapeDataString($Class))&key=$([Uri]::EscapeDataString($Key))") }
  catch {
    if ($_.Exception.Message -match '403') { Say '× 班级或密钥不对：请到工作台重新复制安装命令' Red } else { Say "× 连不上服务器 $Server ，请检查一体机能否上网" Red }
    Finish
  }
  Say '   √ 正确'

  Say '== 2/4 检查 Python（第一次要下载安装，约 1–3 分钟）'
  function Test-Py($exe) {
    if (-not $exe -or -not (Test-Path $exe) -or $exe -like '*WindowsApps*') { return $false }
    try { return ((& $exe -c "import tkinter,sys;print(sys.version_info>=(3,8))" 2>$null) -eq 'True') } catch { return $false }
  }
  function Find-Py {
    $c = @()
    foreach ($root in "$env:LOCALAPPDATA\Programs\Python", $env:ProgramFiles, ${env:ProgramFiles(x86)}) {
      if ($root -and (Test-Path $root)) { $c += Get-ChildItem $root -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' } }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) { try { $c += (& py -3 -c "import sys;print(sys.executable)" 2>$null) } catch {} }
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) { $c += $p.Path }
    foreach ($e in $c) { if (Test-Py $e) { return $e } }
    return $null
  }
  $py = Find-Py
  if (-not $py) {
    $v = [Environment]::OSVersion.Version
    $ver = if ($v.Major -lt 6 -or ($v.Major -eq 6 -and $v.Minor -lt 3)) { '3.8.10' } else { '3.12.10' }  # Win7 只能装 3.8
    $x64 = ($env:PROCESSOR_ARCHITECTURE -eq 'AMD64') -or ($env:PROCESSOR_ARCHITEW6432 -eq 'AMD64')
    $file = "python-$ver$(if ($x64) { '-amd64' }).exe"
    $exe = Join-Path $env:TEMP $file
    $ok = $false
    $file38 = "python-3.8.10$(if ($x64) { '-amd64' }).exe"
    foreach ($m in "$Server/dist/python/$file", "$Server/dist/python/$file38", "https://registry.npmmirror.com/-/binary/python/$ver/$file", "https://mirrors.huaweicloud.com/python/$ver/$file", "https://www.python.org/ftp/python/$ver/$file") {
      $local = $m.StartsWith($Server)  # 先试局域网版服务器自带的安装包，没有就不提示
      try { if (-not $local) { Say "   下载 $m" }; $wc.DownloadFile($m, $exe); if ((Get-Item $exe).Length -gt 10MB) { $ok = $true; break } } catch { if (-not $local) { Say "   这个地址下载失败，换一个" DarkYellow } }
    }
    if (-not $ok) { Say '× Python 下载失败，请检查一体机能否上网' Red; Finish }
    Say "   正在安装 Python $ver（不会弹窗，请稍等）"
    Start-Process $exe -ArgumentList '/quiet InstallAllUsers=0 PrependPath=0 Include_tcltk=1 Include_test=0 Include_doc=0 Include_launcher=0 Shortcuts=0 AssociateFiles=0' -Wait
    Remove-Item $exe -ErrorAction SilentlyContinue
    $py = Find-Py
    if (-not $py) { Say '× Python 安装失败。可能被杀毒软件拦截了：请在杀毒软件里允许后再运行一次安装命令' Red; Finish }
  }
  $pyw = Join-Path (Split-Path $py) 'pythonw.exe'
  Say "   √ $py"

  Say '== 3/4 下载接收端'
  Get-WmiObject Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*.classcall*receiver.py*' } | ForEach-Object { $null = $_.Terminate() }
  New-Item -ItemType Directory -Force -Path $Dir | Out-Null
  $wc.DownloadFile("$Server/kylin/receiver.py", "$Dir\receiver.py.new")
  Move-Item "$Dir\receiver.py.new" "$Dir\receiver.py" -Force
  [IO.File]::WriteAllText("$Dir\config.txt", "server=$Server`r`nclass=$Class`r`nkey=$Key`r`n", (New-Object Text.UTF8Encoding $false))
  Say "   √ 已保存到 $Dir"

  Say '== 4/4 设置开机自启和桌面图标'
  $sh = New-Object -ComObject WScript.Shell
  function Lnk($path, $arg, $icon) {
    $l = $sh.CreateShortcut($path); $l.TargetPath = $pyw; $l.Arguments = $arg; $l.WorkingDirectory = $Dir
    if ($icon) { $l.IconLocation = $icon }
    $l.Save()
  }
  Lnk (Join-Path ([Environment]::GetFolderPath('Startup')) '班级助手接收端.lnk') "`"$Dir\receiver.py`"" $null
  Lnk (Join-Path ([Environment]::GetFolderPath('Desktop')) '班级看板.lnk') "`"$Dir\receiver.py`" --show-board" "$env:SystemRoot\System32\shell32.dll,15"
  Start-Process $pyw -ArgumentList "`"$Dir\receiver.py`"" -WorkingDirectory $Dir
  Start-Sleep 5
  $running = Get-WmiObject Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*.classcall*receiver.py*' }
  if ($running) {
    Say ''
    Say "√ 安装完成：$Class 接收端已启动，并已设置开机自动运行" Green
    Say '   屏幕右下角应该弹出"接收端已启动"。桌面上有「作业登记」「考勤登记」「班级看板」三个图标。'
    Say "   出问题时查看日志：$Dir\receiver.log"
  } else {
    Say '× 启动失败。把下面这个文件的内容拍照发给管理员：' Red
    Say "   $Dir\receiver.log"
    & $py "$Dir\receiver.py"
  }
} catch {
  Say "× 安装出错：$($_.Exception.Message)" Red
}
Finish

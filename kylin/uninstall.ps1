# 班级助手 - Windows 一体机卸载（关掉接收端、取消开机自启、删桌面图标和程序）
# 用法：按 Win+R，粘贴工作台「班级管理」里的 Windows 卸载命令，回车。
$Host.UI.RawUI.WindowTitle = '班级助手 - 卸载一体机接收端'
# 先关接收端，不然它会把桌面图标补回来
Get-WmiObject Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*.classcall*receiver.py*' } | ForEach-Object { $null = $_.Terminate() }
Start-Sleep 2
$desk = [Environment]::GetFolderPath('Desktop')
Remove-Item "$desk\作业登记.url", "$desk\考勤登记.url", "$desk\班级看板.lnk" -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) '班级助手接收端.lnk') -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:USERPROFILE '.classcall') -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path (Join-Path $env:USERPROFILE '.classcall')) {
  Write-Host '× 程序文件夹没删干净，请重启电脑后再运行一次卸载命令' -ForegroundColor Red
} else {
  Write-Host '√ 班级助手已卸载（接收端已关闭，开机不再启动，桌面图标已删除）' -ForegroundColor Green
  Write-Host '  安装时顺带装的 Python 没有删，不影响使用；想删可以在「设置 → 应用」里卸载。'
}
Write-Host ''
Read-Host '按回车关闭这个窗口' | Out-Null

#!/bin/bash
# 班级助手 - 麒麟/Linux 一体机卸载（关掉接收端、取消开机自启、删桌面图标和程序）
# 用法：wget -qO- http://服务器/kylin/uninstall.sh | bash
DIR="$HOME/.classcall"
pkill -f "$DIR/receiver.py" 2>/dev/null  # 先关接收端，不然它会把桌面图标补回来
sleep 1
rm -f "$HOME/.config/autostart/classcall.desktop"
crontab -l 2>/dev/null | grep -v "# classcall" | crontab - 2>/dev/null  # 开机备用入口
for d in "$HOME/桌面" "$HOME/Desktop"; do
  rm -f "$d/作业登记.desktop" "$d/考勤登记.desktop" "$d/班级看板.desktop"
done
rm -rf "$DIR"
echo "✅ 班级助手已卸载（接收端已关闭，开机不再启动，桌面图标已删除）"

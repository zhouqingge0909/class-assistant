#!/bin/bash
# 班级助手 - 麒麟/Linux 一体机安装（安装 + 立即启动 + 开机自启）
# 单班安装：wget -qO- http://服务器/kylin/install.sh | bash -s 班级 教室密钥
# 桌面云统一安装（按网卡 MAC 自动认班）：wget -qO- http://服务器/kylin/install.sh | bash -s auto
AUTO=0
if [ "$1" = "auto" ] || [ "$1" = "--auto" ]; then
  AUTO=1
  SERVER="${2:-__SERVER__}"
else
  CLASS="$1"
  KEY="$2"
  SERVER="${3:-__SERVER__}"
  if [ -z "$CLASS" ] || [ -z "$KEY" ]; then
    echo "单班用法：wget -qO- $SERVER/kylin/install.sh | bash -s 班级 教室密钥"
    echo "桌面云通用用法：wget -qO- $SERVER/kylin/install.sh | bash -s auto"
    exit 1
  fi
fi
DIR="$HOME/.classcall"
mkdir -p "$DIR" "$HOME/.config/autostart"

dl() { wget -qO "$2" "$1" 2>/dev/null || curl -fsSL -o "$2" "$1"; }
if ! dl "$SERVER/kylin/receiver.py" "$DIR/receiver.py"; then
  echo "❌ 下载失败，请检查一体机能否上网"; exit 1
fi

# 系统没装 tkinter、又不知道 sudo 密码时：下载 deb 解压到 ~/.classcall/tk，receiver.py 会自己带上路径启动
if ! python3 -c "import tkinter" 2>/dev/null && [ ! -d "$DIR/tk" ]; then
  echo "本机缺少 tkinter，正在免密码安装到 $DIR/tk ..."
  DEB="$DIR/tk-deb"; mkdir -p "$DEB"
  (cd "$DEB" && apt-get download python3-tk libtk8.6 libtcl8.6 tk8.6-blt2.5 >/dev/null 2>&1)
  ARCH=$(dpkg --print-architecture 2>/dev/null)
  if ! ls "$DEB"/*.deb >/dev/null 2>&1 && [ -n "$ARCH" ]; then  # 不能上网：从局域网版服务器下载
    for p in python3-tk libtk8.6 libtcl8.6 tk8.6-blt2.5; do
      dl "$SERVER/dist/tk/$ARCH/$p.deb" "$DEB/$p.deb" || rm -f "$DEB/$p.deb"
    done
  fi
  for f in "$DEB"/*.deb; do [ -f "$f" ] && dpkg -x "$f" "$DIR/tk"; done
  rm -rf "$DEB"
  if [ ! -d "$DIR/tk" ]; then
    echo "❌ 本机缺少 tkinter，且无法自动下载。请让有 sudo 密码的人运行：sudo apt-get install python3-tk"
    exit 1
  fi
fi

if [ "$AUTO" = "1" ]; then
cat > "$DIR/config.txt" <<EOF
server=$SERVER
mode=auto
EOF
else
cat > "$DIR/config.txt" <<EOF
server=$SERVER
class=$CLASS
key=$KEY
EOF
fi

cat > "$HOME/.config/autostart/classcall.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=班级助手接收端
Exec=python3 $DIR/receiver.py
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

pkill -f "$DIR/receiver.py" 2>/dev/null
sleep 1
nohup python3 "$DIR/receiver.py" >/dev/null 2>&1 &
sleep 2
if pgrep -f "$DIR/receiver.py" >/dev/null; then
  if [ "$AUTO" = "1" ]; then
    echo "✅ 安装完成（桌面云自动认班模式）：接收端已启动，并已设置开机自动运行"
    echo "   本机启动后会按网卡 MAC 地址自动关联班级；未绑定时屏幕会提示选择班级"
  else
    echo "✅ 安装完成：$CLASS 接收端已启动，并已设置开机自动运行"
  fi
  echo "   出问题时查看日志：cat $DIR/receiver.log"
else
  echo "❌ 启动失败，运行下面命令查看原因："
  echo "   python3 $DIR/receiver.py"
fi

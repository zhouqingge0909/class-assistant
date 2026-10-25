#!/bin/bash
# 班级助手 - 在你自己的云服务器（Ubuntu）上一键安装 / 更新
# 用法一（从 GitHub 下载的代码里直接装）：sudo bash setup.sh
# 用法二（从已经装好的服务器上下载安装包）：curl -fsSL http://那台服务器的IP/dist/setup.sh | sudo SRC=http://那台服务器的IP bash
# 再运行一次就是更新到最新版，班级数据和密码都会保留。
set -e
SRC="${SRC:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
DIR=/opt/class-call

[ "$(id -u)" = 0 ] || { echo "❌ 请在命令前面加 sudo"; exit 1; }
command -v apt-get >/dev/null || { echo "❌ 只支持 Ubuntu 系统，请把服务器重装成 Ubuntu 22.04 或 24.04"; exit 1; }
if ss -ltnp 2>/dev/null | grep -q ':80 ' && ! systemctl is-active -q class-call; then
  echo "❌ 80 端口已被别的程序占用（可能装了宝塔面板等）。请把服务器重装成纯净的 Ubuntu 系统镜像"; exit 1
fi

echo "== 1/4 下载班级助手"
mkdir -p "$DIR"
if [ -f "$HERE/server.py" ] && [ -d "$HERE/kylin" ]; then
  tar -C "$HERE" -cf - server.py fonts.sh kylin static | tar -C "$DIR" -xf -   # 用本地代码
  mkdir -p "$DIR/dist" && cp "$HERE/setup.sh" "$DIR/dist/setup.sh"
else
  [ -n "$SRC" ] || { echo "❌ 请在下载的代码文件夹里运行：sudo bash setup.sh"; exit 1; }
  TMP=$(mktemp)
  curl -fsSL -m 120 -o "$TMP" "$SRC/dist/class-assistant.tar.gz" || { echo "❌ 下载失败，请稍后再试"; exit 1; }
  tar --warning=no-unknown-keyword -C "$DIR" -xzf "$TMP"
  rm -f "$TMP"
fi

echo "== 2/4 安装运行环境（第一次大约 2 分钟）"
if [ ! -x "$DIR/venv/bin/python" ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv >/dev/null
  python3 -m venv "$DIR/venv"
fi
"$DIR/venv/bin/pip" install -q -U edge-tts pillow qrcode -i https://mirrors.cloud.tencent.com/pypi/simple \
  || "$DIR/venv/bin/pip" install -q -U edge-tts pillow qrcode

echo "== 3/4 下载字体（第一次大约 3 分钟）"
bash "$DIR/fonts.sh" || echo "   ⚠️  部分字体没下载成功，不影响使用，以后再运行一次安装命令会补上"

echo "== 4/4 启动"
id classcall >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin classcall
mkdir -p "$DIR/data" "$DIR/backups"
chown -R classcall:classcall "$DIR"
cat > /etc/systemd/system/class-call.service <<'UNIT'
[Unit]
Description=Class Call server
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/class-call
ExecStart=/opt/class-call/venv/bin/python server.py
Environment=PORT=80 PYTHONUNBUFFERED=1
User=classcall
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
LimitNOFILE=65536
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 80/tcp >/dev/null; fi
systemctl daemon-reload
systemctl enable class-call >/dev/null 2>&1
systemctl restart class-call
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null http://127.0.0.1/ && break
  sleep 1
done
curl -s -o /dev/null http://127.0.0.1/ || { echo "❌ 启动失败，把下面的内容拍照发给作者："; journalctl -u class-call -n 20 --no-pager; exit 1; }

IP=$(curl -s -m 5 "$SRC/dist/myip" || true)
PW=$("$DIR/venv/bin/python" -c "import json;print(json.load(open('$DIR/config.json'))['teacher_password'])")
echo
echo "=============================================="
echo " ✅ 班级助手已经装好，并会开机自动运行"
echo
echo "   工作台地址：http://${IP:-你的服务器公网IP}"
echo "   管理员密码：$PW"
echo
echo " 请把这两行抄下来或截图保存。"
echo " 忘了密码、或想更新到最新版，就再运行一次安装命令。"
echo "=============================================="

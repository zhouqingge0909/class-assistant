#!/bin/bash
# 把喊话系统部署/更新到云服务器（Ubuntu）
# 用法：./deploy.sh 服务器IP [登录用户名]
# 在 Mac 上改了 students.csv、config.json 或代码后，再运行一次即可更新。
set -e
IP="$1"
LOGIN="$2"
KEY="$HOME/.ssh/class_call_ed25519"
DIR="$(cd "$(dirname "$0")" && pwd)"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

[ -z "$IP" ] && { echo "用法：./deploy.sh 服务器IP"; exit 1; }

if [ -z "$LOGIN" ]; then
  for u in ubuntu root; do
    if "${SSH[@]}" -o BatchMode=yes "$u@$IP" true 2>/dev/null; then LOGIN=$u; break; fi
  done
fi
[ -z "$LOGIN" ] && { echo "连不上服务器：请确认 IP 正确、公钥已绑定到这台服务器。"; exit 1; }
HOST="$LOGIN@$IP"
echo "== 已连接 $HOST"

echo "== 1/3 上传程序和配置"
COPYFILE_DISABLE=1 tar -C "$DIR" --exclude ./data --exclude ./windows --exclude ./声音试听 \
  --exclude ./deploy.sh --exclude ./fonts --exclude ./backups --exclude ./pull_backups.sh --exclude './*.md' --exclude '.DS_Store' -czf - . |
  "${SSH[@]}" "$HOST" 'sudo mkdir -p /opt/class-call && sudo tar --warning=no-unknown-keyword -C /opt/class-call -xzf -'

echo "== 2/3 安装运行环境（第一次需要一两分钟）"
"${SSH[@]}" "$HOST" 'bash -s' <<'REMOTE'
set -e
cd /opt/class-call
if [ ! -x venv/bin/python ]; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv >/dev/null
  sudo python3 -m venv venv
fi
sudo venv/bin/pip install -q -U edge-tts pillow qrcode -i https://mirrors.cloud.tencent.com/pypi/simple \
  || sudo venv/bin/pip install -q -U edge-tts pillow qrcode
id classcall >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin classcall
sudo mkdir -p data backups
sudo chown -R classcall:classcall /opt/class-call
sudo tee /etc/systemd/system/class-call.service >/dev/null <<'UNIT'
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
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
if command -v ufw >/dev/null && sudo ufw status | grep -q active; then sudo ufw allow 80/tcp >/dev/null; fi
sudo bash /opt/class-call/fonts.sh
sudo chown -R classcall:classcall /opt/class-call/fonts
sudo systemctl daemon-reload
sudo systemctl enable class-call >/dev/null 2>&1
sudo systemctl restart class-call
sleep 2
curl -s -o /dev/null -w "   服务器本机自检：HTTP %{http_code}\n" http://127.0.0.1/
sudo journalctl -u class-call -n 20 --no-pager | grep -E "语音|启动" || true
REMOTE

echo "== 3/3 从你的 Mac 检查能否访问"
if curl -s -o /dev/null -m 8 -w "%{http_code}" "http://$IP/" | grep -q 200; then
  echo "   ✅ 外网可以访问"
else
  echo "   ⚠️  外网访问不通：请到云服务器控制台的「防火墙」里放行 TCP 80 端口"
fi

echo
echo "老师发送页面：http://$IP/"

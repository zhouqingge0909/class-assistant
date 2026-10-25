#!/bin/bash
# 把班级助手部署/更新到「网友公共服务器」（Ubuntu，root 登录）
# 用法：./deploy-public.sh 服务器IP
# 和 deploy.sh 不同：绝不上传本校的 config.json（管理员密码、教室密钥）和 students.csv（真实名单）；
# 远端第一次启动时自己生成新的管理员密码，并打开公共模式（"public": true）。
set -e
IP="$1"
KEY="$HOME/.ssh/class_call_ed25519"
DIR="$(cd "$(dirname "$0")" && pwd)"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
HOST="root@$IP"

[ -z "$IP" ] && { echo "用法：./deploy-public.sh 服务器IP"; exit 1; }
"${SSH[@]}" -o BatchMode=yes "$HOST" true 2>/dev/null || { echo "连不上服务器：请确认 IP 正确、公钥已绑定到这台服务器（root 登录）。"; exit 1; }
echo "== 已连接 $HOST"

echo "== 1/3 打包并检查（不含本校配置和名单）"
PKG="$(mktemp -t class-call-public).tgz"
trap 'rm -f "$PKG"' EXIT
COPYFILE_DISABLE=1 tar -C "$DIR" --exclude './config.json*' --exclude './tts.json*' --exclude './students.csv*' --exclude '*.bak*' --exclude ./data --exclude ./lan --exclude ./dist \
  --exclude ./windows --exclude ./声音试听 --exclude '*.md' --exclude '*.MD' --exclude '.claude' --exclude ./fonts --exclude ./backups \
  --exclude './deploy*.sh' --exclude ./pack.sh --exclude ./kick-admins.sh --exclude ./set-tts-key.sh --exclude ./pull_backups.sh --exclude '.DS_Store' -czf "$PKG" .
# 连 config.json.bak 这类备份、任何一层的 CLAUDE.md / *.md 说明文件和 .claude 设置也拦下
if tar -tzf "$PKG" | grep -qiE '(^|/)(config\.json|tts\.json|students\.csv)|\.bak|(^|/)data/|\.md$|(^|/)\.claude(/|$)'; then
  echo "   ❌ 包里有 config.json / students.csv / 备份文件 / data / 说明文档(.md)，已停止，什么都没上传"; exit 1
fi
echo "   ✅ 包里没有本校的密码和名单（$(tar -tzf "$PKG" | wc -l | tr -d ' ') 个文件）"
"${SSH[@]}" "$HOST" 'mkdir -p /opt/class-call && tar --warning=no-unknown-keyword -C /opt/class-call -xzf -' < "$PKG"

echo "== 2/3 安装运行环境（第一次需要一两分钟）"
"${SSH[@]}" "$HOST" 'bash -s' <<'REMOTE'
set -e
cd /opt/class-call
# 公共模式：没有配置就写一个只有 public 的，让 server.py 自己生成管理员密码；已有配置不覆盖，只确保 public=true
if [ ! -f config.json ]; then
  echo '{"public": true}' > config.json
else
  python3 - <<'PY'
import json
p = "/opt/class-call/config.json"
cfg = json.load(open(p, encoding="utf-8"))
if cfg.get("public") is not True:
    cfg["public"] = True
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
fi
if [ ! -x venv/bin/python ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv >/dev/null
  python3 -m venv venv
fi
venv/bin/pip install -q -U edge-tts pillow qrcode -i https://mirrors.cloud.tencent.com/pypi/simple \
  || venv/bin/pip install -q -U edge-tts pillow qrcode
id classcall >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin classcall
mkdir -p data backups
chown -R classcall:classcall /opt/class-call
chmod 600 config.json
tee /etc/systemd/system/class-call.service >/dev/null <<'UNIT'
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
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT
if command -v ufw >/dev/null && ufw status | grep -q active; then ufw allow 80/tcp >/dev/null; fi
bash /opt/class-call/fonts.sh
chown -R classcall:classcall /opt/class-call/fonts
systemctl daemon-reload
systemctl enable class-call >/dev/null 2>&1
systemctl restart class-call
sleep 2
curl -s -o /dev/null -w "   服务器本机自检：HTTP %{http_code}\n" http://127.0.0.1/
curl -s -o /dev/null -w "   注册页自检：HTTP %{http_code}\n" http://127.0.0.1/register
journalctl -u class-call -n 20 --no-pager | grep -E "语音|启动" || true
REMOTE

echo "== 3/3 从你的 Mac 检查能否访问"
if curl -s -o /dev/null -m 8 -w "%{http_code}" "http://$IP/" | grep -q 200; then
  echo "   ✅ 外网可以访问"
else
  echo "   ⚠️  外网访问不通：请到云服务器控制台的「防火墙」里放行 TCP 80 端口"
fi

PW=$("${SSH[@]}" "$HOST" "python3 -c 'import json;print(json.load(open(\"/opt/class-call/config.json\"))[\"teacher_password\"])'")
echo
echo "工作台：      http://$IP/"
echo "网友注册页：  http://$IP/register"
echo "管理员密码：  $PW   （只显示在这里，不要发给别人）"
echo "下一步：用管理员密码登录工作台 →「记录」栏目 →「网友注册」卡片里设置注册码"

#!/bin/bash
# 打包给其他学校自己搭服务器用的安装包：只有程序，不含名单、数据、密码
# 用法：./pack.sh   然后 ./deploy.sh 你的服务器IP ubuntu 传到服务器上
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
COPYFILE_DISABLE=1 tar -C "$DIR" --exclude .DS_Store -czf "$DIR/dist/class-assistant.tar.gz" server.py fonts.sh kylin static
echo "已打包：dist/class-assistant.tar.gz（$(du -h "$DIR/dist/class-assistant.tar.gz" | cut -f1)）"
tar -tzf "$DIR/dist/class-assistant.tar.gz" | grep -E "config\.json|students|data/" && { echo "❌ 包里混进了数据文件"; exit 1; } || true

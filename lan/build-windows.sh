#!/bin/bash
# 生成局域网版 Windows 压缩包：自带 Python（免安装）、不含任何数据和密码
# 用法：bash lan/build-windows.sh   →  lan/out/class-assistant-lan-windows.zip
# 需要：curl、python3（能用 pip）。下载的东西缓存在 lan/.cache，第二次打包不用重新下载。
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAN="$ROOT/lan"
CACHE="$LAN/.cache"
PYVER=3.8.10                      # 3.8 是最后一个支持 Win7 的版本
MIRROR="https://registry.npmmirror.com/-/binary/python/$PYVER"
PIP_INDEX="${PIP_INDEX:-https://mirrors.aliyun.com/pypi/simple}"
mkdir -p "$CACHE/wheels" "$LAN/out"

echo "== 1/4 下载 Python ${PYVER}（免安装版 + 给 Windows 一体机用的安装包）"
for f in "python-$PYVER-embed-amd64.zip" "python-$PYVER-amd64.exe" "python-$PYVER.exe"; do
  [ -s "$CACHE/$f" ] || curl -fL -m 900 -o "$CACHE/$f" "$MIRROR/$f"
done
# python.org 公布的 MD5，防止镜像出错或被篡改
md5of() { if command -v md5 >/dev/null; then md5 -q "$1"; else md5sum "$1" | cut -d' ' -f1; fi; }
while read -r sum f; do
  [ "$(md5of "$CACHE/$f")" = "$sum" ] || { echo "❌ $f 校验不对，删掉 lan/.cache 里的它再试"; exit 1; }
done <<EOF
3acb1d7d9bde5a79f840167b166bb633 python-$PYVER-embed-amd64.zip
62cf1a12a5276b0259e8761d4cf4fe42 python-$PYVER-amd64.exe
b355cfc84b681ace8908ae50908e8761 python-$PYVER.exe
EOF

echo "== 2/4 下载依赖（pillow 处理图片、qrcode 生成二维码）"
python3 -m pip download -q -d "$CACHE/wheels" --platform win_amd64 --python-version 38 --implementation cp --abi cp38 \
  --only-binary=:all: --index-url "$PIP_INDEX" "pillow==10.4.0" "qrcode==7.4.2" "colorama" 2>&1 | grep -v "pip version\|upgrade pip" || true

echo "== 3/4 组装"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
PKG="$OUT/班级助手局域网版"
APP="$PKG/app"
mkdir -p "$APP/python/Lib/site-packages" "$APP/dist/python"
cp "$ROOT/server.py" "$APP/"
cp -R "$ROOT/static" "$ROOT/kylin" "$APP/"
unzip -q "$CACHE/python-$PYVER-embed-amd64.zip" -d "$APP/python"
printf 'python38.zip\r\n.\r\nLib\\site-packages\r\nimport site\r\n' > "$APP/python/python38._pth"
for w in "$CACHE"/wheels/*.whl; do unzip -q -o "$w" -d "$APP/python/Lib/site-packages"; done
cp "$CACHE/python-$PYVER-amd64.exe" "$CACHE/python-$PYVER.exe" "$APP/dist/python/"
find "$PKG" \( -name .DS_Store -o -name '*.bak*' -o -name __pycache__ \) -prune -exec rm -rf {} +

# .bat 转成 GBK + CRLF（中文 Windows 的命令行只认这个）；说明转成带 BOM 的 UTF-8 + CRLF（记事本都能正常显示）
python3 - "$LAN" "$PKG" <<'PY'
import os, sys
lan, pkg = sys.argv[1:]
for name in os.listdir(lan):
    src = os.path.join(lan, name)
    if name.endswith(".bat"):
        text = open(src, encoding="utf-8").read().replace("\r\n", "\n").replace("\n", "\r\n")
        open(os.path.join(pkg, name), "wb").write(text.encode("gbk"))
    elif name.endswith(".txt"):
        text = open(src, encoding="utf-8").read().replace("\r\n", "\n").replace("\n", "\r\n")
        open(os.path.join(pkg, name), "wb").write(text.encode("utf-8-sig"))
PY

# 自检：绝不能带上数据和密码
if find "$PKG" \( -name config.json -o -name 'students.csv*' -o -name tts.json -o -path '*/app/data' -o -name CLAUDE.md -o -name '*.md' \) | grep .; then
  echo "❌ 包里混进了数据或说明文件，已停止"; exit 1
fi

echo "== 4/4 打包"
ZIP="$LAN/out/class-assistant-lan-windows.zip"
rm -f "$ZIP"
# 文件名用 GBK 存（不标 UTF-8）：中文 Windows 自带的解压（包括 Win7）、360压缩、7-Zip 都不会乱码
python3 - "$OUT" "$ZIP" <<'PY'
import os, sys, zipfile
src, dst = sys.argv[1:]
class GbkInfo(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):
        return self.filename.encode("gbk"), self.flag_bits & ~0x800
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            info = GbkInfo.from_file(p, os.path.relpath(p, src))
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as fh:
                z.writestr(info, fh.read(), compresslevel=9)
PY
echo "已生成：${ZIP}（$(du -h "$ZIP" | cut -f1)）"

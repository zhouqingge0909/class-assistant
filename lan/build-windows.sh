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

echo "== 1/5 下载 Python ${PYVER}（免安装版 + 给 Windows 一体机用的安装包）"
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

echo "== 2/5 下载依赖（pillow 处理图片、qrcode 生成二维码）"
python3 -m pip download -q -d "$CACHE/wheels" --platform win_amd64 --python-version 38 --implementation cp --abi cp38 \
  --only-binary=:all: --index-url "$PIP_INDEX" "pillow==10.4.0" "qrcode==7.4.2" "colorama" 2>&1 | grep -v "pip version\|upgrade pip" || true

echo "== 3/5 下载麒麟一体机缺 tkinter 时用的包（Ubuntu 20.04，麒麟 V10 就是基于它的）"
U="https://mirrors.aliyun.com/ubuntu"
while read -r arch pkg path sum; do
  f="$CACHE/tk-$arch/$pkg.deb"
  mkdir -p "$CACHE/tk-$arch"
  base="$U"; [ "$arch" = arm64 ] && base="$U-ports"
  [ -s "$f" ] || curl -fL -m 300 -o "$f" "$base/$path"
  got=$(if command -v shasum >/dev/null; then shasum -a 256 "$f"; else sha256sum "$f"; fi | cut -d' ' -f1)
  [ "$got" = "$sum" ] || { echo "❌ $arch/$pkg 校验不对，删掉 lan/.cache/tk-$arch 再试"; exit 1; }
done <<DEBS
amd64 python3-tk pool/main/p/python3-stdlib-extensions/python3-tk_3.8.10-0ubuntu1~20.04_amd64.deb 361757e84f4abeb50f5aa0a2856e8bcc749d8e2e353ec6a9760288d1f4ba3c51
amd64 libtk8.6 pool/main/t/tk8.6/libtk8.6_8.6.10-1_amd64.deb 086c346e6c2b55798e40ebb46519bfdf1de98a651c8730802769445735d66c9a
amd64 libtcl8.6 pool/main/t/tcl8.6/libtcl8.6_8.6.10+dfsg-1_amd64.deb 390780dbbccaad89676577ebe681e588ee170637a6f272e6f74fcb041f90785f
amd64 tk8.6-blt2.5 pool/main/b/blt/tk8.6-blt2.5_2.5.3+dfsg-4_amd64.deb e27ce27e78cb2e76c4624a3ca2591be613f875c1c53d00ed66a96de6e1a7bcb1
arm64 python3-tk pool/main/p/python3-stdlib-extensions/python3-tk_3.8.10-0ubuntu1~20.04_arm64.deb c15160d124d61f5fa4bb481b202a699e4e47e9544aa398a17369dc311343710c
arm64 libtk8.6 pool/main/t/tk8.6/libtk8.6_8.6.10-1_arm64.deb be1ccd1a5f03d9e8ab8217ea9f80f12a4900eec30a09c6567320d8a23a33d95e
arm64 libtcl8.6 pool/main/t/tcl8.6/libtcl8.6_8.6.10+dfsg-1_arm64.deb b06966ac7ff8e2f4d3e54c1b7f4c43f6f3042f54765664e728f8ee4cad2b53d2
arm64 tk8.6-blt2.5 pool/main/b/blt/tk8.6-blt2.5_2.5.3+dfsg-4_arm64.deb 0b05b822a4d80c7e1379dfb0e0d1b004d1215e090a48963bf5249906bbb44e50
DEBS

echo "== 4/5 组装"
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
mkdir -p "$APP/dist/tk"
cp -R "$CACHE/tk-amd64" "$APP/dist/tk/amd64"
cp -R "$CACHE/tk-arm64" "$APP/dist/tk/arm64"
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

echo "== 5/5 打包"
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

#!/bin/bash
# 在云服务器上下载工作台可选的字体（只下载用到的那一款字重，已下载的会跳过）
# 字体都是按"分片"切好的网页字体，浏览器只加载页面上用到的字，所以打开很快。
set -e
DIR="$(cd "$(dirname "$0")" && pwd)/fonts"
MIRROR="https://registry.npmmirror.com"

# 名称|包地址|CSS 文件
FONTS="
misans|misans/4.0.0/files/lib/Normal|MiSans-Regular.min.css MiSans-Bold.min.css
manhei|@chinese-fonts/lxgwmanhei/3.0.0/files/dist/LXGWMarkerGothic|result.css
xiaolai|@chinese-fonts/xiaolai/3.0.0/files/dist/Xiaolai|result.css
wenkai|@chinese-fonts/lxgwwenkai/3.0.0/files/dist/LXGWWenKai-Medium|result.css
smiley|@chinese-fonts/dyh/3.0.0/files/dist/SmileySans-Oblique|result.css
"

echo "$FONTS" | while IFS='|' read -r id path cssfiles; do
  [ -z "$id" ] && continue
  mkdir -p "$DIR/$id"
  for css in $cssfiles; do
    [ -s "$DIR/$id/$css" ] || curl -sf -m 60 -o "$DIR/$id/$css" "$MIRROR/$path/$css"
    grep -oE "url\(['\"]?[^'\")]+\.woff2" "$DIR/$id/$css" | sed -E "s/url\(['\"]?(\.\/)?//" | sort -u |
      while read -r f; do [ -s "$DIR/$id/$f" ] || echo "$f"; done |
      xargs -r -P 8 -I{} curl -sf -m 60 -o "$DIR/$id/{}" "$MIRROR/$path/{}"
  done
  echo "   字体 $id：$(ls "$DIR/$id" | grep -c woff2) 个分片，$(du -sh "$DIR/$id" | cut -f1)"
done

# 一体机弹窗用的完整字体文件（tkinter 不能用网页分片字体）
J="https://cdn.jsdelivr.net/npm"
mkdir -p "$DIR/ttf"
rm -f "$DIR/ttf/LXGWMarkerGothic.ttf"
for item in "MiSans-Bold.otf|$J/@fontpkg/mi-sans@4.3.0/MiSans-Bold.otf" \
            "MiSans-Regular.otf|$J/@fontpkg/mi-sans@4.3.0/MiSans-Regular.otf" \
            "LXGWWenKaiScreen.ttf|$J/lxgw-wenkai-subset@1.235.2/ttf/lxgwwenkaiscreen.original.v1.235.ttf"; do
  name="${item%%|*}"; url="${item#*|}"
  [ -s "$DIR/ttf/$name" ] || curl -sf -m 600 -o "$DIR/ttf/$name" "$url"
done
echo "   一体机字体：$(ls "$DIR/ttf" | tr '\n' ' ')"

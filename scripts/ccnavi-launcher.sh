#!/bin/sh
# ccnavi — 配布先で hook が起動する 1 本。隣に並ぶ機械ごとの組み立てから、この機械で
# 動くものを選んで起動する。scripts/ccnavi-setup.sh が CCNAVI_BIN_PATH の指す場所
# （既定は .ccnavi/bin/ccnavi）へ配る。
#
#   .ccnavi/bin/ccnavi                  ← これ
#   .ccnavi/bin/darwin-arm64/ccnavi
#   .ccnavi/bin/linux-x86_64/ccnavi
#   .ccnavi/bin/windows-x86_64/ccnavi.exe
#
# hook の command は 1 行しか書けず、settings.json は Windows・WSL・Linux・macOS で
# 同じものを開く。PyInstaller の実行ファイルは組み立てた機械でしか動かないので、
# どれを起動するかは起動の瞬間に、起動した機械が決めるしかない。
#
# 代償は、ツール呼び出しのたびに sh の起動と uname 1 回ぶんが乗ること。uname は
# 1 回で OS と CPU の両方を取る。
#
# 語は ccnavi/platformtag.py と scripts/ccnavi-setup.sh の host_target と揃える。
#
# 見つからなければ 127 で終わる。実行ファイルそのものが無いときにシェルが返すのと
# 同じ値で、hook からは「起動できなかった」として扱われる。

here=${0%/*}
[ "$here" != "$0" ] || here=.

found=$(uname -sm 2>/dev/null) || found=""
os=${found% *}
arch=${found##* }

case "$os" in
Linux*) os=linux ;;
Darwin*) os=darwin ;;
MINGW* | MSYS* | CYGWIN*) os=windows ;;
*) os=unknown ;;
esac
case "$arch" in
x86_64 | amd64 | AMD64) arch=x86_64 ;;
arm64 | aarch64 | ARM64) arch=arm64 ;;
*) arch=unknown ;;
esac

# arm64 の macOS と Windows は x86_64 の実行ファイルを変換して動かす（Rosetta 2 /
# Windows on Arm）。自分向けが無いときだけそちらへ回る。
targets="$os-$arch"
case "$targets" in
darwin-arm64) targets="$targets darwin-x86_64" ;;
windows-arm64) targets="$targets windows-x86_64" ;;
esac

for target in $targets; do
	for name in ccnavi ccnavi.exe; do
		if [ -f "$here/$target/$name" ]; then
			exec "$here/$target/$name" "$@"
		fi
	done
done

printf 'ccnavi: この機械（%s-%s）で動く実行ファイルが %s/ にありません。この機械で build.py を回し、scripts/ccnavi-setup.sh で配ってください。\n' "$os" "$arch" "$here" >&2
exit 127

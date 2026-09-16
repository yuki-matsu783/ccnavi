#!/bin/sh
# ccnavi-launcher — hook が起動する 1 本。CCNAVI_BIN_PATH はここを指す。1 つ上の bin/ に
# 並ぶ機械ごとの組み立てから、この機械で動くものを選んで起動する。
#
#   .ccnavi/scripts/ccnavi-launcher.sh      ← これ
#   .ccnavi/bin/darwin-arm64/ccnavi
#   .ccnavi/bin/linux-x86_64/ccnavi
#   .ccnavi/bin/windows-x86_64/ccnavi.exe
#
# ccnavi のリポジトリでも配布先でも同じ綴りで置く。ccnavi のリポジトリでは build.py が、
# 配布先では scripts/ccnavi-setup.sh が、実行ファイルを .ccnavi/bin/<os>-<arch>/ に置く。
#
# hook の command は 1 行しか書けず、settings.json は Windows・WSL・Linux・macOS で
# 同じものを開く。PyInstaller の実行ファイルは組み立てた機械でしか動かないので、
# どれを起動するかは起動の瞬間に、起動した機械が決めるしかない。
#
# 代償は、ツール呼び出しのたびに sh の起動と uname 1 回ぶんが乗ること。uname は
# 1 回で OS と CPU の両方を取る。
#
# bin_dir は `..` を含むまま使い、正規化しない。`${here%/*}` で切る形は here が `.` や
# 1 段の名前のときに壊れる。シンボリックリンクは解かない。
#
# 語は ccnavi/platformtag.py と scripts/ccnavi-setup.sh の host_target と揃える。
#
# 見つからなければ 127 で終わる。実行ファイルそのものが無いときにシェルが返すのと
# 同じ値で、hook からは「起動できなかった」として扱われる。

here=${0%/*}
[ "$here" != "$0" ] || here=.
bin_dir=$here/../bin

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
		if [ -f "$bin_dir/$target/$name" ]; then
			exec "$bin_dir/$target/$name" "$@"
		fi
	done
done

printf 'ccnavi: この機械（%s-%s）で動く実行ファイルが %s/ にありません。ccnavi のリポジトリでは build.py を回すと置かれます。配布先では、この機械で組み立てたものを scripts/ccnavi-setup.sh で配ってください。\n' "$os" "$arch" "$bin_dir" >&2
exit 127

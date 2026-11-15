#!/bin/sh
# ccnavi-dirs セッションの頭で、projects/ に clone 済みの外リポジトリを新しい順に最大 10 件、文脈へ入れる。
#
#   sh .ccnavi/scripts/ccnavi-dirs.sh
#
# 数えるのは projects/ 直下で `.git`（ディレクトリでもファイルでも）を持つもの。.git の無いディレクトリは
# 数えない。ワークスペースルートで起動する前提（CLAUDE_PROJECT_DIR の projects/ を見る）。
# 並びは最新コミットの時刻が新しい順。コミットが無い・読めないものは末尾に回す。
# 出力はモデルに届くので、プロジェクト名だけを出す。11 件以上あるときは、残りの件数だけ添える。該当が無ければ何も出さない。
#
# 本体は実行ファイルの `ccnavi dirs`（go/internal/entry/dirs）。ここは起動役（実行ファイルを探して渡すだけ）。
# 実行ファイルが無ければ、何も出さずに標準エラーへ理由を言って 1 で終わる（入口の ccnavi-session-start.sh は続ける）。

set -eu

# 共通部分。実行ファイルの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

root="${CLAUDE_PROJECT_DIR:-.}"
[ -d "$root/projects" ] || exit 0
bin=$(ccnavi_bin "$root") || {
	printf 'ccnavi-dirs: ccnavi の実行ファイルが見つかりません。実行ファイルを組む（sh scripts/ccnavi-build.sh）か置いてください。\n' >&2
	exit 1
}
exec "$bin" --root "$root" dirs

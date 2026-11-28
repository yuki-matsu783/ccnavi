#!/bin/sh
# ccnavi-user-prune 判定ログを整理して、動かしたものを出す。ユーザが端末で打つ。
#
#   sh .ccnavi/scripts/ccnavi-user-prune.sh [--preview]
#
# ユーザだけが打つ sh は ccnavi-user-*.sh に分けてあり、エージェントからは hook が名前で止める。
# 中身は実行ファイルの `user prune` で、引数はそのまま渡す。ユーザが打つ入口を sh にそろえるための
# 薄い包みで、実行ファイルに入口の印（`--entry sh`）を付けて起こす。印が無い `user` の入口は、
# 実行ファイルが直に打った形として断る。
# `--preview` は動かすものを並べるだけ。
#
# 終了コード: 実行ファイルの `user prune` のもの。2 は引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方と実行ファイルの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-prune.sh [--preview]

  判定ログを整理して、動かしたものを出す。
  実行ファイルの `user prune` を、入口の印を付けて起こす。ユーザが端末で実行します。
USAGE
}

if [ $# -eq 1 ]; then
	case "$1" in
	-h | --help | help)
		usage
		exit 0
		;;
	esac
fi

# ワークスペースの外で開いた端末からも動くように、cwd から見つからなければ、この sh のある場所からルートを決める。
root=$(ccnavi_workspace_anywhere) || ccnavi_no_workspace ccnavi-user-prune

# 実行ファイルは、打った人の cwd のまま起こす（取り込み先のツリーと根は、実行ファイルが cwd から決める）。
if bin=$(ccnavi_bin "$root"); then
	exec "$bin" --entry sh user prune "$@"
fi
ccnavi_no_bin ccnavi-user-prune

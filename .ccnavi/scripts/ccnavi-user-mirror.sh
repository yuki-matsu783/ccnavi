#!/bin/sh
# ccnavi-user-mirror 共通設定のミラーと工程スキル（skills/）がずれていないかを見て、プロジェクトへ揃える。ユーザが端末で打つ。
#
#   sh .ccnavi/scripts/ccnavi-user-mirror.sh [<プロジェクト名>…] [--preview | --yes --expect <値>]
#
# ユーザだけが打つ sh は ccnavi-user-*.sh に分けてあり、エージェントからは hook が名前で止める。
# 中身は実行ファイルの `user mirror` で、引数はそのまま渡す。ユーザが打つ入口を sh にそろえるための
# 薄い包みで、実行ファイルに入口の印（`--entry sh`）を付けて起こす。印が無い `user` の入口は、
# 実行ファイルが直に打った形として断る。
# 引数なしはずれを見せて y/N を聞く（実行ファイルが聞く）。`--preview` はずれを見るだけ。
# `--yes --expect <値>` は y/N を省く形（ボードが送る形。ターミナルでも、見せた値と同じなら使える）。
#
# 終了コード: 実行ファイルの `user mirror` のもの。2 は引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方と実行ファイルの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
使い方: sh .ccnavi/scripts/ccnavi-user-mirror.sh [<プロジェクト名>…] [--preview | --yes --expect <値>]

  共通設定のミラーと工程スキル（skills/）がずれていないかを見て、プロジェクトへ揃える。ずれを見せたあと、実行ファイルが y/N を聞く。
  --preview はずれを見るだけで、何も書かない。--yes --expect は y/N を省く形（見せた値と合うときだけ揃える）。
  実行ファイルの `user mirror` を、入口の印を付けて起こす。ユーザが端末で実行します。全体の一覧は `ccnavi user`。

例: sh .ccnavi/scripts/ccnavi-user-mirror.sh --preview
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
root=$(ccnavi_workspace_anywhere) || ccnavi_no_workspace ccnavi-user-mirror

# 実行ファイルは、打った人の cwd のまま起こす（取り込み先のツリーと根は、実行ファイルが cwd から決める）。
if bin=$(ccnavi_bin "$root"); then
	exec "$bin" --entry sh user mirror "$@"
fi
ccnavi_no_bin ccnavi-user-mirror

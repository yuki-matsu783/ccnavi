#!/bin/sh
# ccnavi-session-start セッション開始時に走らせる sh を 1 本にまとめた入口。SessionStart にはこれを 1 行で登録する。
#
#   sh .ccnavi/scripts/ccnavi-session-start.sh
#
# 順に呼ぶ。
#   1. ccnavi-fetch.sh  リモートの最新に合わせる（動いたものだけ出力する）
#   2. ccnavi-dirs.sh   プロジェクト直下のディレクトリ一覧を出す
#
# どちらかが失敗しても、残りは走らせる。セッションの頭で止めても得るものが無い。常に 0 を返す。

dir=$(dirname "$0")

sh "$dir/ccnavi-fetch.sh" || true
sh "$dir/ccnavi-dirs.sh" || true
exit 0

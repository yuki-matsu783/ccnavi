#!/bin/sh
# ccnavi-idle 放置したセッションに、次のセッションへの引き継ぎ文書を書かせる待機を受け持つ。
#
#   sh .ccnavi/scripts/ccnavi-idle.sh start     Stop に登録する（asyncRewake: true、timeout: 3600）
#   sh .ccnavi/scripts/ccnavi-idle.sh notice    Stop に登録する。ユーザへの知らせ（1 セッション 1 回）
#   sh .ccnavi/scripts/ccnavi-idle.sh reset     UserPromptSubmit に登録する。ユーザが打ったら待機をやめる
#
# 会話が大きく育ったまま放置されると、モデルが覚えている会話のコピー（プロンプトキャッシュ）が
# 時間切れで消え、続きを送り直す料金が大きく増える。消える前に引き継ぎ文書を書かせておくための待機で、
# 判断は実行ファイル（`ccnavi idle start|check|notice|reset`）に任せ、ここは待つだけ。
# `notice` と `reset` は、payload を実行ファイルに渡すだけ（待たない）。
#
# 流れ:
#   1. Stop の payload（標準入力）を `ccnavi idle start` に渡す。促す対象でなければ `skip` が返り、何もせず 0 で終わる。
#   2. 対象なら `wait <秒>`・`session <id>`・`token <印>` が返る。その秒数だけ `sleep` する。
#   3. その間にユーザが打てば、状態が戻って印が合わなくなる（UserPromptSubmit の `ccnavi idle reset`）。
#   4. 起きたら `ccnavi idle check` に聞く。`fire` なら、続く行を指示として標準エラーに出し、終了コード 2 で終わる。
#      asyncRewake は、この終了コード 2 でセッションを起こし、標準エラーの文を指示として渡す。
#
# 見ている人がいない実行（claude -p・SDK）と、サブエージェントは、実行ファイルが `skip` で除く。
# ここで待たせるとプロセスを引き止めてしまうので、除くのは待つより前にする。
#
# 終了コード: 起こすとき 2。それ以外は常に 0（促しの失敗が作業を止めてはならない）。

set -u
. "$(dirname "$0")/ccnavi-common.sh" 2>/dev/null || exit 0

case "${1:-}" in
start | notice | reset) ;;
*) exit 0 ;;
esac

root=$(ccnavi_workspace 2>/dev/null) || exit 0
bin=$(ccnavi_bin "$root" 2>/dev/null) || exit 0

case "$1" in
notice | reset)
	"$bin" --root "$root" idle "$1" 2>/dev/null || :
	exit 0
	;;
esac

payload=$(cat)

# 値の取り出し（1 行 1 項目の `<鍵> <値>`）。
pick() {
	printf '%s\n' "$plan" | sed -n "s/^$1 //p" | head -n 1
}

plan=$(printf '%s' "$payload" | "$bin" --root "$root" idle start 2>/dev/null) || exit 0
wait_seconds=$(pick wait)
session=$(pick session)
token=$(pick token)
[ -n "$wait_seconds" ] && [ -n "$session" ] && [ -n "$token" ] || exit 0
case "$wait_seconds" in
*[!0-9]*) exit 0 ;;
esac

sleep "$wait_seconds" || exit 0

answer=$("$bin" --root "$root" idle check "$session" "$token" "$wait_seconds" </dev/null 2>/dev/null) || exit 0
case "$answer" in
fire*)
	printf '%s\n' "$answer" | sed '1d' >&2
	exit 2
	;;
esac
exit 0

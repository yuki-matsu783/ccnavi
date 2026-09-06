#!/bin/sh
# Stop: ターンの終わりに 1 回だけテストを走らせる。
#
# 編集ごとの PostToolUse に置くと、1 ファイル直すたびに全件が走る。複数ファイルに
# またがる変更では途中の状態が必ず落ちるので、意味のない失敗の山を毎回読むことに
# なり、本当の失敗がその中に紛れる。ターンの終わりなら、変更が揃った状態で 1 回。
#
# 落ちたときは exit 2 で差し戻す。このイベントでモデルに届く経路はそれだけ。

payload=$(cat)

# この hook が差し戻した結果としてもう一度止まろうとしている場合。
# ここで再び差し戻すと、直せないまま無限に往復する。
case "$payload" in
*'"stop_hook_active":true'* | *'"stop_hook_active": true'*) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

# --failfast で最初の 1 件で止める。全件の失敗を並べても、直す順番は結局
# 1 件ずつなので、読む量だけが増える。
if tests=$(uv run --python 3.12 python -m unittest discover -s tests -t . --failfast 2>&1); then
	exit 0
fi

printf 'unittest（最初に落ちた 1 件）:\n%s\n' "$(printf '%s' "$tests" | tail -40)" >&2
printf '落ちたテストを直してから終わってください。\n' >&2
exit 2

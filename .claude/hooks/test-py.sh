#!/bin/sh
# Stop: ターンの終わりに 1 回だけテストを走らせ、落ちていたら差し戻す。
#
# 編集ごとの PostToolUse に置くと、1 ファイル直すたびに全件が走る。複数ファイルに
# またがる変更では途中の状態が必ず落ちるので、意味のない失敗の山を毎回読むことに
# なり、本当の失敗がその中に紛れる。ターンの終わりなら、変更が揃った状態で 1 回。
#
# 落ちたときは exit 2 で差し戻す。このイベントでモデルに届く経路はそれだけ。
#
# ただし差し戻しには上限を置く。直せない失敗を無限に差し戻すと、同じ場所を
# 延々と往復して人の手が入る機会が来ない。上限に達したら止まらせて、判断を人へ返す。
# payload の stop_hook_active は真偽値でしかなく「何回目か」を持たないので、
# 回数はここで数える。

# 差し戻す上限。3 回試して直らないなら、直し方が分かっていないということ。
MAX=3

payload=$(cat)

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

# JSON パーサ無しで取り出す。値は識別子なので引用符の中だけ見れば足りる。
session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -z "$session" ] && session=unknown

counter=".claude/ccnavi/stop-retries/$session"

# stop_hook_active が false なら、この hook が差し戻した結果ではなく、
# モデルが自分の判断で止まろうとしている。そこが数え始めの位置。
case "$payload" in
*'"stop_hook_active":true'* | *'"stop_hook_active": true'*) ;;
*) rm -f "$counter" ;;
esac

# --failfast で最初の 1 件で止める。全件の失敗を並べても、直す順番は結局
# 1 件ずつなので、読む量だけが増える。
if tests=$(uv run --python 3.12 python -m unittest discover -s tests -t . --failfast 2>&1); then
	rm -f "$counter"
	exit 0
fi

tried=0
[ -f "$counter" ] && tried=$(cat "$counter" 2>/dev/null)
case "$tried" in
'' | *[!0-9]*) tried=0 ;;
esac
tried=$((tried + 1))

if [ "$tried" -gt "$MAX" ]; then
	rm -f "$counter"
	# ここは exit 0 なのでモデルには届かない。届けると差し戻しと同じことになる。
	# 上限の意味は「機械の往復をやめて人に返す」なので、宛先は人でよい。
	printf 'ccnavi: テストが落ちたまま %s 回差し戻したので、これ以上は止めません。\n' "$MAX" >&2
	printf '%s\n' "$(printf '%s' "$tests" | tail -20)" >&2
	exit 0
fi

mkdir -p "$(dirname "$counter")"
printf '%s' "$tried" >"$counter"
printf 'unittest（最初に落ちた 1 件 / %s 回目、あと %s 回で打ち切り）:\n%s\n' \
	"$tried" "$((MAX - tried))" "$(printf '%s' "$tests" | tail -40)" >&2
printf '落ちたテストを直してから終わってください。\n' >&2
exit 2

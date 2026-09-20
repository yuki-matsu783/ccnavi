#!/bin/sh
# Stop: 拡張のファイルを触ったターンの終わりに、関わるグループのテストだけ回す。
#
# 触っていないターンは何もしない（exit 0）。文書だけを直したターンや Python だけの
# ターンでは 1 秒も伸びない。触ったターンで伸びるのは 3〜8 秒で、中身は
# `scripts/test-groups.js` が決める（触ったファイルの import を辿って、関わる
# グループだけ回す。全部で 11 秒）。
#
# 落ちたときは exit 2 で差し戻す。このイベントでモデルに届く経路はそれだけ。
# 差し戻しの上限は test-py.sh と同じ 3 回。数え方も同じ理由で、payload の
# stop_hook_active は「何回目か」を持たないのでここで数える。回数のファイルは
# test-py.sh と分けてある（別々に上限を数える。片方の失敗で他方の回数が減らない）。

# 差し戻す上限。3 回試して直らないなら、直し方が分かっていないということ。
MAX=3

payload=$(cat)

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -z "$session" ] && session=unknown

state="logs/session"
counter="$state/$session.ext-retries"
files="$state/$session.ext-files"

# 見捨てられたセッションの跡を掃除する（test-py.sh と同じ理由・同じ 1 時間）。
# この hook だけを登録しても効くように、ここにも置いてある。
find "$state" -type f -mmin +60 -delete 2>/dev/null

# stop_hook_active が false なら、この hook が差し戻した結果ではなく、
# モデルが自分の判断で止まろうとしている。そこが数え始めの位置。
case "$payload" in
*'"stop_hook_active":true'* | *'"stop_hook_active": true'*) ;;
*) rm -f "$counter" ;;
esac

# 拡張を触っていないターンは何もしない。触ったかどうかは PostToolUse の
# mark-ext.sh が書き残している。
[ -s "$files" ] || exit 0

# node が無い機械では回せない。モデルが直せることではないので、差し戻さずに人へ言う。
if ! command -v node >/dev/null 2>&1; then
	printf 'ccnavi: node が無いので拡張のテストを回していません（要るのは Node 22）。\n' >&2
	rm -f "$files"
	exit 0
fi

touched=$(sort -u "$files")

# 触ったファイルの綴りから、それが属する拡張のディレクトリを切り出す。ワークツリーで
# 作業していても CLAUDE_PROJECT_DIR は元のディレクトリを指したままなので、そこを
# 回すと、直したツリーが検査されないまま通ってしまう。
roots=$(printf '%s\n' "$touched" |
	sed -n 's#^\(.*/vscode-extension/ccnavi-board\)/.*#\1#p' | sort -u)

failed=""
output=""
for root in $roots; do
	[ -f "$root/scripts/test-groups.js" ] || continue
	# そのツリーのファイルだけを渡す。2 つのワークツリーを触ったターンでは、
	# それぞれのツリーで 1 回ずつ回る。
	mine=$(printf '%s\n' "$touched" | sed -n "\#^$root/#p" | tr '\n' ' ')
	[ -z "$mine" ] && continue
	if out=$(node "$root/scripts/test-groups.js" --for $mine 2>&1); then
		continue
	fi
	failed=$root
	output=$out
	break
done

if [ -z "$failed" ]; then
	rm -f "$counter" "$files"
	exit 0
fi

tried=0
[ -f "$counter" ] && tried=$(cat "$counter" 2>/dev/null)
case "$tried" in
'' | *[!0-9]*) tried=0 ;;
esac
tried=$((tried + 1))

if [ "$tried" -gt "$MAX" ]; then
	rm -f "$counter" "$files"
	# ここは exit 0 なのでモデルには届かない。届けると差し戻しと同じことになる。
	# 上限の意味は「機械の往復をやめて人に返す」なので、宛先は人でよい。
	printf 'ccnavi: %s のテストが落ちたまま %s 回差し戻したので、これ以上は止めません。\n' \
		"$failed" "$MAX" >&2
	printf '%s\n' "$(printf '%s' "$output" | tail -20)" >&2
	exit 0
fi

mkdir -p "$state"
printf '%s' "$tried" >"$counter"
printf '拡張のテスト（%s / %s 回目、あと %s 回で打ち切り）:\n%s\n' \
	"$failed" "$tried" "$((MAX - tried))" "$(printf '%s' "$output" | tail -40)" >&2
printf '落ちたテストを直してから終わってください。\n' >&2
exit 2

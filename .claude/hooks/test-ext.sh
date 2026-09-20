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
# mark-ext.sh と同じ直し方。区切りが入っていたら使わない。
case "$session" in
'' | */* | *\\*) session=unknown ;;
esac

state="logs/session"
counter="$state/$session.ext-retries"
files="$state/$session.ext-files"

# 見捨てられたセッションの跡を掃除する（test-py.sh と同じ理由・同じ 1 時間）。
# この hook だけを登録しても効くように、ここにも置いてある。
#
# 自分のセッションのファイルは外す。1 つのターンは 1 時間を超えることがあり（拡張を
# 直したあと調べ物が続くターン）、消してから読むと「触っていないターン」に見えて
# 黙って何も回らない。他セッションのぶんも、走っている連鎖のファイルは書くたびに
# 新しくなるので巻き添えにならない。
find "$state" -type f -mmin +60 ! -name "$session.*" -delete 2>/dev/null

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

sort -u "$files" >"$files.sorted" || exit 0

# 触ったファイルの綴りから、それが属する拡張のディレクトリを切り出す。ワークツリーで
# 作業していても CLAUDE_PROJECT_DIR は元のディレクトリを指したままなので、そこを
# 回すと、直したツリーが検査されないまま通ってしまう。
#
# パスは 1 行ずつ読む。`for root in $roots` と書くと、空白の入ったパス
# （Windows の `C:/Users/First Last/`）が語に割れ、どの語も test-groups.js を持たないので
# 何も回さずに通ってしまう。同じ理由で、絞り込みは sed の正規表現ではなく case の
# 文字列比較で行う（パスに `#` や `[` が入ると sed が構文エラーになる）。
sed -n 's#^\(.*/vscode-extension/ccnavi-board\)/.*#\1#p' "$files.sorted" |
	sort -u >"$files.roots"

failed=""
output=""
ran=0
notready=""
while IFS= read -r root; do
	[ -n "$root" ] || continue
	if [ ! -f "$root/scripts/test-groups.js" ]; then
		printf 'ccnavi: %s にテストの入口がないので回していません。\n' "$root" >&2
		continue
	fi
	# そのツリーのファイルだけを渡す。2 つのワークツリーを触ったターンでは、
	# それぞれのツリーで 1 回ずつ回る。
	set --
	while IFS= read -r one; do
		case "$one" in
		"$root"/*) set -- "$@" "$one" ;;
		esac
	done <"$files.sorted"
	[ "$#" -gt 0 ] || continue
	ran=1
	out=$(node "$root/scripts/test-groups.js" --for "$@" 2>&1)
	status=$?
	[ "$status" = 0 ] && continue
	# 3 は「node_modules が無い」など環境が足りない側。テストは落ちていないので、
	# 差し戻して「落ちたテストを直せ」と言うのは嘘になる。人へ回す。
	if [ "$status" = 3 ]; then
		notready=$out
		continue
	fi
	failed=$root
	output=$out
	break
done <"$files.roots"

rm -f "$files.sorted" "$files.roots"

if [ -z "$failed" ]; then
	rm -f "$counter" "$files"
	if [ -n "$notready" ]; then
		printf '%s\n' "$notready" >&2
		exit 0
	fi
	# 印はあるのに 1 つも回せなかった。黙って通すと「テストが通った」と区別が付かない。
	if [ "$ran" = 0 ]; then
		printf 'ccnavi: 拡張を触った印はありますが、回せるツリーが見つかりませんでした。\n' >&2
	fi
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

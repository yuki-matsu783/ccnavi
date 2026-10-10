#!/bin/sh
# Stop: 拡張のファイルを編集したターンの終わりに、関わるグループのテストだけ実行する
#
# 編集していないターンは何もしない（exit 0）。文書だけを直したターンや Python だけの
# ターンでは 1 秒も伸びない。編集したターンで伸びるのは 3〜8 秒で、中身は
# `scripts/test-groups.js` が決める（編集したファイルの import を辿って、関わる
# グループだけ実行する。全部で 11 秒）
#
# 失敗したときは exit 2 で差し戻す。このイベントでモデルに届く経路はこれだけ
#
# ただし差し戻しには上限を置く。直せない失敗を無限に差し戻すと、同じ場所を
# 繰り返し往復してユーザの手が入る機会が来ない。上限に達したら止めて、判断をユーザへ返す
# payload の stop_hook_active は真偽値でしかなく「何回目か」を持たないため、
# 回数はここで数える

# 差し戻す上限。3 回試して直らないなら、直し方が分かっていないということ
MAX=3

payload=$(cat)

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
# mark-ext.sh と同じ直し方。区切り文字が入っていたら使わない
case "$session" in
'' | */* | *\\*) session=unknown ;;
esac

state="logs/session"
counter="$state/$session.ext-retries"
files="$state/$session.ext-files"

# 終了したセッションが残したファイルを掃除する（test-py.sh と同じ理由・同じ 1 時間）
# このフックだけを登録しても効くように、ここにも置いてある
#
# 自分のセッションのファイルは除外する。1 つのターンは 1 時間を超えることがあり（拡張を
# 直したあと調べ物が続くターン）、消してから読むと「編集していないターン」に見えて
# 黙って何も実行されない。他セッションのぶんも、走っている連鎖のファイルは書くたびに
# 新しくなるため巻き込まれない
find "$state" -type f -mmin +60 ! -name "$session.*" -delete 2>/dev/null

# stop_hook_active が false なら、このフックが差し戻した結果ではなく、
# モデルが自分の判断で止まろうとしている。そこが数え始めの位置
case "$payload" in
*'"stop_hook_active":true'* | *'"stop_hook_active": true'*) ;;
*) rm -f "$counter" ;;
esac

# 拡張を編集していないターンは何もしない。編集したかどうかは PostToolUse の
# mark-ext.sh が記録している
[ -s "$files" ] || exit 0

# node がない環境では実行できない。モデルが直せることではないため、差し戻さずにユーザへ伝える
if ! command -v node >/dev/null 2>&1; then
	printf 'ccnavi: node がないため拡張のテストを実行していません（Node 22 が必要）。\n' >&2
	rm -f "$files"
	exit 0
fi

sort -u "$files" >"$files.sorted" || exit 0

# 編集したファイルのパスから、それが属する拡張のディレクトリを切り出す。ワークツリーで
# 作業していても CLAUDE_PROJECT_DIR は元のディレクトリを指したままのため、そこを
# 実行すると、直したツリーが検査されないまま通ってしまう
#
# パスは 1 行ずつ読む。`for root in $roots` と書くと、空白の入ったパス
# （Windows の `C:/Users/First Last/`）が単語に割れ、どの単語も test-groups.js を持たないため
# 何も実行せずに通ってしまう。同じ理由で、絞り込みは sed の正規表現ではなく case の
# 文字列比較で行う（パスに `#` や `[` が入ると sed が構文エラーになる）
sed -n 's#^\(.*/extensions/vscode/ccnavi-board\)/.*#\1#p' "$files.sorted" |
	sort -u >"$files.roots"

failed=""
output=""
ran=0
notready=""
while IFS= read -r root; do
	[ -n "$root" ] || continue
	if [ ! -f "$root/scripts/test-groups.js" ]; then
		printf 'ccnavi: %s にテストの入口がないため実行していません。\n' "$root" >&2
		continue
	fi
	# そのツリーのファイルだけを渡す。2 つのワークツリーを編集したターンでは、
	# それぞれのツリーで 1 回ずつ実行する
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
	# 3 は「node_modules がない」など環境が足りない側。テストは失敗していないため、
	# 差し戻して「失敗したテストを直せ」と言うのは誤りになる。ユーザへ回す
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
	# 記録はあるのに 1 つも実行できなかった。黙って通すと「テストが通った」と区別がつかない
	if [ "$ran" = 0 ]; then
		printf 'ccnavi: 拡張を編集した記録はありますが、実行できるツリーが見つかりませんでした。\n' >&2
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
	# ここは exit 0 なのでモデルには届かない。届けると差し戻しと同じことになる
	# 上限の意味は「機械の往復をやめてユーザに返す」なので、宛先はユーザでよい
	printf 'ccnavi: %s のテストが失敗したまま %s 回差し戻したので、これ以上は止めません。\n' \
		"$failed" "$MAX" >&2
	printf '%s\n' "$(printf '%s' "$output" | tail -20)" >&2
	exit 0
fi

mkdir -p "$state"
printf '%s' "$tried" >"$counter"
printf '拡張のテスト（%s / %s 回目、あと %s 回で打ち切り）:\n%s\n' \
	"$failed" "$tried" "$((MAX - tried))" "$(printf '%s' "$output" | tail -40)" >&2
printf '失敗したテストを直してから終わってください。\n' >&2
exit 2
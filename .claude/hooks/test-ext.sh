#!/bin/sh
# Stop: 拡張（VS Code・Chrome）のファイルを編集したターンの終わりに、関わるグループのテストだけ実行する
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
# 知らないうちに何も実行されなくなる。他セッションのぶんも、走っている連鎖のファイルは書くたびに
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
	printf 'ccnavi: node がないため拡張のテストを実行していません（Node 22.13.1 以上が必要）。\n' >&2
	rm -f "$files"
	exit 0
fi

sort -u "$files" >"$files.sorted" || exit 0

# 拡張は 2 つある。VS Code の拡張はテストをグループに分けてあり、入口
# （`scripts/test-groups.js --for <ファイル>...`）が触ったファイルに関わるグループだけを回す
# Chrome の拡張はグループに分けていないので、触ったら 1 つのグループとして全部を回す
# （`scripts/test.js`。拡張を読み込んだ Chromium での試験 `--e2e` は回さない。Chromium が要るため）
VSCODE="extensions/vscode/ccnavi-board"
CHROME="extensions/chrome/ccnavi-approval"

# 編集したファイルのパスから、それが属する拡張のディレクトリを切り出し、`<種類> <ルート>` の
# 1 行にする。ワークツリーで作業していても CLAUDE_PROJECT_DIR は元のディレクトリを指したままのため、
# そこを実行すると、直したツリーが検査されないまま通ってしまう
#
# 先頭に `/` の無い相対パス（`extensions/...` で始まる形）も拾う。mark-ext.sh は受け取った
# とおりに書き残すので、相対パスで来たものを落とすと、拡張を直したのに何も回らない。
# 相対パスは、上で移ったワークスペースルートからのパスとして読む
#
# パスは 1 行ずつ読む。`for root in $roots` と書くと、空白の入ったパス
# （Windows の `C:/Users/First Last/`）が単語に割れ、どの単語も入口を持たないため
# 何も実行せずに通ってしまう。同じ理由で、絞り込みは sed の正規表現ではなく case の
# 文字列比較で行う（パスに `#` や `[` が入ると sed が構文エラーになる）。ループの中で
# 起こすテストは標準入力を /dev/null にする。一覧を読んでいる標準入力を読まれると、残りが飛ぶ
#
# 共通の部品（extensions/shared/）を編集したときは、同じツリーの VS Code 拡張と Chrome 拡張の両方を実行する。
# VS Code 拡張は、shared を読む画面のグループを test-groups.js が import（@ccnavi/shared/...）を辿って決める。
# Chrome 拡張は、設定画面とボードの style.css が @ccnavi/shared/styles/*.css を @import し、
# scripts/build.js がバンドルするので、ファイル名の変更や削除でビルドが壊れる
# sed は 1 行に s を続けて適用すると、前の s が書き換えた行を次の s が読む。shared は 2 つの拡張へ振るので、
# 元の行を 2 度読む。sed を 2 回に分ける
{
	sed -n \
		-e "s#^\(.*/$VSCODE\)/.*#vscode \1#p" \
		-e "s#^\($VSCODE\)/.*#vscode \1#p" \
		-e "s#^\(.*/$CHROME\)/.*#chrome \1#p" \
		-e "s#^\($CHROME\)/.*#chrome \1#p" \
		-e "s#^\(.*\)/extensions/shared/.*#vscode \1/$VSCODE#p" \
		-e "s#^extensions/shared/.*#vscode $VSCODE#p" \
		"$files.sorted"
	sed -n \
		-e "s#^\(.*\)/extensions/shared/.*#chrome \1/$CHROME#p" \
		-e "s#^extensions/shared/.*#chrome $CHROME#p" \
		"$files.sorted"
} | sort -u >"$files.roots"

failed=""
output=""
ran=0
notready=""
while IFS= read -r line; do
	[ -n "$line" ] || continue
	kind=${line%% *}
	root=${line#* }
	case "$kind" in
	chrome) entry="$root/scripts/test.js" ;;
	*) entry="$root/scripts/test-groups.js" ;;
	esac
	if [ ! -f "$entry" ]; then
		printf 'ccnavi: %s にテストの入口がないため実行していません。\n' "$root" >&2
		continue
	fi
	# そのツリーのファイルだけを渡す。2 つのワークツリーを編集したターンでは、
	# それぞれのツリーで 1 回ずつ実行する
	# どちらの拡張にも、同じツリーの共通の部品（extensions/shared/）のファイルも渡す。
	# ルートが相対（extensions/...）のときは、ツリーの接頭辞は空になる
	case "$kind" in
	chrome) base=$CHROME ;;
	*) base=$VSCODE ;;
	esac
	if [ "$root" = "$base" ]; then
		shared="extensions/shared/"
	else
		shared="${root%"/$base"}/extensions/shared/"
	fi
	set --
	while IFS= read -r one; do
		case "$one" in
		"$root"/*) set -- "$@" "$one" ;;
		"$shared"*) set -- "$@" "$one" ;;
		esac
	done <"$files.sorted"
	[ "$#" -gt 0 ] || continue
	ran=1
	if [ "$kind" = chrome ]; then
		# 読み物（*.md・docs/）と生成物の置き場だけを触ったなら回さない。VS Code の入口が
		# 読み物だけのときに何も回さないのと揃える。共通の部品（CSS）はコードとして扱い、回す
		code=""
		for one in "$@"; do
			case "$one" in
			"$shared"*)
				code=yes
				continue
				;;
			esac
			case "${one#"$root"/}" in
			*.md | docs/* | out/* | dist/* | dist-e2e/* | node_modules/*) ;;
			*) code=yes ;;
			esac
		done
		[ -n "$code" ] || continue
		# node_modules が無ければ組み立てられない。モデルが直せることではないので、
		# VS Code の入口の終了コード 3 と同じく差し戻さずにユーザへ伝える
		if [ ! -d "$root/node_modules" ]; then
			notready="ccnavi: $root に node_modules がないため Chrome 拡張のテストを実行していません。$root で 'pnpm install --frozen-lockfile' を通してください。"
			continue
		fi
		out=$(cd "$root" && node scripts/test.js </dev/null 2>&1)
		status=$?
	else
		out=$(node "$entry" --for "$@" </dev/null 2>&1)
		status=$?
	fi
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
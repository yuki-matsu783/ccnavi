#!/bin/sh
# Stop: ターンの終わりに、go/・.ccnavi/・docs のどれかを触ったツリーで gofmt・go vet・go test を回す
#
# 編集ごとの PostToolUse に置くと、1 ファイル直すたびに全件が走る。複数ファイルに
# またがる変更では途中の状態が必ず失敗するため、意味のない失敗の山を毎回読むことに
# なり、本当の失敗がその中に紛れる。ターンの終わりなら、変更が揃った状態で 1 回
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

# JSON パーサなしで取り出す。値は識別子なので引用符の中だけ見れば足りる
session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
# 記録のファイル名になるため、区切り文字が入っていたら使わない（payload の値をそのまま
# パスに継ぐと、logs/session/ の外を指せる。mark-ext.sh・test-ext.sh と同じ直し方）
case "$session" in
'' | */* | *\\*) session=unknown ;;
esac

state="logs/session"
counter="$state/$session.retries"
go_trees="$state/$session.go-trees"

# 終了したセッションが残したファイルを掃除する
#
# ここの状態が意味を持つのは 1 回の停止の連鎖の中だけで、長くても数分。通常は
# テストが通るか、次の連鎖が始まるか、上限に達するかで消える。消えないのは
# 連鎖の途中でセッションが終わったときで、その session_id は二度と現れないため
# 誰も消さない。1 セッションにつき溜まっていく
#
# 保持期間を 1 時間にしてあるのは、連鎖の長さより十分に長く、放置の長さより十分に
# 短いから。走っている連鎖のファイルは書くたびに新しくなるため巻き込まれない
#
# 自分のセッションのファイルは除外する（test-ext.sh と同じ）。1 つのターンは 1 時間を
# 超えることがあり、lint-go.sh が最初の編集で書いた .go-trees を読む前に消すと、
# 「何も触っていないターン」に見えて、直したツリーが実行されなくなる
find "$state" -type f -mmin +60 ! -name "$session.*" -delete 2>/dev/null

# stop_hook_active が false なら、このフックが差し戻した結果ではなく、
# モデルが自分の判断で止まろうとしている。そこが数え始めの位置
case "$payload" in
*'"stop_hook_active":true'* | *'"stop_hook_active": true'*) ;;
*) rm -f "$counter" ;;
esac

# このターンで go/・.ccnavi/・docs のどれかを触ったツリーだけを見る。ワークツリーで作業していても
# CLAUDE_PROJECT_DIR は元のディレクトリを指したままのため、そこだけをテストすると
# 直したツリーが検査されないまま通ってしまう。逆に、あるツリーを全部テストすると、
# 編集していない他セッションの書きかけでこちらが差し戻される
# どこを触ったかは PostToolUse の lint-go.sh が記録している。何も触っていないターンは回さない
#
# ツリーは 1 行に 1 つで、1 行ずつ読む。`for target in $targets` と書くと、空白の入ったパス
# （Windows の `C:/Users/First Last/`）が単語に割れる。ループの中のテストは標準入力を /dev/null にする。
# 一覧を読んでいる標準入力をテストが読むと、残りのツリーが飛ばされる
[ -s "$go_trees" ] || exit 0
command -v go >/dev/null 2>&1 || exit 0
sort -u "$go_trees" >"$go_trees.sorted" || exit 0

# 毎ターン回すのは、書式・vet・clitest 以外の全部のテスト・リポジトリの決まりのテスト（clitest/repo）。
# clitest（本物の GitLab の模擬や sh を起こす重いもの）の残りと Go 全体のテストは、commit スキルが
# コミット前に回す。-count=1 は付けない（入力が変わっていないテストは go の結果の保存が効く）。
# Windows 向けの go vet も回す。OS で分けたファイル（build タグ）は、この機械の vet だけでは読まれない
go_check() (
	cd "$1/go" || exit 1
	unformatted=$(gofmt -l .)
	if [ -n "$unformatted" ]; then
		printf 'gofmt: 整形されていないファイル:\n%s\n' "$unformatted"
		exit 1
	fi
	go vet ./... || exit 1
	GOOS=windows go vet ./... || exit 1
	# shellcheck disable=SC2046
	go test $(go list ./... | grep -v /clitest/) || exit 1
	go test ./internal/clitest/repo/
)

failed=""
output=""
while IFS= read -r tree; do
	[ -f "$tree/go/go.mod" ] || continue
	if out=$(go_check "$tree" </dev/null 2>&1); then
		continue
	fi
	failed=$tree
	output=$out
	break
done <"$go_trees.sorted"
rm -f "$go_trees.sorted"

if [ -z "$failed" ]; then
	rm -f "$counter" "$go_trees"
	exit 0
fi

tried=0
[ -f "$counter" ] && tried=$(cat "$counter" 2>/dev/null)
case "$tried" in
'' | *[!0-9]*) tried=0 ;;
esac
tried=$((tried + 1))

if [ "$tried" -gt "$MAX" ]; then
	rm -f "$counter" "$go_trees"
	# ここは exit 0 なのでモデルには届かない。届けると差し戻しと同じことになる
	# 上限の意味は「機械の往復をやめてユーザに返す」なので、宛先はユーザでよい
	printf 'ccnavi: %s のテストが失敗したまま %s 回差し戻したので、これ以上は止めません。\n' \
		"$failed" "$MAX" >&2
	printf '%s\n' "$(printf '%s' "$output" | tail -20)" >&2
	exit 0
fi

mkdir -p "$state"
printf '%s' "$tried" >"$counter"
printf 'テスト（%s / 最初に失敗した 1 件 / %s 回目、あと %s 回で打ち切り）:\n%s\n' \
	"$failed" "$tried" "$((MAX - tried))" "$(printf '%s' "$output" | tail -40)" >&2
printf '失敗したテストを直してから終わってください。\n' >&2
exit 2

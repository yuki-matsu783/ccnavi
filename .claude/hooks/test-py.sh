#!/bin/sh
# Stop: ターンの終わりに、このターンで編集したツリーのテストを実行する
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
[ -z "$session" ] && session=unknown

state="logs/session"
counter="$state/$session.retries"
trees="$state/$session.trees"

# 終了したセッションが残したファイルを掃除する
#
# ここの状態が意味を持つのは 1 回の停止の連鎖の中だけで、長くても数分。通常は
# テストが通るか、次の連鎖が始まるか、上限に達するかで消える。消えないのは
# 連鎖の途中でセッションが終わったときで、その session_id は二度と現れないため
# 誰も消さない。1 セッションにつき溜まっていく
#
# 保持期間を 1 時間にしてあるのは、連鎖の長さより十分に長く、放置の長さより十分に
# 短いから。走っている連鎖のファイルは書くたびに新しくなるため巻き込まれない
find "$state" -type f -mmin +60 -delete 2>/dev/null

# stop_hook_active が false なら、このフックが差し戻した結果ではなく、
# モデルが自分の判断で止まろうとしている。そこが数え始めの位置
case "$payload" in
*'"stop_hook_active":true'* | *'"stop_hook_active": true'*) ;;
*) rm -f "$counter" ;;
esac

# このターンで編集したツリーだけを見る。ワークツリーで作業していても
# CLAUDE_PROJECT_DIR は元のディレクトリを指したままのため、そこだけをテストすると
# 直したツリーが検査されないまま通ってしまう。逆に、あるツリーを全部テストすると、
# 編集していない他セッションの書きかけでこちらが差し戻される
# どこを編集したかは PostToolUse の lint-py.sh が記録している
if [ -s "$trees" ]; then
	targets=$(sort -u "$trees")
else
	# Python を 1 つも編集していないターン。ルールファイルやテストの固定データの変更でも
	# 失敗するため、既定のツリーで 1 回は実行する
	targets=$(pwd)
fi

# --failfast で最初の 1 件で止める。全件の失敗を並べても、直す順番は結局
# 1 件ずつなので、読む量だけが増える
failed=""
output=""
for target in $targets; do
	# tests/ を持たないツリーは飛ばす。ツリーの有無だけを見ると、`unittest
	# discover -s tests` が "Start directory is not importable" で失敗し、ターンが
	# 止まる。モード B では projects/ のプロジェクトが Python とは限らないため、
	# プロジェクトを 1 つ置いた時点で起きる
	[ -d "$target/tests" ] || continue
	if out=$(cd "$target" && uv run --python 3.12 python -m unittest discover -s tests -t . --failfast 2>&1); then
		continue
	fi
	failed=$target
	output=$out
	break
done

if [ -z "$failed" ]; then
	rm -f "$counter" "$trees"
	exit 0
fi

tried=0
[ -f "$counter" ] && tried=$(cat "$counter" 2>/dev/null)
case "$tried" in
'' | *[!0-9]*) tried=0 ;;
esac
tried=$((tried + 1))

if [ "$tried" -gt "$MAX" ]; then
	rm -f "$counter" "$trees"
	# ここは exit 0 なのでモデルには届かない。届けると差し戻しと同じことになる
	# 上限の意味は「機械の往復をやめてユーザに返す」なので、宛先はユーザでよい
	printf 'ccnavi: %s のテストが失敗したまま %s 回差し戻したので、これ以上は止めません。\n' \
		"$failed" "$MAX" >&2
	printf '%s\n' "$(printf '%s' "$output" | tail -20)" >&2
	exit 0
fi

mkdir -p "$state"
printf '%s' "$tried" >"$counter"
printf 'unittest（%s / 最初に失敗した 1 件 / %s 回目、あと %s 回で打ち切り）:\n%s\n' \
	"$failed" "$tried" "$((MAX - tried))" "$(printf '%s' "$output" | tail -40)" >&2
printf '失敗したテストを直してから終わってください。\n' >&2
exit 2
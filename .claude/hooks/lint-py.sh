#!/bin/sh
# PostToolUse (Write|Edit): 編集したファイルを整形・検査する
#
# Python と Go のソースのときだけ実行する。指摘は exit 2 で返す
# Go はここでは gofmt だけを見る。go vet と go test はパッケージをまたぐので、ターンの終わりの
# test-py.sh が、ここで書き残したモジュールに回す
# このイベントでモデルに届く経路はこれだけなので、指摘がコミット時ではなく
# 同じターンの中で直る
#
# 検査の対象は編集したファイルだけで、ツリー全体ではない。他のファイルの既存の指摘が毎回出て
# 自分の指摘が埋もれるのを避けるため。弱点は、編集していないファイルの指摘が出ないこと。
# 他のファイルを壊す変更（定義の削除で別ファイルの import が壊れる等）はその場では出ず、
# ターンの終わりの test-py.sh (Stop) のテストが失敗すれば、そこで見つかる
#
# 報告はそれだけで読んで成立させる。同じイベントのフックは並行して順不同で走るため、
# 他のフックが何を判断したかには触れない

payload=$(cat)

# JSON パーサなしで tool_input.file_path を取り出す
file=$(printf '%s' "$payload" |
	sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

case "$file" in
*.py | *.go) ;;
*) exit 0 ;;
esac

# Windows のパスは区切りがバックスラッシュで、JSON の中では二重になっている
# dirname はバックスラッシュを区切りと見ないため、直さないと親ディレクトリを辿れない
file=$(printf '%s' "$file" | tr '\\' '/' | tr -s '/')

# 記録の置き場はワークスペースルートに固定する。ツリーが変わっても Stop フックが同じ場所を
# 見られるようにする。cd する前に確保しておく
main="${CLAUDE_PROJECT_DIR:-.}"
session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
case "$session" in
'' | */* | *\\*) session=unknown ;;
esac

# Go: いちばん近い go.mod でモジュールを決めて書き残し、編集したファイルの整形だけを見る
case "$file" in
*.go)
	module=$(dirname "$file")
	while [ ! -f "$module/go.mod" ]; do
		parent=$(dirname "$module")
		[ "$parent" = "$module" ] && exit 0
		module=$parent
	done
	mkdir -p "$main/logs/session"
	printf '%s\n' "$module" >>"$main/logs/session/$session.go-trees"
	command -v gofmt >/dev/null 2>&1 || exit 0
	unformatted=$(gofmt -l "$file" 2>&1) || :
	[ -z "$unformatted" ] && exit 0
	printf 'gofmt: 整形されていません。'"'"'gofmt -w "%s"'"'"' を実行してください:\n%s\n' "$file" "$unformatted" >&2
	printf 'ここで報告された指摘を直してから次へ進んでください。\n' >&2
	exit 2
	;;
esac

# 編集したファイルが属するツリーを、いちばん近い pyproject.toml で決める
#
# ワークツリーで作業していても CLAUDE_PROJECT_DIR は元のディレクトリを指したままのため、
# そこへ cd すると、直したのはワークツリーなのに検査するのはワークスペースルートになり
# 誤った結果になる。置き場の決まり (.claude/worktrees/) をハードコードせず、プロジェクトの
# 目印を上に辿って見つけるのは、決まりを変えてもここが古いまま放置されないようにするため
tree=$(dirname "$file")
while :; do
	[ -f "$tree/pyproject.toml" ] && break
	parent=$(dirname "$tree")
	[ "$parent" = "$tree" ] && break
	tree=$parent
done
[ -f "$tree/pyproject.toml" ] || tree="$main"

# どのツリーを編集したかを記録する。ターンの終わりに Stop フックが、ここに挙がった
# ツリーだけをテストする。触っていないツリーを巻き込むと、他セッションの書きかけで
# こちらが差し戻される
mkdir -p "$main/logs/session"
printf '%s\n' "$tree" >>"$main/logs/session/$session.trees"

cd "$tree" || exit 0

# uv だけを前提にする。使う Python の版はここで固定するため、
# PATH にどの python があるかに左右されない
UV="uv run --python 3.12"

report=""
add() { report="${report}$1
"; }

# ruff は設定を、対象ファイルのパスから上の階層へ順に探す。絶対パスのまま渡せば、そのツリーの
# pyproject.toml が効く。ファイルを明示すると exclude が無視されるため、--force-exclude を付ける
if unformatted=$($UV --with ruff ruff format --check --force-exclude "$file" 2>&1); then
	:
else
	add "ruff format: 整形されていません。'uv run --with ruff ruff format \"$file\"' を実行してください:
$unformatted"
fi

if lint=$($UV --with ruff ruff check --force-exclude "$file" 2>&1); then
	:
else
	add "ruff check:
$lint"
fi

# テストはここでは走らせない。Stop の test-py.sh に移した。編集ごとに全件を
# 走らせると、複数ファイルにまたがる変更では途中の状態が必ず失敗するため、
# 意味のない失敗の山を毎回読むことになり、本当の失敗がその中に紛れる

# 実行ファイルはここでは作り直さない。動かして確かめるときに `sh scripts/ccnavi-build.sh` を手で回す

[ -z "$report" ] && exit 0

printf '検査したツリー: %s\n%s\n' "$tree" "$report" >&2
printf 'ここで報告された指摘を直してから次へ進んでください。\n' >&2
exit 2
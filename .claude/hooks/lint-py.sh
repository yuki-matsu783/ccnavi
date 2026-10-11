#!/bin/sh
# PostToolUse (Write|Edit): 編集したファイルのツリーを整形・検査する
#
# Python ソースのときだけ実行する。指摘は exit 2 で返す
# このイベントでモデルに届く経路はこれだけなので、指摘がコミット時ではなく
# 同じターンの中で直る
#
# 報告はそれだけで読んで成立させる。同じイベントのフックは並行して順不同で走るため、
# 他のフックが何を判断したかには触れない

payload=$(cat)

# JSON パーサなしで tool_input.file_path を取り出す
file=$(printf '%s' "$payload" |
	sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

case "$file" in
*.py) ;;
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
[ -z "$session" ] && session=unknown

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

if unformatted=$($UV --with ruff ruff format --check . 2>&1); then
	:
else
	add "ruff format: 整形されていません。'uv run --with ruff ruff format .' を実行してください:
$unformatted"
fi

if lint=$($UV --with ruff ruff check . 2>&1); then
	:
else
	add "ruff check:
$lint"
fi

# テストはここでは走らせない。Stop の test-py.sh に移した。編集ごとに全件を
# 走らせると、複数ファイルにまたがる変更では途中の状態が必ず失敗するため、
# 意味のない失敗の山を毎回読むことになり、本当の失敗がその中に紛れる

# 実行ファイルはここでは作り直さない。PyInstaller が 11 秒かかるため外してある
# 動かして確かめるときに `uv run --with pyinstaller python build.py` を手で回す

[ -z "$report" ] && exit 0

printf '検査したツリー: %s\n%s\n' "$tree" "$report" >&2
printf 'ここで報告された指摘を直してから次へ進んでください。\n' >&2
exit 2
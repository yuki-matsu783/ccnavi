#!/bin/sh
# PostToolUse (Write|Edit|MultiEdit): 編集したファイルのツリーを整形・検査する。
#
# 編集したファイルが Python のソースのときだけ走る。報告は exit 2 で返す。
# このイベントでモデルに届く経路はそれだけなので、指摘がコミット時ではなく
# 同じターンの中で直る。
#
# 報告はそれだけで読んで成立させる。同じイベントの hook は並行して順不同で走るので、
# 他の hook が何を判断したかには決して触れない。

payload=$(cat)

# JSON パーサ無しで tool_input.file_path を取り出す。
file=$(printf '%s' "$payload" |
	sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

case "$file" in
*.py) ;;
*) exit 0 ;;
esac

# Windows のパスは区切りがバックスラッシュで、JSON の中では 2 個に増えている。
# dirname はバックスラッシュを区切りと見ないので、直さないと親を辿れない。
file=$(printf '%s' "$file" | tr '\\' '/' | tr -s '/')

# 記録の置き場は main に固定する。ツリーが変わっても Stop の hook が同じ場所を
# 見られるように。cd する前に確保しておく。
main="${CLAUDE_PROJECT_DIR:-.}"
session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -z "$session" ] && session=unknown

# 編集したファイルが属するツリーを、いちばん近い pyproject.toml で決める。
#
# worktree で作業していても CLAUDE_PROJECT_DIR は元のディレクトリを指したままなので、
# そこへ cd すると、直したのは worktree なのに検査するのは main になる。それでは
# 検査が嘘をつく。置き場の決まり (.claude/worktrees/) を焼き込まず、プロジェクトの
# 目印を上に辿って見つけるのは、決まりを変えてもここが黙って古くならないように。
tree=$(dirname "$file")
while :; do
	[ -f "$tree/pyproject.toml" ] && break
	parent=$(dirname "$tree")
	[ "$parent" = "$tree" ] && break
	tree=$parent
done
[ -f "$tree/pyproject.toml" ] || tree="$main"

# どのツリーを触ったかを残す。ターンの終わりに Stop の hook が、ここに挙がった
# ツリーだけをテストする。触っていないツリーを巻き添えにすると、他セッションの
# 書きかけでこちらが差し戻される。
mkdir -p "$main/.claude/ccnavi/session"
printf '%s\n' "$tree" >>"$main/.claude/ccnavi/session/$session.trees"

cd "$tree" || exit 0

# uv だけを前提にする。使う Python の版はここで固定するので、
# PATH にどの python が居るかに左右されない。
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
# 走らせると、複数ファイルにまたがる変更では途中の状態が必ず落ちるので、
# 意味のない失敗の山を毎回読むことになり、本当の失敗がその中に紛れる。

# 実行ファイルはここでは作り直さない。PyInstaller が 11 秒かかるので外してある。
# 動かして確かめるときに `uv run --with pyinstaller python build.py` を手で回す。

[ -z "$report" ] && exit 0

printf '検査したツリー: %s\n%s\n' "$tree" "$report" >&2
printf 'ここで報告された指摘を直してから次へ進んでください。\n' >&2
exit 2

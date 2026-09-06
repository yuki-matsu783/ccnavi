#!/bin/sh
# PostToolUse (Write|Edit|MultiEdit): Python のツリーを整形・検査し、テストを通し、
# 登録されている実行ファイルを作り直す。
#
# 編集したファイルが Python のソースのときだけ走る。報告は exit 2 で返す。
# このイベントでモデルに届く経路はそれだけなので、指摘がコミット時ではなく
# 同じターンの中で直る。
#
# 報告はそれだけで読んで成立させる。同じイベントの hook は並行して順不同で走るので、
# 他の hook が何を判断したかには決して触れない。

payload=$(cat)

# JSON パーサ無しで tool_input.file_path を取り出す。読むのは拡張子だけなので、
# Windows のパスに含まれるバックスラッシュは問題にならない。
file=$(printf '%s' "$payload" |
	sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

case "$file" in
*.py) ;;
*) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

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

# 実行ファイルはここでは作り直さない。PyInstaller は 11 秒かかるので、
# 編集 1 回ごとに走らせると 1 ファイル直すたびに 20 秒近く待つことになる。
# 登録されている実行ファイルはそのぶん古いままなので、動かして確かめるときに
# 手で `uv run --with pyinstaller python build.py` を回す。

[ -z "$report" ] && exit 0

printf '%s\n' "$report" >&2
printf 'ここで報告された指摘を直してから次へ進んでください。\n' >&2
exit 2

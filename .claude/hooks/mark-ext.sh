#!/bin/sh
# PostToolUse (Write|Edit): 拡張のファイルを触ったことを書き残す。
#
# NotebookEdit は入れない。あの道具が渡すのは `notebook_path` で `file_path` ではなく、
# 拡張にノートブックは無いので、入れても一度も当たらない。
#
# 書き残すだけで、検査もテストもここではしない。ターンの終わりに test-ext.sh が
# この一覧を読み、関わるグループだけを回す。編集ごとに回すと、複数ファイルに
# またがる変更では途中の状態が必ず落ちる（ADR-0036 と同じ理由）。
#
# 拡張には整形も静的検査も無いので、lint-py.sh に当たるものはここには無い。
# 型の検査は tsc で、テストと同じコンパイルで済むので test-ext.sh の側にある。
#
# 報告はしない（exit 0 だけ）。同じイベントの hook は並行して順不同で走るので、
# 他の hook が何を判断したかには触れない。

payload=$(cat)

# JSON パーサ無しで tool_input.file_path を取り出す。
#
# 最初のものを取る。`sed 's/.*"file_path".*/'` は貪欲なので最後のものを取り、
# payload に同じ綴りが 2 回出る形（道具の返りにも入っているとき）では、
# 編集した相手ではないほうを拾う。
file=$(printf '%s' "$payload" |
	grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' |
	sed -n '1s/.*"\([^"]*\)"$/\1/p')
[ -z "$file" ] && exit 0

# Windows のパスは区切りがバックスラッシュで、JSON の中では 2 個に増えている。
# 綴りを見て判断するので、ここで `/` に寄せる。
file=$(printf '%s' "$file" | tr '\\' '/' | tr -s '/')

# 拡張の外は数えない。ワークツリーで作業していてもパスの途中にこの綴りが立つので、
# 置き場の決まり（.claude/worktrees/）を焼き込まずに拾える。
# 相対で来た形（先頭に `/` が無い）も受ける。
case "$file" in
*/vscode-extension/ccnavi-board/* | vscode-extension/ccnavi-board/*) ;;
*) exit 0 ;;
esac

# 記録の置き場はワークスペースルートに固定する。ワークツリーで編集していても
# CLAUDE_PROJECT_DIR は元のディレクトリを指したままなので、Stop の hook が同じ場所を
# 見られる。どのツリーを触ったかはパスそのものが持っている。
main="${CLAUDE_PROJECT_DIR:-.}"
session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
# 記録の名前になるので、区切りが入っていたら使わない（payload の値をそのまま
# パスに継ぐと、logs/session/ の外を指せる）。
case "$session" in
'' | */* | *\\*) session=unknown ;;
esac

mkdir -p "$main/logs/session" || exit 0
printf '%s\n' "$file" >>"$main/logs/session/$session.ext-files"
exit 0

#!/bin/sh
# PostToolUse (Write|Edit): 拡張のファイルを編集したことを記録する
#
# NotebookEdit は対象外。あのツールが渡すのは `notebook_path` で `file_path` ではなく、
# 拡張にノートブックはないため、入れても一度も該当しない
#
# 記録するだけで、検査もテストもここではしない。ターンの終わりに test-ext.sh が
# この一覧を読み、関わるグループだけを実行する。編集ごとに実行すると、複数ファイルに
# またがる変更では途中の状態が必ず失敗する。Python のテストをターンの終わりに実行するのも同じ理由
#
# 拡張には整形も静的検査もないため、lint-py.sh に当たるものはここにはない
# 型の検査は tsc で、テストと同じコンパイルで済むため test-ext.sh の側にある
#
# 報告はしない（exit 0 のみ）。同じイベントのフックは並行して順不同で走るため、
# 他のフックが何を判断したかには触れない

payload=$(cat)

# JSON パーサなしで tool_input.file_path を取り出す
#
# 最初のものを取る。`sed 's/.*"file_path".*/'` は貪欲マッチのため最後のものを取り、
# payload に同じキーが 2 回出る形（ツールの返り値にも入っているとき）では、
# 編集した相手ではないほうを拾ってしまう
file=$(printf '%s' "$payload" |
	grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' |
	sed -n '1s/.*"\([^"]*\)"$/\1/p')
[ -z "$file" ] && exit 0

# Windows のパスは区切りがバックスラッシュで、JSON の中では二重になっている
# パスの表記を見て判断するため、ここで `/` に統一する
file=$(printf '%s' "$file" | tr '\\' '/' | tr -s '/')

# 拡張の外は対象外。ワークツリーで作業していてもパスの途中にこの文字列が現れるため、
# 置き場の決まり（.claude/worktrees/）をハードコードせずに拾える
# 相対パスで来た形（先頭に `/` がない）も受け付ける
case "$file" in
*/extensions/vscode/ccnavi-board/* | extensions/vscode/ccnavi-board/*) ;;
*) exit 0 ;;
esac

# 記録の置き場はワークスペースルートに固定する。ワークツリーで編集していても
# CLAUDE_PROJECT_DIR は元のディレクトリを指したままのため、Stop フックが同じ場所を
# 見られる。どのツリーを編集したかはパスそのものが持っている
main="${CLAUDE_PROJECT_DIR:-.}"
session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
# 記録のファイル名になるため、区切り文字が入っていたら使わない（payload の値をそのまま
# パスに継ぐと、logs/session/ の外を指せる）
case "$session" in
'' | */* | *\\*) session=unknown ;;
esac

mkdir -p "$main/logs/session" || exit 0
printf '%s\n' "$file" >>"$main/logs/session/$session.ext-files"
exit 0
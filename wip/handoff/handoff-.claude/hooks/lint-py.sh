#!/bin/sh
# PostToolUse (Write|Edit): 編集した Go のファイルを gofmt で見て、触った場所を書き残す
#
# 指摘は exit 2 で返す。このイベントでモデルに届く経路はこれだけなので、指摘がコミット時ではなく
# 同じターンの中で直る
# ここで見るのは gofmt だけ。go vet と go test はパッケージをまたぐので、ターンの終わりの
# test-go.sh が、ここで書き残した場所に回す
#
# 検査の対象は編集したファイルだけで、ツリー全体ではない。他のファイルの既存の指摘が毎回出て
# 自分の指摘が埋もれるのを避けるため。他のファイルを壊す変更（定義の削除で別ファイルの参照が壊れる等）は
# その場では出ず、ターンの終わりの test-go.sh のテストが失敗すれば、そこで見つかる
#
# 報告はそれだけで読んで成立させる。同じイベントのフックは並行して順不同で走るため、
# 他のフックが何を判断したかには触れない

payload=$(cat)

# JSON パーサなしで tool_input.file_path を取り出す
file=$(printf '%s' "$payload" |
	sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -n "$file" ] || exit 0

# Windows のパスは区切りがバックスラッシュで、JSON の中では二重になっている
# dirname はバックスラッシュを区切りと見ないため、直さないと親ディレクトリを辿れない
file=$(printf '%s' "$file" | tr '\\' '/' | tr -s '/')

# 記録の置き場はワークスペースルートに固定する。ツリーが変わっても Stop フックが同じ場所を
# 見られるようにする
main="${CLAUDE_PROJECT_DIR:-.}"
session=$(printf '%s' "$payload" |
	sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
case "$session" in
'' | */* | *\\*) session=unknown ;;
esac

# 編集したファイルが属するツリーを、go/go.mod を持ついちばん近い親で決める。
# ワークツリーで作業していても CLAUDE_PROJECT_DIR は元のディレクトリを指したままのため、
# そこを見ると、直したのはワークツリーなのに検査するのはワークスペースルートになる
tree=$(dirname "$file")
while [ ! -f "$tree/go/go.mod" ]; do
	parent=$(dirname "$tree")
	[ "$parent" = "$tree" ] && exit 0
	tree=$parent
done

# go/・.ccnavi/・docs/・wip/・.claude/・scripts/ のどれかを触ったら、そのツリーを書き残す。ターンの終わりに test-go.sh が、
# ここに挙がったツリーだけをテストする。触っていないツリーを巻き込むと、他セッションの書きかけで
# こちらが差し戻される
case "${file#"$tree"/}" in
go/* | .ccnavi/* | docs/* | wip/* | .claude/* | scripts/*)
	mkdir -p "$main/logs/session"
	printf '%s\n' "$tree" >>"$main/logs/session/$session.go-trees"
	;;
esac

case "$file" in
*.go) ;;
*) exit 0 ;;
esac

command -v gofmt >/dev/null 2>&1 || exit 0
unformatted=$(gofmt -l "$file" 2>&1) || :
[ -z "$unformatted" ] && exit 0
printf 'gofmt: 整形されていません。'"'"'gofmt -w "%s"'"'"' を実行してください:\n%s\n' "$file" "$unformatted" >&2
printf 'ここで報告された指摘を直してから次へ進んでください。\n' >&2
exit 2

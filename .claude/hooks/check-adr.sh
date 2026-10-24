#!/bin/sh
# PostToolUse (Write|Edit): ADR の番号が、ほかの ADR と重なっていないかを見る
#
# ADR は docs/30_adr/<主題>/NNNN-名前.md に分けて置く。番号は主題をまたぐ通し番号なので、
# 1 つのディレクトリだけを見て最後尾の次を採ると、別の主題が使っている番号と重なる。
# ファイルを書いた直後に言えば、そのターンの中で直せる
#
# 重い検査はしない。番号の重複はテスト (tests/core/test_adr_numbers.py) も見るが、
# そちらは ADR を足しただけのコミットでは回らない。ここはその手前の見張り
#
# 指摘は exit 2 で返す。このイベントでモデルに届く経路はこれだけ
# 報告はそれだけで読んで成立させる。同じイベントのフックは並行して順不同で走るため、
# 他のフックが何を判断したかには触れない

payload=$(cat)

# JSON パーサなしで tool_input.file_path を取り出す。最初のものを取る
file=$(printf '%s' "$payload" |
	grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' |
	sed -n '1s/.*"\([^"]*\)"$/\1/p')
[ -z "$file" ] && exit 0

# Windows のパスは区切りがバックスラッシュで、JSON の中では二重になっている
file=$(printf '%s' "$file" | tr '\\' '/' | tr -s '/')

# パスの大文字小文字は区別しない。見分けるためだけに小文字にし、取り出しは元の綴りで行う
lower=$(printf '%s' "$file" | tr 'A-Z' 'a-z')

case "$lower" in
*/docs/30_adr/[0-9][0-9][0-9][0-9]-*.md)
	top=1
	;;
*/docs/30_adr/*/[0-9][0-9][0-9][0-9]-*.md)
	top=
	;;
*) exit 0 ;;
esac

# 編集したファイルが属するツリーの docs/30_adr を見る
# ワークツリーで作業していても CLAUDE_PROJECT_DIR は元のディレクトリを指したままなので、
# そちらではなく、編集したパスの前半をツリーの根として使う
pos=$(printf '%s' "$lower" | awk '{ print index($0, "/docs/30_adr/") }')
root=$(printf '%s' "$file" | cut -c1-$((pos - 1)))
adr="$root/docs/30_adr"
[ -d "$adr" ] || exit 0

name=$(basename "$file")
num=$(printf '%s' "$name" | cut -c1-4)

# 全体の最大の番号の次。0172 は 8 進数と読まれるため、先頭の 0 を落としてから足す
next() {
	max=$(ls "$adr"/*/ 2>/dev/null | grep -oE '^[0-9]{4}' | sort | tail -1 | sed 's/^0*//')
	printf '%04d' $((${max:-0} + 1))
}

if [ -n "$top" ]; then
	echo "ADR は docs/30_adr/ の直下ではなく、主題のディレクトリの中に置いてください: $name" >&2
	echo "主題は docs/30_adr.md の「置き場と分け方」の表から選びます（rules・ticket・config・base・extension・worktree-git）。" >&2
	exit 2
fi

# 同じ番号のファイルが、自分以外にもあるか
dup=""
for f in "$adr"/*/"$num"-*.md; do
	[ -e "$f" ] || continue
	[ "$f" = "$file" ] && continue
	dup="$dup $f"
done

if [ -n "$dup" ]; then
	echo "ADR の番号 $num は、すでに別の ADR が使っています:" >&2
	for f in $dup; do echo "  $f" >&2; done
	echo "主題をまたいだ通し番号なので、全ディレクトリの最大の番号の次を採ります。次の空き番号は $(next) です。" >&2
	echo "（探し方は docs/30_adr.md の「最後尾は、主題のディレクトリを横断して探す」）" >&2
	exit 2
fi

exit 0

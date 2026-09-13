#!/bin/sh
# ccnavi-clean — 作業ツリー 1 本の生成物を消す。`git worktree remove` の前に打つ。
#
#   sh .claude/scripts/ccnavi-clean.sh <名前>
#   sh .claude/scripts/ccnavi-clean.sh <名前> --dry-run
#
# Windows では、pnpm の node_modules が深すぎる（260 文字を超える）ことと、uv の
# .venv が掴まれていることで、`git worktree remove` が途中で止まって抜け殻が残る。
# 先に生成物だけを消しておく。
#
# <名前> は .claude/worktrees/ の直下のディレクトリの名前。パスは受け付けない。
# `rm -rf` の代わりに任意の場所を消す道具にしないためで、ccnavi の recursive-delete の
# 趣旨（消す対象を名指しする）に合わせてある。
#
# 消すのは、作り直せば戻る決まった名前のディレクトリだけ。何を消すかと消し方は
# ccnavi-clean.js にある。
#
# 作業ツリーに未コミットの変更があれば、何も消さずに止める。別のセッションが
# そこで作業している見込みが高い。git に登録の残っていない抜け殻（.git が無い、
# または .git が指す先が消えている）は確かめようが無いので、確かめずに進める。
# 消すのは生成物だけなので、書きかけは残る。
#
# 終了コード: 0 成功 / 1 前提の未充足か消し残し / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 §25.8）。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .claude/scripts/ccnavi-clean.sh <名前> [--dry-run]

  <名前>     .claude/worktrees/ の直下の名前。パスは書けない
  --dry-run  消すものを並べるだけで、消さない

消すもの: node_modules / .venv / __pycache__ / .pytest_cache と、package.json の隣の out
未コミットの変更がある作業ツリーでは、何も消さずに止まる
USAGE
}

name=""
dry=""
for arg in "$@"; do
	case "$arg" in
	-h | --help | help)
		usage
		exit 0
		;;
	--dry-run)
		dry=1
		;;
	-*)
		printf 'ccnavi-clean: %s は通しません。使えるのは --dry-run だけです。\n' "$arg" >&2
		exit 2
		;;
	*)
		if [ -n "$name" ]; then
			printf 'ccnavi-clean: 作業ツリーは 1 回に 1 本だけ指定します（%s と %s）。\n' "$name" "$arg" >&2
			exit 2
		fi
		name="$arg"
		;;
	esac
done

[ -n "$name" ] || {
	usage
	exit 2
}

case "$name" in
. | .. | */* | *\\* | *:*)
	printf 'ccnavi-clean: 名前にパスは書けません（%s）。.claude/worktrees/ の直下の名前だけを渡してください。\n' "$name" >&2
	exit 2
	;;
esac

root=$(ccnavi_workspace) || {
	printf 'ccnavi-clean: ワークスペースルートが見つかりません（.claude/scripts/ を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

target="$root/.claude/worktrees/$name"

# 作業ツリーの置き場そのものがリンクなら、消す先が置き場の外にある。たどらない。
if [ -L "$target" ]; then
	printf 'ccnavi-clean: %s はリンクです。リンクの先は消しません。\n' "$target" >&2
	exit 2
fi
[ -d "$target" ] || {
	printf 'ccnavi-clean: %s がありません。\n' "$target" >&2
	exit 2
}

# 未コミットの変更。`.git` が無いときは git に聞かない。聞くと上へ登って
# ワークスペースのリポジトリを答えてしまう。
if [ -e "$target/.git" ]; then
	if changes=$(git -C "$target" status --porcelain 2>/dev/null); then
		if [ -n "$changes" ]; then
			printf 'ccnavi-clean: %s に未コミットの変更があるので、何も消しません。別のセッションが作業している見込みが高いです。\n' "$name" >&2
			printf '%s\n' "$changes" | head -n 10 >&2
			exit 1
		fi
	else
		printf 'ccnavi-clean: %s は git の登録が残っていない抜け殻として扱います（未コミットの変更は確かめていません）。\n' "$name" >&2
	fi
fi

command -v node >/dev/null 2>&1 || {
	printf 'ccnavi-clean: node が見つかりません。Node 22 を入れてから打ち直してください。\n' >&2
	exit 2
}

exec node "$(dirname "$0")/ccnavi-clean.js" "$target" ${dry:+--dry-run}

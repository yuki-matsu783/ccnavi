#!/bin/sh
# ccnavi-approve — 提案を承認し、承認済みチケットを親のブランチに乗せて push する。人が端末で打つ。
#
#   sh .claude/scripts/ccnavi-approve.sh
#
# 承認そのものは ccnavi の `--approve`。承認済みチケットは親チケットのツリーの
# $CCNAVI_APPROVED（既定 .ccnavi/tickets）に置かれる。そこはプロジェクトの git が
# 追跡していて、コミットして push するまで他の機械には届かない（設計 §9.2）。
# A が承認して B の機械で作業する流れは、この push で成り立つ。
#
# 実行ファイルはネットワークに出さない（設計 §3）ので、コミットと push はここでやる。
# 実行ファイルが持つのは、どこに置くかと、何を承認してよいかの判定だけ。
#
# コミットはパスを限る。`-a` も `add -A` も使わない。親の作業ツリーには人や他の
# セッションの書きかけがあるので、巻き込むと承認のコミットが他人の変更を運ぶ。
#
# 統合先が保護されている（main / master / develop / release）ときは push しない。
# そこへ直接送る判断は人のものなので、綴りを出して止める。
#
# 終了コード: 0 成功 / 1 承認が通らなかった / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 §11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .claude/scripts/ccnavi-approve.sh

  承認待ちのチケットを束で見せ、承認したら承認済みチケットをコミットして push する。
  端末から人が打つ。エージェントからは呼べない。
USAGE
}

case "${1:-}" in
"") ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	printf 'ccnavi-approve: 引数は取りません。\n' >&2
	usage >&2
	exit 2
	;;
esac

# main の根。作業ツリーの中から呼ばれても、ツリーの一覧は main の側から数える。
root=$(ccnavi_workspace) || {
	printf 'ccnavi-approve: ワークスペースルートが見つかりません（.claude/scripts/ を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 実行ファイル。設定に書かれた綴りを優先し、無ければ既定の置き場、それも無ければソース。
case "${CCNAVI_BIN_PATH:-}" in
/* | [A-Za-z]:*) bin="$CCNAVI_BIN_PATH" ;;
*) bin="$root/${CCNAVI_BIN_PATH:-dist/ccnavi/ccnavi}" ;;
esac
if [ -x "$bin" ]; then
	set -- "$bin" --root "$root" --approve
elif [ -x "$bin.exe" ]; then
	set -- "$bin.exe" --root "$root" --approve
elif [ -f "$root/ccnavi/__main__.py" ]; then
	set -- uv run python -m ccnavi --root "$root" --approve
else
	printf 'ccnavi-approve: ccnavi の実行ファイルが無い (%s)。build.py で組み立ててください。\n' "$bin" >&2
	exit 2
fi

# 承認。落ちたらそこで終わり。承認済みチケットが 1 つも書かれていないので、運ぶものも無い。
"$@" || exit 1

approved="${CCNAVI_APPROVED:-.ccnavi/tickets}"
projects="${CCNAVI_PROJECTS:-projects}"

# 承認済みチケットを持ちうるツリー。ワークスペース、プロジェクト、作業ツリー。
# glob が何にも当たらなければ綴りのまま残るので、-d で落とす。
trees="$root"
for dir in "$root/$projects"/* "$root/.claude/worktrees"/*; do
	[ -d "$dir" ] || continue
	trees="$trees
$dir"
done

printf '%s\n' "$trees" | while IFS= read -r tree; do
	[ -n "$tree" ] || continue
	[ -d "$tree/$approved" ] || continue
	git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
	changed=$(git -C "$tree" status --porcelain -- "$approved" 2>/dev/null || :)
	[ -n "$changed" ] || continue

	name=$(basename "$tree")
	branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
		printf 'ccnavi-approve: %s はブランチの上に居ない。承認済みチケットは手でコミットしてください。\n' \
			"$name" >&2
		continue
	fi

	git -C "$tree" add -- "$approved"
	git -C "$tree" commit --quiet -m "ccnavi: 承認済みチケットを更新" || {
		printf 'ccnavi-approve: %s で承認済みチケットをコミットできない。\n' "$name" >&2
		continue
	}
	case "$branch" in
	main | master | develop | release | release/*)
		printf 'ccnavi-approve: %s は %s の上に居るので push しません。送るかどうかは人が決めます。\n' \
			"$name" "$branch" >&2
		continue
		;;
	esac

	# push は落ちても承認を巻き戻さない。コミットは残るので、人がもう一度送れる。
	if git -C "$tree" push --quiet -u origin "$branch" 2>/dev/null; then
		printf '承認済みチケットを %s へ送った（%s）。\n' "$branch" "$name"
	else
		printf 'ccnavi-approve: %s の push が通らなかった。手で送ってください（git push -u origin %s）。\n' \
			"$name" "$branch" >&2
	fi
done

exit 0

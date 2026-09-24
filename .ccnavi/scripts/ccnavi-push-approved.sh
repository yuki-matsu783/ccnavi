#!/bin/sh
# ccnavi-push-approved — 承認済みチケットの置き場（と、承認で todo/ から消えた提案）だけをコミットし、
# 保護されたブランチでなければ push する。
#
#   sh .ccnavi/scripts/ccnavi-push-approved.sh
#
# 承認はしない。実行ファイルも起動しない。承認済みチケットは置かれただけでは他の機械に
# 届かない（設計 9.2）ので、置いたあとに運ぶのがこの sh。端末の承認は ccnavi-approve.sh が、
# ボードの承認は端末に送った 1 行が呼ぶ。対になるのはセッションの頭に取ってくる ccnavi-fetch.sh。
#
# 数えるツリーは、ワークスペース、$CCNAVI_PROJECTS（既定 projects）の下、.claude/worktrees の下。
# 置き場は $CCNAVI_TICKETS_APPROVED（既定 .ccnavi/approved）。承認は提案を
# $CCNAVI_TICKETS_PROPOSAL（既定 wip/proposals）の todo/ から動かすので（ADR-0055）、
# そこで追跡されていたファイルの削除も同じコミットに入れる。todo/ の書きかけ（未追跡・編集中）は運ばない。
#
# - コミットはパスを限る。`-a` も `add -A` も使わない。他人の書きかけを運ばない
# - シンボリックリンクは辿らない。置き場（projects/ や .claude/worktrees/）そのものも、その下の
#   1 件ずつも。辿るとワークスペースの外のリポジトリにコミットして push する
# - ブランチの上に居ない（detached）ツリーは名指しして飛ばす。失敗には数えない
# - main / master / develop / release / release/* はコミットだけして push しない
# - 1 本のツリーで add・commit・push が落ちても、他のツリーは運ぶ
#
# 終了コード: 0 運ぶものが無い・全部コミットした（push しなかったブランチ、飛ばしたツリーを含む） /
#           1 ステージかコミットできなかったツリーか、push が落ちたツリーが 1 つ以上ある /
#           2 引数の誤り・ワークスペースルートが見つからない・一時ファイルが作れない

set -eu

. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-push-approved.sh

  承認済みチケットの置き場に変更があるツリーごとに、その置き場だけをコミットし、
  保護されたブランチでなければ push する。引数は取らない。
USAGE
}

case "${1:-}" in
"") ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	printf 'ccnavi-push-approved: 引数は取りません。\n' >&2
	usage >&2
	exit 2
	;;
esac

root=$(ccnavi_workspace) || {
	printf 'ccnavi-push-approved: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

approved="${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}"
proposals="${CCNAVI_TICKETS_PROPOSAL:-wip/proposals}"
projects="${CCNAVI_PROJECTS:-projects}"
# 末尾の / を落とす。`[ -L "projects/" ]` はリンクを辿って偽になる。
approved="${approved%/}"
proposals="${proposals%/}"
projects="${projects%/}"
# 落として空になる綴り（`/`）と `.` は、ワークスペースルートそのものを指す。置き場なら
# ルートの直下を全部ツリーとして数え、承認済みチケットの置き場ならツリー全体をコミットする。
# どちらも頼まれた置き場ではないので、既定に戻す。
case "$approved" in
"" | .) approved=".ccnavi/approved" ;;
esac
case "$proposals" in
"" | .) proposals="wip/proposals" ;;
esac
case "$projects" in
"" | .) projects="projects" ;;
esac

# ツリーごとの結果を subshell (while はパイプの右側なので別プロセス) の外へ持ち出すための控え。
state=$(mktemp "${TMPDIR:-/tmp}/ccnavi-push-approved.XXXXXX") || {
	printf 'ccnavi-push-approved: 一時ファイルが作れません。\n' >&2
	exit 2
}
trap 'rm -f "$state"' EXIT

# ワークスペースルートから rel を 1 段ずつ下り、シンボリックリンクの段があれば 0。
# その段（ルートからの綴り）を linked に残す。`.claude` だけがリンクでも見逃さない。
linked=""
linked_segment() {
	linked=""
	rest="$1"
	walked=""
	while [ -n "$rest" ]; do
		segment="${rest%%/*}"
		if [ "$segment" = "$rest" ]; then
			rest=""
		else
			rest="${rest#*/}"
		fi
		[ -n "$segment" ] || continue
		walked="${walked:+$walked/}$segment"
		if [ -L "$root/$walked" ]; then
			linked="$walked"
			return 0
		fi
	done
	return 1
}

# 数えるのは 置き場 → その下の 1 件ずつ、の二段。どちらの段でもリンクは辿らない。
trees="$root"
for place in "$projects" ".claude/worktrees"; do
	if linked_segment "$place"; then
		printf 'ccnavi-push-approved: %s はシンボリックリンクなので、その下を辿りません。\n' "$linked" >&2
		continue
	fi
	[ -d "$root/$place" ] || continue
	for dir in "$root/$place"/*; do
		if [ -L "$dir" ]; then
			printf 'ccnavi-push-approved: %s はシンボリックリンクなので辿りません。\n' \
				"$place/$(basename "$dir")" >&2
			continue
		fi
		# glob が何にも当たらなければ綴りのまま残るので、-d で落とす。
		[ -d "$dir" ] || continue
		trees="$trees
$dir"
	done
done

printf '%s\n' "$trees" | while IFS= read -r tree; do
	[ -n "$tree" ] || continue
	[ -d "$tree/$approved" ] || continue
	git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
	changed=$(git -C "$tree" status --porcelain -- "$approved" 2>/dev/null || :)
	[ -n "$changed" ] || continue

	# 何か 1 つでも運ぶ対象があったことの印。detached で飛ばしても「無い」とは言わない。
	printf 'seen\n' >>"$state"

	name=$(basename "$tree")
	branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
		printf 'ccnavi-push-approved: %s はブランチの上に居ない。承認済みチケットは手でコミットしてください。\n' \
			"$name" >&2
		continue
	fi

	git -C "$tree" add -- "$approved" || {
		printf 'ccnavi-push-approved: %s で承認済みチケットをステージできない。\n' "$name" >&2
		printf 'fail\n' >>"$state"
		continue
	}
	# 承認で todo/ から消えた提案。追跡されていたものの削除だけを入れる（`ls-files --deleted`）。
	# 未追跡の下書きも、編集中の提案も入れない。消えたものが無ければ pathspec にも足さない
	# （git に知られていない綴りを pathspec に並べると commit が落ちる）。
	gone=$(git -C "$tree" ls-files --deleted -z -- "$proposals/todo" 2>/dev/null | tr '\000' '\n' || :)
	scope="$approved"
	if [ -n "$gone" ]; then
		printf '%s\n' "$gone" | while IFS= read -r removed; do
			[ -n "$removed" ] || continue
			git -C "$tree" add -u -- "$removed" 2>/dev/null || :
		done
		scope="$approved
$proposals/todo"
	fi
	printf '%s\n' "$scope" | tr '\n' '\000' | xargs -0 git -C "$tree" commit --quiet -m "ccnavi: 承認済みチケットを更新" -- || {
		printf 'ccnavi-push-approved: %s で承認済みチケットをコミットできない。\n' "$name" >&2
		printf 'fail\n' >>"$state"
		continue
	}
	case "$branch" in
	main | master | develop | release | release/*)
		printf 'ccnavi-push-approved: %s は %s の上に居るので push しません。送るかどうかは人が決めます。\n' \
			"$name" "$branch" >&2
		continue
		;;
	esac

	# push は落ちても巻き戻さない。コミットは残るので、人がもう一度送れる。
	if git -C "$tree" push --quiet -u origin "$branch" 2>/dev/null; then
		printf '承認済みチケットを %s へ送った（%s）。\n' "$branch" "$name"
	else
		printf 'ccnavi-push-approved: %s の push が通らなかった。手で送ってください（git push -u origin %s）。\n' \
			"$name" "$branch" >&2
		printf 'fail\n' >>"$state"
	fi
done

if [ ! -s "$state" ]; then
	printf '運ぶ承認済みチケットは無い。\n'
	exit 0
fi
grep -q '^fail$' "$state" && exit 1
exit 0

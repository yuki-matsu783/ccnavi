#!/bin/sh
# ccnavi-fetch — セッションの頭で、リモートに合わせるものを 2 つ取ってくる。
#
#   sh .ccnavi/scripts/ccnavi-fetch.sh
#
# 1 つめは**承認済みチケットとマーカー**。これらは親チケットのブランチに乗り、A の機械から
# push されて届く（設計 §9.2）。取ってこないと、B の機械は古い版で判定する。承認したのに
# 範囲が効かない、レビュー済みなのに止まったまま、という形になる。
#
# 2 つめは**ワークツリーの起点になるデフォルトブランチ**（`origin/HEAD` が指すもの。多くは
# `main`）。親のワークツリーは `ccnavi-git.sh worktree add <行き先> -b <名前> <統合先>` で
# 切り、起点は `<統合先>` の HEAD になる。手元の `main` が古いと、そこから切る枝も古いところから
# 伸びる。戻すときに fast-forward が通らず、承認済みチケットも古い版で判定することになる
# （ADR-0060）。
#
# **デフォルトブランチは、チェックアウトされていなくても進める。** ワークスペースルートが
# `main` 以外に居るセッションでは、上の 1 つめ（そのツリーがチェックアウトしているブランチを
# 進める）では `main` に届かない。届かないところが、そのまま起点の古さになる。
#
# 進めるのは fast-forward だけ。マージも rebase もしない。作業ツリーに未コミットの
# 変更があるツリーは触らない。そこに居るのは人か別のセッションの書きかけで、
# セッションの頭に走る hook が動かしてよいものではない（CLAUDE.md の「他セッションの
# 作業を踏まないために」）。進められなかったツリーは理由を 1 行で言う。
#
# 出力はモデルに届く。何も動かなかったときは黙る。毎回同じ行を返すと、
# セッションの頭の文脈がそれで埋まる。
#
# 終了コード: 常に 0。取ってこられないことは失敗ではない（オフラインでも作業は続く）。

set -u

# 共通部分。ワークスペースルートの探し方はここにある（設計 §11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

approved="${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}"
projects="${CCNAVI_PROJECTS:-projects}"

# 見つからなければ黙って終わる。セッションの頭に走るので、ここで止めても得るものが無い。
root=$(ccnavi_workspace) || exit 0

# 報せを組み立てる `$( )` の中に `case` は書けない（macOS の bash 3.2 が `)` を読み違える。
# tests/core/test_sh_portability.py）。`case` の要るものはここで関数にしておく。

# そのリポジトリのデフォルトブランチの名前。分からなければ 1 を返す。
#
# `origin/HEAD` は clone のときに置かれる。`git init` してから `remote add` した手元や、
# 古い clone には無いので、そのときは `origin/main`・`origin/master` の在る側に落とす。
# どちらも無ければ「分からない」。当てずっぽうで別のブランチを進めない。
ccnavi_fetch_default() {
	ccnavi_fd_head=$(git -C "$1" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || :)
	case "$ccnavi_fd_head" in
	origin/?*)
		printf '%s\n' "${ccnavi_fd_head#origin/}"
		return 0
		;;
	esac
	for ccnavi_fd_try in main master; do
		if git -C "$1" rev-parse --verify --quiet "refs/remotes/origin/$ccnavi_fd_try" >/dev/null 2>&1; then
			printf '%s\n' "$ccnavi_fd_try"
			return 0
		fi
	done
	return 1
}

# そのブランチをチェックアウトしているツリーの綴り。どこにも無ければ空。
#
# チェックアウトされているブランチの ref は付け替えない（索引と作業ツリーがずれる）。
# 在れば `merge --ff-only`、無ければ `update-ref` に分ける、その分け目を返す。
ccnavi_fetch_tree_of() {
	ccnavi_ft_want="refs/heads/$2"
	ccnavi_ft_path=""
	git -C "$1" worktree list --porcelain 2>/dev/null | while IFS= read -r ccnavi_ft_line; do
		case "$ccnavi_ft_line" in
		'worktree '*)
			ccnavi_ft_path="${ccnavi_ft_line#worktree }"
			;;
		'branch '*)
			if [ "${ccnavi_ft_line#branch }" = "$ccnavi_ft_want" ]; then
				printf '%s\n' "$ccnavi_ft_path"
				break
			fi
			;;
		esac
	done
}

# そのツリーを第 1 周が見るか。見るなら 0。
#
# 見るなら、デフォルトブランチもそこで済んでいる（結果が「進めなかった」でも、理由は
# そこで 1 行言っている）。第 2 周は黙って飛ばす。同じことを 2 度取ってこないためでもある。
ccnavi_fetch_seen() {
	[ -d "$1/$approved" ] || return 1
	git -C "$1" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1 || return 1
	return 0
}

# 承認済みチケットを持ちうるツリー。ワークスペース、プロジェクト、ワークツリー。
trees="$root"
for dir in "$root/$projects"/* "$root/.claude/worktrees"/*; do
	[ -d "$dir" ] || continue
	trees="$trees
$dir"
done

# デフォルトブランチを持つリポジトリ。ワークスペース自身と、プロジェクト。
#
# ワークツリーは数えない。元リポジトリと ref を共有しているので、元で進めれば届く。
#
# **`.ccnavi/approved/` は問わない。** 第 1 周が見るのは「承認済みチケットを持つツリー」だが、
# ここで欲しいのは「これから切るワークツリーの起点」で、いま何を持っているかとは別（ADR-0060）。
repos="$root"
for dir in "$root/$projects"/*; do
	[ -e "$dir/.git" ] || continue
	repos="$repos
$dir"
done

report=$(
	# 第 1 周。そのツリーがチェックアウトしているブランチを upstream まで進める。
	printf '%s\n' "$trees" | while IFS= read -r tree; do
		[ -n "$tree" ] || continue
		[ -d "$tree/$approved" ] || continue
		git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue

		branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
		[ -n "$branch" ] && [ "$branch" != "HEAD" ] || continue
		git -C "$tree" rev-parse --abbrev-ref "@{u}" >/dev/null 2>&1 || continue

		name=$(basename "$tree")
		git -C "$tree" fetch --quiet origin "$branch" 2>/dev/null || {
			printf '%s: リモートを取ってこられなかった。手元の版で判定する\n' "$name"
			continue
		}
		behind=$(git -C "$tree" rev-list --count "HEAD..@{u}" 2>/dev/null || echo 0)
		[ "$behind" = "0" ] && continue

		dirty=$(git -C "$tree" status --porcelain --untracked-files=no 2>/dev/null || :)
		if [ -n "$dirty" ]; then
			printf '%s: リモートが %s 件先に居るが、未コミットの変更があるので進めない\n' \
				"$name" "$behind"
			continue
		fi
		if git -C "$tree" merge --ff-only --quiet "@{u}" 2>/dev/null; then
			printf '%s: 承認済みチケットとマーカーを %s 件分だけ新しくした（%s）\n' "$name" "$behind" "$branch"
		else
			printf '%s: リモートと分岐しているので進めない。人が合流させること（%s）\n' \
				"$name" "$branch"
		fi
	done

	# 第 2 周。リポジトリごとに、ワークツリーの起点になるデフォルトブランチを進める。
	printf '%s\n' "$repos" | while IFS= read -r repo; do
		[ -n "$repo" ] || continue
		git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
		git -C "$repo" remote get-url origin >/dev/null 2>&1 || continue
		default=$(ccnavi_fetch_default "$repo") || continue
		name=$(basename "$repo")
		ref="refs/remotes/origin/$default"

		here=$(ccnavi_fetch_tree_of "$repo" "$default")
		if [ -n "$here" ] && ccnavi_fetch_seen "$here"; then
			continue
		fi

		git -C "$repo" fetch --quiet origin "$default" 2>/dev/null || {
			printf '%s: ワークツリーの起点になる %s を取ってこられなかった。手元の版から切ることになる\n' \
				"$name" "$default"
			continue
		}

		old=$(git -C "$repo" rev-parse --verify --quiet "refs/heads/$default" 2>/dev/null || :)
		if [ -z "$old" ]; then
			# 手元に無い。無いままでは `worktree add ... <統合先>` の起点に指せない。
			git -C "$repo" update-ref -m ccnavi-fetch "refs/heads/$default" "$ref" 2>/dev/null || continue
			git -C "$repo" branch --quiet --set-upstream-to "origin/$default" "$default" >/dev/null 2>&1 || :
			printf '%s: ワークツリーの起点になる %s が手元に無かったので、リモートの版で作った\n' \
				"$name" "$default"
			continue
		fi

		behind=$(git -C "$repo" rev-list --count "refs/heads/$default..$ref" 2>/dev/null || echo 0)
		[ "$behind" = "0" ] && continue
		ahead=$(git -C "$repo" rev-list --count "$ref..refs/heads/$default" 2>/dev/null || echo 0)
		if [ "$ahead" != "0" ]; then
			printf '%s: ワークツリーの起点になる %s がリモートと分岐している。人が合流させること\n' \
				"$name" "$default"
			continue
		fi

		if [ -n "$here" ]; then
			dirty=$(git -C "$here" status --porcelain --untracked-files=no 2>/dev/null || :)
			if [ -n "$dirty" ]; then
				printf '%s: ワークツリーの起点になる %s がリモートより %s 件古いが、未コミットの変更があるので進めない\n' \
					"$name" "$default" "$behind"
				continue
			fi
			git -C "$here" merge --ff-only --quiet "$ref" 2>/dev/null || {
				printf '%s: ワークツリーの起点になる %s を進められなかった。人が合流させること\n' \
					"$name" "$default"
				continue
			}
		else
			git -C "$repo" update-ref -m ccnavi-fetch "refs/heads/$default" "$ref" "$old" 2>/dev/null || {
				printf '%s: ワークツリーの起点になる %s を進められなかった。人が合流させること\n' \
					"$name" "$default"
				continue
			}
		fi
		printf '%s: ワークツリーの起点になる %s を %s 件分だけ新しくした\n' "$name" "$default" "$behind"
	done
)

[ -n "$report" ] || exit 0
printf '[ccnavi] 承認済みチケットとマーカーは親ブランチに乗って届き、ワークツリーの起点はデフォルトブランチになる。セッションの頭で取ってきた結果:\n'
printf '%s\n' "$report"
exit 0

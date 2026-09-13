#!/bin/sh
# ccnavi-fetch — セッションの頭で、親ブランチのリモートを取ってきて承認済みチケットを新しくする。
#
#   sh .claude/scripts/ccnavi-fetch.sh
#
# 承認済みチケットと印は親チケットのブランチに乗り、A の機械から push されて届く
# （設計 §9.2）。取ってこないと、B の機械は古い版で判定する。承認したのに範囲が
# 効かない、レビュー済みなのにゲートが閉じたまま、という形になる。
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

approved="${CCNAVI_APPROVED:-.ccnavi/tickets}"
projects="${CCNAVI_PROJECTS:-projects}"

# 見つからなければ黙って終わる。セッションの頭に走るので、ここで止めても得るものが無い。
root=$(ccnavi_workspace) || exit 0

# 承認済みチケットを持ちうるツリー。ワークスペース、プロジェクト、作業ツリー。
trees="$root"
for dir in "$root/$projects"/* "$root/.claude/worktrees"/*; do
	[ -d "$dir" ] || continue
	trees="$trees
$dir"
done

report=$(
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
			printf '%s: 承認済みチケットと印を %s 件分だけ新しくした（%s）\n' "$name" "$behind" "$branch"
		else
			printf '%s: リモートと分岐しているので進めない。人が合流させること（%s）\n' \
				"$name" "$branch"
		fi
	done
)

[ -n "$report" ] || exit 0
printf '[ccnavi] 承認済みチケットと印は親ブランチに乗って届く。セッションの頭で取ってきた結果:\n'
printf '%s\n' "$report"
exit 0

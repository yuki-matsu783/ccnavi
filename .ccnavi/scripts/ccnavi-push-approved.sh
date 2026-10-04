#!/bin/sh
# ccnavi-push-approved 承認済みチケットの置き場（と、承認で todo/ から消えた提案、取り込んで消えた
# フローの下書き）だけをコミットし、
# 保護されたブランチでなければ push する。
#
#   sh .ccnavi/scripts/ccnavi-push-approved.sh [<親>...]
#
# 承認はしない。承認済みチケットは置かれただけでは他の機械に届かない（設計 9.2）ので、置いたあとに
# コミットして push するのがこの sh。端末の承認は ccnavi-agree.sh が、ボードの承認とフローの保存は端末に送った 1 行が、
# ユーザの判断の入口（ccnavi-review.sh chat・config-synced・close-early）が、それぞれ最後にこの sh を呼ぶ。
# ユーザの判断を溜めずにその場で送るためで、送れなければ次の C1 が止まり、この sh の打ち直しを案内する。
# 対になるのはセッションの頭に取ってくる ccnavi-fetch.sh。
#
# 取り込み済みの親子のチケット（origin があり、親子のチケットの取り込み状態が present。chat だけのものを除く）のワークツリーは、
# C1 と同じ手順でコミットして push する。取り込んでから送るので、Chrome での承認と重なっても push が拒まれにくい。
# 手順は、ロック（C1 の中からの入れ子を許す）→ 途中の操作の確認 → 取り込み（ccnavi-sync.sh）→
# 置き場（承認済みと、レビュー待ちの review/ と、承認で消えた todo/ の提案と、取り込んで消えた flows/ の下書き）を commit --only → push → 落ちたように見えたら届いたかを ls-remote で確かめる。
# push が落ちてもコミットは残す（ユーザが打ち直せる）。取り込み済みかは実行ファイル（`c1 family`）に聞く。
# 答えない実行ファイルで親子のチケットの取り込み状態があれば、コミットせずに止める。
#
# <親> を並べると、その親子のチケットだけをコミットして push する。取り込み済みでない親子のチケットは
# コミットしない（今のまま、ユーザがコミットする）。省けば今どおり、置き場に変更のあるツリー全部。
#
# 数えるツリーは、ワークスペース、$CCNAVI_PROJECTS（既定 projects）の下、.claude/worktrees の下。
# 置き場は $CCNAVI_TICKETS_APPROVED（既定 .ccnavi/approved）。承認は提案を
# $CCNAVI_TICKETS_PROPOSAL（既定 wip/proposals）の todo/ から動かす（コピーは作らない）ので、
# そこで追跡されていたファイルの削除も同じコミットに入れる。todo/ の書きかけ（未追跡・編集中）はコミットしない。
# 同じく、ボードのフロー編集画面が取り込んだ下書き（$CCNAVI_TICKETS_PROPOSAL の flows/）を
# フローの保存のあとに消すので、追跡されていた下書きの削除だけをコミットする。未追跡の下書きと、書き直された
# 下書き（消えていない）はコミットしない。`ccnavi c1 sort` の置き場には足さず、消えたものだけをここで拾う。
#
# - コミットはパスを限る。`-a` も `add -A` も使わない。他人の書きかけをコミットしない
# - シンボリックリンクは辿らない。置き場（projects/ や .claude/worktrees/）そのものも、その下の
#   1 件ずつも。辿るとワークスペースの外のリポジトリにコミットして push する
# - ブランチの上に居ない（detached）ツリーは名指しして処理しない。失敗には数えない
# - main / master / develop / release / release/* と、そのリポジトリの統合先（ccnavi-common.sh の
#   ccnavi_integration。決まらなければ固定のリストだけ）はコミットだけして push しない
# - 1 本のツリーで add・commit・push が落ちても、他のツリーはコミットして push する
#
# 終了コード: 0 コミットするものが無い・全部コミットした（push しなかったブランチ、処理しなかったツリーを含む） /
#           1 ステージかコミットできなかったツリーか、push が落ちたツリーが 1 つ以上ある /
#           2 引数の誤り・ワークスペースルートが見つからない・一時ファイルが作れない

set -eu

. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-push-approved.sh [<親>...]

  承認済みチケットの置き場に変更があるツリーごとに、その置き場だけをコミットし、
  保護されたブランチでなければ push する。取り込み済みの親子のチケットの親のワークツリーは、
  取り込んでから送る（C1 と同じ手順）。<親> を並べるとその親子のチケットだけ。
USAGE
}

case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
esac
for want in ${1+"$@"}; do
	if ! ccnavi_is_ident "$want"; then
		printf 'ccnavi-push-approved: %s は親の識別子の形ではありません。\n' "$want" >&2
		usage >&2
		exit 2
	fi
done

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
# 落として空になるパス（`/`）と `.` は、ワークスペースルートそのものを指す。置き場なら
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
# ボードのフロー編集画面が保存の途中で置く一時ファイル（flows/ の下の `.<名前>.<番号>.tmp`）。
# 落ちて残ってもコミットしない。書きかけの中身をユーザの手順書としてコミットしないため。
skip_temp=":(exclude)$approved/flows/.*.tmp"

# ツリーごとの結果を subshell (while はパイプの右側なので別プロセス) の外へ持ち出すための一時ファイル。
state=$(mktemp "${TMPDIR:-/tmp}/ccnavi-push-approved.XXXXXX") || {
	printf 'ccnavi-push-approved: 一時ファイルが作れません。\n' >&2
	exit 2
}
trap 'rm -f "$state"; ccnavi_c1_end' EXIT
trap 'rm -f "$state"; ccnavi_c1_end; exit 130' INT TERM HUP

# ---- 取り込み済みの親子のチケットは C1 と同じ手順でコミットして push する
ccnavi_log_root="$root"
ccnavi_c1_root="$root"
ccnavi_c1_label=ccnavi-push-approved
ccnavi_c1_sh="$(dirname "$0")"
if bin=$(ccnavi_bin "$root"); then
	ccnavi_c1_exe() { "$bin" --root "$root" "$@"; }
elif [ -f "$root/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
	ccnavi_c1_exe() { (cd "$root" && uv run --quiet python -m ccnavi --root "$root" "$@"); }
else
	ccnavi_c1_exe() { return 1; }
fi
tab=$(printf '\t')

# 置き場の変更のパス（承認済み、レビュー待ちの review/、承認で消えた todo/ の提案、取り込んで消えた
# flows/ の下書き）を ccnavi_c1_tmp/carry に。
# 書きかけの一時ファイル（fsio の `.<名前>.<一意>.part*`、フローの保存の `flows/.*.tmp`、configsync の
# `*.ccnavi-sync`）はコミットしない。
carry_paths() {
	: >"$ccnavi_c1_tmp/carry"
	git -C "$1" -c core.quotepath=false status --porcelain -z --untracked-files=all --no-renames \
		-- "$approved" "$proposals/review" 2>/dev/null | tr '\000' '\n' |
		sed -n 's/^...//p' >"$ccnavi_c1_tmp/carry-all" || :
	git -C "$1" ls-files --deleted -z -- "$proposals/todo" "$proposals/flows" 2>/dev/null | tr '\000' '\n' \
		>>"$ccnavi_c1_tmp/carry-all" || :
	grep -v -E '(^|/)\.[^/]*\.part(\.[^/]*)?$|(^|/)flows/\.[^/]*\.tmp$|\.ccnavi-sync$' \
		"$ccnavi_c1_tmp/carry-all" >"$ccnavi_c1_tmp/carry" || :
}

# 取り込み済みの親子のチケット 1 つをコミットして push する。ccnavi_c1_family を済ませた後に呼ぶ。0 送った（push するものが無いを含む）/ 1 落ちた
#
# 取り込み状態の鍵とロックは識別子（ccnavi_c1_family_id）、ref・push・ls-remote と取り込み状態の branch は
# 親のブランチ名（ccnavi_c1_branch。親チケットの branch:、無ければ識別子）。
carry_family() {
	cf_p="$ccnavi_c1_family_id"
	cf_b="${ccnavi_c1_branch:-$ccnavi_c1_family_id}"
	cf_tree="$ccnavi_c1_tree"
	cf_rc=0
	ccnavi_lock_take "$root" "$ccnavi_c1_repo" "$cf_p" "$(ccnavi_c1_number "${CCNAVI_LOCK_WAIT:-}" 120)" || cf_rc=$?
	if [ "$cf_rc" -ne 0 ]; then
		if ccnavi_lock_long "$(ccnavi_state "$root")/locks/$ccnavi_c1_repo/$cf_p"; then
			printf 'ccnavi-push-approved: %s のロックが 10 分を超えて取られたままになっている。持ち主はまだ動いているので強制取得しない。終わるのを待つか、持ち主をユーザが確かめてください。%s\n' \
				"$cf_p" "$(ccnavi_lock_describe "$(ccnavi_state "$root")/locks/$ccnavi_c1_repo/$cf_p")" >&2
		else
			printf 'ccnavi-push-approved: %s のロックを他の操作が持っている。終わってから打ち直してください。\n' "$cf_p" >&2
		fi
		return 1
	fi
	cf_busy=$(ccnavi_c1_busy "$cf_tree")
	if [ -n "$cf_busy" ]; then
		printf 'ccnavi-push-approved: %s の親のワークツリーに途中の操作（%s）がある。済ませてから打ち直してください。\n' "$cf_p" "$cf_busy" >&2
		ccnavi_lock_drop
		return 1
	fi
	# 取り込んでから送る（Chrome の承認と重なっても push が拒まれにくい）。衝突したら取りやめてユーザの対応に切り替える。
	cf_rc=0
	sh "$(dirname "$0")/ccnavi-sync.sh" "$cf_p" </dev/null >"$ccnavi_c1_tmp/sync" 2>&1 || cf_rc=$?
	sed 's/^/  /' "$ccnavi_c1_tmp/sync" >&2
	cf_record=$(ccnavi_family_record "$root" "$ccnavi_c1_repo" "$cf_p")
	if [ "$cf_rc" -ne 0 ] || [ "$(ccnavi_record_get "$cf_record" state)" != present ]; then
		printf 'ccnavi-push-approved: %s を取り込めなかった（上の ccnavi-sync.sh の文面）。ユーザの判断はまだ送っていない。直してから打ち直してください。\n' "$cf_p" >&2
		ccnavi_lock_drop
		return 1
	fi
	carry_paths "$cf_tree"
	if [ -s "$ccnavi_c1_tmp/carry" ]; then
		cf_base=$(git -C "$cf_tree" rev-parse HEAD)
		while IFS= read -r cf_path; do
			[ -n "$cf_path" ] || continue
			git -C "$cf_tree" add -A -- ":(literal)$cf_path" 2>/dev/null || :
		done <"$ccnavi_c1_tmp/carry"
		sed 's/^/:(literal)/' "$ccnavi_c1_tmp/carry" | tr '\n' '\000' >"$ccnavi_c1_tmp/pathspec"
		# C1 と同じく、ユーザの hook は実行せず、署名などで止まらないようタイムアウトで切る。
		if ! ccnavi_git_timed "$(ccnavi_c1_number "${CCNAVI_C1_COMMIT_TIMEOUT:-}" 60)" "$ccnavi_c1_tmp/err" "$cf_tree" \
			commit --quiet --only --no-verify -m "ccnavi: 承認済みチケットを更新" \
			--pathspec-from-file="$ccnavi_c1_tmp/pathspec" --pathspec-file-nul >"$ccnavi_c1_tmp/out"; then
			cat "$ccnavi_c1_tmp/out" >>"$ccnavi_c1_tmp/err"
			printf 'ccnavi-push-approved: %s で承認済みチケットをコミットできない（%s）。\n' "$cf_p" "$(ccnavi_git_refusal "$ccnavi_c1_tmp/err")" >&2
			ccnavi_c1_tree="$cf_tree"
			ccnavi_c1_unstage "$ccnavi_c1_tmp/carry" "$cf_base"
			ccnavi_lock_drop
			return 1
		fi
	fi
	cf_head=$(git -C "$cf_tree" rev-parse HEAD)
	if [ "$cf_head" = "$(git -C "$cf_tree" rev-parse --verify -q "refs/remotes/origin/$cf_b" 2>/dev/null || :)" ]; then
		printf '%s: コミットして push するものは無い。\n' "$cf_p"
		ccnavi_lock_drop
		return 0
	fi
	cf_timeout=$(ccnavi_c1_number "${CCNAVI_C1_TIMEOUT:-}" 60)
	if ccnavi_git_timed "$cf_timeout" "$ccnavi_c1_tmp/push-err" "$cf_tree" \
		push --quiet origin "refs/heads/$cf_b:refs/heads/$cf_b" >/dev/null ||
		{ ccnavi_git_timed "$cf_timeout" "$ccnavi_c1_tmp/ls-err" "$cf_tree" \
			ls-remote origin "refs/heads/$cf_b" >"$ccnavi_c1_tmp/ls" &&
			grep -F -x -q -- "$cf_head${tab}refs/heads/$cf_b" "$ccnavi_c1_tmp/ls"; }; then
		ccnavi_record_write "$cf_record" remote origin branch "$cf_b" sha "$cf_head" \
			fetched_at "$(ccnavi_record_get "$cf_record" fetched_at)" state present reason "" || :
		printf '承認済みチケットを、取り込んでから %s へ送った。\n' "$cf_b"
		ccnavi_lock_drop
		return 0
	fi
	# 落ちてもコミットは残す（ユーザが打ち直せる）。C1 は未送信を見つけて止まり、これを打ち直すよう言う。
	printf 'ccnavi-push-approved: %s の push が通らなかった（%s）。コミットは残した。接続を戻して sh %s/ccnavi-push-approved.sh %s を打ち直してください。\n' \
		"$cf_p" "$(head -n 1 "$ccnavi_c1_tmp/push-err" 2>/dev/null)" "$(dirname "$0")" "$cf_p" >&2
	ccnavi_lock_drop
	return 1
}

# 親子のチケットを名指しされたとき。取り込み済みならコミットして push し、そうでなければ何もしない（今のまま）。
if [ "$#" -gt 0 ]; then
	named_rc=0
	for want in "$@"; do
		ccnavi_c1_family "$want"
		case "$ccnavi_c1_target" in
		yes) carry_family || named_rc=1 ;;
		stop)
			ccnavi_c1_refuse
			named_rc=1
			;;
		*)
			printf '%s: 取り込み済みの親子のチケットでない（%s）。ここではコミットも push もしない（今のまま、ユーザがコミットする）。\n' \
				"$want" "${ccnavi_c1_why:-対象外}"
			;;
		esac
	done
	exit "$named_rc"
fi

# ワークスペースルートから rel を 1 段ずつ下り、シンボリックリンクの段があれば 0。
# その段（ルートからのパス）を linked に残す。`.claude` だけがリンクでも見逃さない。
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
		# glob が何にも当たらなければパターンがそのまま残るので、-d で落とす。
		[ -d "$dir" ] || continue
		trees="$trees
$dir"
	done
done

# 一時の置き場は先に作る（下のループはパイプの右側の別プロセスで、そこで作ると後始末が届かない）。
ccnavi_c1_scratch || {
	printf 'ccnavi-push-approved: 一時ディレクトリが作れません。\n' >&2
	exit 2
}
printf '%s\n' "$trees" | while IFS= read -r tree; do
	[ -n "$tree" ] || continue
	[ -d "$tree/$approved" ] || continue
	git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
	changed=$(git -C "$tree" status --porcelain -- "$approved" "$skip_temp" 2>/dev/null || :)
	[ -n "$changed" ] || continue

	# 何か 1 つでもコミットする対象があったことの記録。detached で処理しなくても「無い」とは言わない。
	printf 'seen\n' >>"$state"

	name=$(basename "$tree")
	branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
		printf 'ccnavi-push-approved: %s はブランチの上に居ない。承認済みチケットは手でコミットしてください。\n' \
			"$name" >&2
		continue
	fi

	# 取り込み済みの親子のチケットの親のワークツリー（ディレクトリ名 = 識別子。ブランチは親チケットの branch:、
	# 無ければ識別子と同じ名前）は、取り込んでから送る。
	case "$tree" in
	"$root/.claude/worktrees/"*)
		ccnavi_c1_family "$name"
		if [ "$ccnavi_c1_target" != no ] && [ "$ccnavi_c1_family_id" != "$name" ]; then
			# 取り込み済みの親子のチケットの子のワークツリー。チケットとマーカーは親のワークツリーに置くので、
			# 子のツリーの置き場の変更はコミットしない（子のブランチはリモートに出さない）。
			printf 'ccnavi-push-approved: %s は親子のチケット %s の子のワークツリー。子のツリーの置き場の変更はコミットしない（チケットは親のワークツリーに置く）。ユーザが中身を確かめる。\n' \
				"$name" "$ccnavi_c1_family_id" >&2
			printf 'fail\n' >>"$state"
			continue
		fi
		case "$ccnavi_c1_target" in
		yes)
			if grep -F -x -q -- "$ccnavi_c1_family_id" "$ccnavi_c1_tmp/carried" 2>/dev/null; then
				continue
			fi
			printf '%s\n' "$ccnavi_c1_family_id" >>"$ccnavi_c1_tmp/carried"
			carry_family </dev/null || printf 'fail\n' >>"$state"
			continue
			;;
		stop)
			ccnavi_c1_refuse
			printf 'fail\n' >>"$state"
			continue
			;;
		esac
		;;
	esac

	git -C "$tree" add -- "$approved" "$skip_temp" || {
		printf 'ccnavi-push-approved: %s で承認済みチケットをステージできない。\n' "$name" >&2
		printf 'fail\n' >>"$state"
		continue
	}
	# 承認で todo/ から消えた提案。追跡されていたものの削除だけを入れる（`ls-files --deleted`）。
	# 未追跡の下書きも、編集中の提案も入れない。消えたものが無ければ pathspec にも足さない
	# （git に知られていないパスを pathspec に並べると commit が落ちる）。
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
	# 取り込んで消えたフローの下書き。追跡されていたものの削除だけを、1 件ずつのパスで入れる。
	# 置き場ごと pathspec に並べると、書き直されて残っている下書きまでコミットに入るため。
	drafts=$(git -C "$tree" ls-files --deleted -z -- "$proposals/flows" 2>/dev/null | tr '\000' '\n' || :)
	if [ -n "$drafts" ]; then
		printf '%s\n' "$drafts" | while IFS= read -r removed; do
			[ -n "$removed" ] || continue
			git -C "$tree" add -u -- ":(literal)$removed" 2>/dev/null || :
		done
		scope="$scope
$(printf '%s\n' "$drafts" | sed '/^$/d; s/^/:(literal)/')"
	fi
	printf '%s\n' "$scope" | tr '\n' '\000' | xargs -0 git -C "$tree" commit --quiet -m "ccnavi: 承認済みチケットを更新" -- || {
		printf 'ccnavi-push-approved: %s で承認済みチケットをコミットできない。\n' "$name" >&2
		printf 'fail\n' >>"$state"
		continue
	}
	case "$branch" in
	main | master | develop | release | release/*)
		printf 'ccnavi-push-approved: %s は %s の上に居るので push しません。送るかどうかはユーザが決めます。\n' \
			"$name" "$branch" >&2
		continue
		;;
	esac
	# 固定のリストに無い名前の統合先（develop-v1.0.0 など）も送らない。名前は ccnavi-fetch.sh・
	# ccnavi-git.sh と同じ順（CCNAVI_INTEGRATION_BRANCH → ccnavi-sync.sh の取り込み結果 → origin/HEAD →
	# origin/main・master）で、そのツリーが属するリポジトリについて決める。決まらなければ固定のリストだけ。
	integ=$(ccnavi_integration "$tree" "$root") || integ=""
	if [ -n "$integ" ] && [ "$branch" = "$integ" ]; then
		printf 'ccnavi-push-approved: %s は統合先 %s の上に居るので push しません。送るかどうかはユーザが決めます。\n' \
			"$name" "$branch" >&2
		continue
	fi

	# push は落ちても巻き戻さない。コミットは残るので、ユーザがもう一度送れる。
	if git -C "$tree" push --quiet -u origin "$branch" 2>/dev/null; then
		printf '承認済みチケットを %s へ送った（%s）。\n' "$branch" "$name"
	else
		printf 'ccnavi-push-approved: %s の push が通らなかった。手で送ってください（git push -u origin %s）。\n' \
			"$name" "$branch" >&2
		printf 'fail\n' >>"$state"
	fi
done

if [ ! -s "$state" ]; then
	printf 'コミットして push する承認済みチケットは無い。\n'
	exit 0
fi
grep -q '^fail$' "$state" && exit 1
exit 0

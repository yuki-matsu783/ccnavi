#!/bin/sh
# ccnavi-fetch セッションの頭で、リモートに合わせるものを 2 つ取ってくる。
#
#   sh .ccnavi/scripts/ccnavi-fetch.sh
#
# 1 つめは**承認済みチケットとマーカー**。これらは親チケットのブランチにコミットされ、A の機械から
# push されて届く（設計 9.2）。取ってこないと、B の機械は古い版で判定する。承認したのに
# 範囲が反映されない、レビュー済みなのに止まったまま、という形になる。
#
# 2 つめは**ワークツリーの起点になる統合先**（CCNAVI_INTEGRATION_BRANCH、無ければ ccnavi-sync.sh が
# 同期状態に書いた名前、無ければデフォルトブランチ＝`origin/HEAD` が指すもの。多くは `main`）。親のワークツリーは `ccnavi-git.sh worktree add <行き先> -b <名前> <統合先>` で
# 切り、起点は `<統合先>` の HEAD になる。手元の `main` が古いと、そこから切るブランチも古いコミットから
# 始まる。戻すときに fast-forward が通らず、承認済みチケットも古い版で判定することになる
# （ADR-0060）。
#
# **デフォルトブランチは、チェックアウトされていなくても進める。** ワークスペースルートが
# `main` 以外に居るセッションでは、上の 1 つめ（そのツリーがチェックアウトしているブランチを
# 進める）では `main` に届かない。
#
# 進めるのは fast-forward だけ。マージも rebase もしない。作業ツリーに未コミットの
# 変更があるツリーは触らない。そこに居るのは人か別のセッションの書きかけで、
# セッションの頭に走る hook が動かしてよいものではない（docs/claude/worktree.md の「他セッションの
# 作業を踏まないために」）。進められなかったツリーは理由を 1 行で言う。
#
# 出力はモデルに届く。何も動かなかったときは何も出さない。毎回同じ行を返すと、
# セッションの頭の文脈がそれで埋まる。
#
# **待たせない。** 認証を尋ねる画面を出させず（GIT_TERMINAL_PROMPT・GCM_INTERACTIVE）、
# fetch 1 回にタイムアウトを付けて CCNAVI_FETCH_TIMEOUT 秒（既定 15）で切る。hook の上限（60 秒）に
# 当たると、報せごと捨てられる。一度落ちた origin には、この回ではもう取りに行かない。
#
# **認証で落ちたときは、そう言う。** 尋ねないので、資格情報が無いか切れていると毎回落ちる。
# オフラインと同じ 1 行では、人は理由を調べることになる。認証は人が端末で打つ git（承認の
# sh を含む）で一度済ませれば保存され、次のセッションから hook の fetch も通る。見分けは
# git の文言に頼るので、LC_ALL=C で英語に揃えてから見る。見分けられなければ、ただの
# 「取ってこられなかった」に戻るだけ。
#
# **取り込み済みの親子チケットは早送りだけ**（ADR-0093 の 4.2。段階 2b）。親のワークツリー（`.claude/worktrees/<P>`
# で、ディレクトリ名 = ブランチ名、親の承認済みチケットか提案があり、同期状態がある）は、ロックを 1 回だけ試し
# （取れなければ早送りしない。待たない）、`origin/<P>` の祖先なら `merge --ff-only` する。書きかけとの重なりは
# git に任せ、拒まれたら重なったパスを言う。分かれていれば merge はせず「取り込みが要る」と 1 行言う。
# 取り込み（merge）・消えたかの確かめ・同期状態の書き出しは手で打つ ccnavi-sync.sh の仕事で、ここはしない。
# 開始から CCNAVI_FETCH_BUDGET 秒（既定 45）を過ぎたら、残りの fetch と早送りはせずに名指しする。
# fetch 1 回のタイムアウトも枠の残りより長くしない（hook の上限は 60 秒）。
#
# **「リモートにその ref が無い」で落ちた fetch は、その origin を落ちたものに数えない。** 数えると、
# 同じ origin の統合先の取り込みまで行われなくなる。消えたかどうかはここでは決めず、ccnavi-sync.sh に回す。
#
# 終了コード: 常に 0。取ってこられないことは失敗ではない（オフラインでも作業は続く）。

set -u

# 共通部分。ワークスペースルートの探し方はここにある（設計 11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

approved="${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}"
projects="${CCNAVI_PROJECTS:-projects}"

# 見つからなければ何も出さずに終わる。セッションの頭に走るので、ここで止めても得るものが無い。
root=$(ccnavi_workspace) || exit 0

# 認証を尋ねない。hook には端末が無く、尋ねれば落ちるか、画面を開いて誰かが閉じるまで待つ。
GIT_TERMINAL_PROMPT=0
GCM_INTERACTIVE=never
export GIT_TERMINAL_PROMPT GCM_INTERACTIVE

limit="${CCNAVI_FETCH_TIMEOUT:-15}"
case "$limit" in
'' | *[!0-9]*) limit=15 ;;
esac
# 早送りの時間の枠（秒）。sh の開始から数える。
budget="${CCNAVI_FETCH_BUDGET:-45}"
case "$budget" in
'' | *[!0-9]*) budget=45 ;;
esac
started=$(date +%s)
ccnavi_log_root="$root"

# 落ちた origin の URL を書いておく。同じ origin には取りに行かない。周はパイプの中（サブシェル）で
# 回るので、変数では渡らない。
scratch=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-fetch) || exit 0
# dash は EXIT の trap を INT・TERM・HUP で走らせないので、そちらにも置く。
fetch_cleaned=""
fetch_cleanup() {
	[ -z "$fetch_cleaned" ] || return 0
	fetch_cleaned=yes
	ccnavi_lock_drop
	rm -rf "$scratch"
}
trap 'fetch_cleanup' EXIT
trap 'fetch_cleanup; exit 130' INT TERM HUP
: >"$scratch/failed"

# 認証で落ちたときに git（と資格情報の仕組み）が出す文言。https・ssh・GitHub・GitLab。
auth_failed='Authentication failed|could not read (Username|Password)|terminal prompts disabled'
auth_failed="$auth_failed"'|Invalid username or password|HTTP Basic: Access denied'
auth_failed="$auth_failed"'|Permission denied \(publickey|returned error: 40[13]'

# 時間の枠の残り（秒）。負なら 0。
ccnavi_fetch_left() {
	ccnavi_fl_now=$(date +%s)
	ccnavi_fl_left=$((budget - (ccnavi_fl_now - started)))
	[ "$ccnavi_fl_left" -gt 0 ] || ccnavi_fl_left=0
	printf '%s\n' "$ccnavi_fl_left"
}

# origin へ 1 本取りに行く。取れたら 0、落ちたら 1、認証で落ちたら 3。取りに行かなかったら 2。
# リモートにその ref が無くて落ちたら 4（その origin を落ちたものに数えない）。
# 時間の枠（CCNAVI_FETCH_BUDGET、既定 45 秒）を過ぎていたら取りに行かずに 5。
#
# タイムアウト監視（ccnavi_git_timed）が limit 秒か枠の残りの短い方で切る。hook の上限（60 秒）を超えないため。
# 単一ブランチの clone でも origin/<ブランチ> が進むよう、行き先を書いて取る（sh の中の git。
# ccnavi-git.sh の入口の refspec の拒否とは別の話）。
ccnavi_fetch_git() {
	ccnavi_fg_url=$(git -C "$1" remote get-url origin 2>/dev/null || :)
	if [ -n "$ccnavi_fg_url" ] && grep -qxF -- "$ccnavi_fg_url" "$scratch/failed"; then
		return 2
	fi
	ccnavi_fg_left=$(ccnavi_fetch_left)
	[ "$ccnavi_fg_left" -gt 0 ] || return 5
	ccnavi_fg_limit="$limit"
	[ "$ccnavi_fg_left" -lt "$ccnavi_fg_limit" ] && ccnavi_fg_limit="$ccnavi_fg_left"
	ccnavi_fg_rc=0
	ccnavi_git_timed "$ccnavi_fg_limit" "$scratch/err" "$1" \
		fetch --quiet origin "+refs/heads/$2:refs/remotes/origin/$2" >/dev/null || ccnavi_fg_rc=$?
	if [ "$ccnavi_fg_rc" -ne 0 ]; then
		grep -qi "couldn't find remote ref" "$scratch/err" 2>/dev/null && return 4
		printf '%s\n' "$ccnavi_fg_url" >>"$scratch/failed"
		grep -qiE "$auth_failed" "$scratch/err" 2>/dev/null && return 3
		return 1
	fi
	return 0
}

# 取りに行く。取れたら 0。<ツリー> <ブランチ> <落ちたときの 1 行> [<リモートに無いときの 1 行>]
#
# 落ちたときの 1 行は、その origin で初めて落ちたときだけ出す。同じ origin の 2 件目は
# 取りに行かず、何も出さない。分け方に要る判定を、報せの `$( )` の外に置くための関数。
ccnavi_fetch_or_note() {
	ccnavi_fetch_git "$1" "$2"
	ccnavi_fn_rc=$?
	[ "$ccnavi_fn_rc" -eq 0 ] && return 0
	[ "$ccnavi_fn_rc" -eq 2 ] && return 1
	if [ "$ccnavi_fn_rc" -eq 5 ]; then
		printf '%s: 時間の枠（%s 秒）を過ぎたので %s を取りに行かなかった。後で sh %s/.ccnavi/scripts/ccnavi-sync.sh を打つか、もう一度セッションを始めてください\n' \
			"$(basename "$1")" "$budget" "$2" "$root"
		return 1
	fi
	if [ "$ccnavi_fn_rc" -eq 4 ]; then
		printf '%s\n' "${4:-$3}"
		return 1
	fi
	printf '%s\n' "$3"
	[ "$ccnavi_fn_rc" -eq 3 ] && printf '%s\n' "  認証で落ちた（資格情報が無いか、切れているか、権限が無い）。hook は資格情報の入力を求めない。利用者に端末で一度 'git fetch origin' を打って認証を済ませてもらえば、次のセッションから通る"
	return 1
}

# 報せを組み立てる `$( )` の中に `case` は書けない（macOS の bash 3.2 が `)` を読み違える。
# tests/core/test_sh_portability.py）。`case` の要るものはここで関数にしておく。

# そのリポジトリのデフォルトブランチの名前。分からなければ 1 を返す。
#
# `origin/HEAD` は clone のときに置かれる。`git init` してから `remote add` した手元や、
# 古い clone には無いので、そのときは `origin/main`・`origin/master` の在る側を使う。
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

# ワークツリーの起点にするブランチ（統合先。ADR-0093 の D30。段階 2b のレビューの決定 B6）。
#
# 環境変数 CCNAVI_INTEGRATION_BRANCH（SessionStart には settings.local.json の env も渡る）、
# 無ければ ccnavi-sync.sh が同期状態（sync/<リポジトリ>/integration/head）に書いた名前、
# 無ければホストのデフォルトブランチ（ccnavi_fetch_default）。
ccnavi_fetch_integration() {
	if [ -n "${CCNAVI_INTEGRATION_BRANCH:-}" ]; then
		printf '%s\n' "$CCNAVI_INTEGRATION_BRANCH"
		return 0
	fi
	ccnavi_fi_key=$(ccnavi_repo_key "$1" "$root")
	ccnavi_fi_name=$(ccnavi_record_get "$(ccnavi_state "$root")/sync/$ccnavi_fi_key/integration/head" branch)
	if [ -n "$ccnavi_fi_name" ]; then
		printf '%s\n' "$ccnavi_fi_name"
		return 0
	fi
	ccnavi_fetch_default "$1"
}

# そのブランチをチェックアウトしているツリーのパス。どこにも無ければ空。
#
# チェックアウトされているブランチの ref は付け替えない（索引と作業ツリーが食い違う）。
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
# そこで 1 行言っている）。第 2 周はそのツリーを見ず、何も出さない。同じことを 2 度取ってこないためでもある。
ccnavi_fetch_seen() {
	[ -d "$1/$approved" ] || return 1
	git -C "$1" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1 || return 1
	return 0
}

# そのツリーが、取り込み済みの親子チケットの親のワークツリーか（ADR-0093 の 4.2）。<ツリー> <名前> <ブランチ>
#
# `.claude/worktrees/` の直下で、ディレクトリ名 = ブランチ名、親の承認済みチケットか提案があり、同期状態がある。
# 当たらないツリーは今までどおり（未コミットがあれば進めない）。他セッションのワークツリーを動かさないため、
# 条件は全部満たすときだけ。
ccnavi_fetch_family() {
	case "$1" in
	"$root"/.claude/worktrees/*) ;;
	*) return 1 ;;
	esac
	[ "$2" = "$3" ] || return 1
	[ -L "$1" ] && return 1
	ccnavi_parent_tree "$1" "$2" || return 1
	ccnavi_ff_key=$(ccnavi_repo_key "$1" "$root")
	[ -f "$(ccnavi_family_record "$root" "$ccnavi_ff_key" "$2")" ] || return 1
	return 0
}

# 取り込み済みの親子チケットを早送りする（ADR-0093 の 4.2「SessionStart の早送り」）。<ツリー> <P>
#
# merge はしない。書きかけとの重なりは git に任せる（拒まれたら重なったパスを言う）。途中の状態
# （MERGE_HEAD）を残さないのが早送りだけにした理由で、hook の時間の枠で切られても書きかけが残らない。
ccnavi_fetch_forward() {
	ccnavi_fw_key=$(ccnavi_repo_key "$1" "$root")
	ccnavi_fetch_or_note "$1" "$2" \
		"${2}: リモートを取ってこられなかった。手元の版で判定する" \
		"${2}: 親のブランチをリモートから取ってこられなかった（リモートに無い）。sh ${root}/.ccnavi/scripts/ccnavi-sync.sh ${2} で確かめてください" ||
		return 0
	ccnavi_fw_now=$(date +%s)
	if [ "$((ccnavi_fw_now - started))" -ge "$budget" ]; then
		printf '%s: 時間の枠（%s 秒）を過ぎたので早送りを飛ばした。sh %s/.ccnavi/scripts/ccnavi-sync.sh %s で取り込んでください\n' \
			"$2" "$budget" "$root" "$2"
		return 0
	fi
	ccnavi_fw_remote=$(git -C "$1" rev-parse --verify --quiet "refs/remotes/origin/$2^{commit}" 2>/dev/null || :)
	ccnavi_fw_local=$(git -C "$1" rev-parse --verify --quiet "HEAD^{commit}" 2>/dev/null || :)
	[ -n "$ccnavi_fw_remote" ] && [ -n "$ccnavi_fw_local" ] || return 0
	[ "$ccnavi_fw_remote" = "$ccnavi_fw_local" ] && return 0
	git -C "$1" merge-base --is-ancestor "$ccnavi_fw_remote" "$ccnavi_fw_local" 2>/dev/null && return 0
	if ! git -C "$1" merge-base --is-ancestor "$ccnavi_fw_local" "$ccnavi_fw_remote" 2>/dev/null; then
		printf '%s: リモートと分かれているので、取り込みが要る（sh %s/.ccnavi/scripts/ccnavi-sync.sh %s）\n' "$2" "$root" "$2"
		return 0
	fi
	# ロックは 1 回だけ試す（待たない）。取れなければ他の操作の最中なので早送りしない。
	if ! ccnavi_lock_take "$root" "$ccnavi_fw_key" "$2" 0; then
		printf '%s: 他の操作の最中なので進めなかった。終わってから sh %s/.ccnavi/scripts/ccnavi-sync.sh %s で取り込んでください\n' "$2" "$root" "$2"
		return 0
	fi
	ccnavi_fw_behind=$(git -C "$1" rev-list --count "$ccnavi_fw_local..$ccnavi_fw_remote" 2>/dev/null || echo '?')
	if LC_ALL=C git -C "$1" merge --ff-only --quiet "$ccnavi_fw_remote" </dev/null >/dev/null 2>"$scratch/ff"; then
		printf '%s: 承認済みチケットとマーカーを %s 件分だけ新しくした（%s）\n' "$2" "$ccnavi_fw_behind" "$2"
	else
		printf '%s: 早送りできなかった。%s（取り込みは sh %s/.ccnavi/scripts/ccnavi-sync.sh %s）\n' \
			"$2" "$(ccnavi_git_refusal "$scratch/ff")" "$root" "$2"
	fi
	ccnavi_lock_drop
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
		git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
		branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
		[ -n "$branch" ] && [ "$branch" != "HEAD" ] || continue
		name=$(basename "$tree")
		# 取り込み済みの親子チケットは早送りだけ（upstream の設定に依らず origin/<P> を見る）。
		if ccnavi_fetch_family "$tree" "$name" "$branch"; then
			ccnavi_fetch_forward "$tree" "$name"
			continue
		fi

		[ -d "$tree/$approved" ] || continue
		git -C "$tree" rev-parse --abbrev-ref "@{u}" >/dev/null 2>&1 || continue
		ccnavi_fetch_or_note "$tree" "$branch" \
			"${name}: リモートを取ってこられなかった。手元の版で判定する" \
			"${name}: ${branch} がリモートに無いので取ってこられなかった。手元の版で判定する" || continue
		behind=$(git -C "$tree" rev-list --count "HEAD..@{u}" 2>/dev/null || echo 0)
		[ "$behind" = "0" ] && continue

		dirty=$(git -C "$tree" status --porcelain --untracked-files=no 2>/dev/null || :)
		if [ -n "$dirty" ]; then
			printf '%s: リモートが %s 件先に進んでいるが、未コミットの変更があるので進めない\n' \
				"$name" "$behind"
			continue
		fi
		if git -C "$tree" merge --ff-only --quiet "@{u}" 2>/dev/null; then
			printf '%s: 承認済みチケットとマーカーを %s 件分だけ新しくした（%s）\n' "$name" "$behind" "$branch"
		else
			printf '%s: リモートと分岐しているので進めない。人に合流させてもらってください（%s）\n' \
				"$name" "$branch"
		fi
	done

	# 第 2 周。リポジトリごとに、ワークツリーの起点になるデフォルトブランチを進める。
	printf '%s\n' "$repos" | while IFS= read -r repo; do
		[ -n "$repo" ] || continue
		git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
		git -C "$repo" remote get-url origin >/dev/null 2>&1 || continue
		default=$(ccnavi_fetch_integration "$repo") || continue
		name=$(basename "$repo")
		ref="refs/remotes/origin/$default"

		here=$(ccnavi_fetch_tree_of "$repo" "$default")
		if [ -n "$here" ] && ccnavi_fetch_seen "$here"; then
			continue
		fi

		ccnavi_fetch_or_note "$repo" "$default" \
			"${name}: ワークツリーの起点になる ${default} を取ってこられなかった。手元の版から切ることになる" \
			"${name}: ワークツリーの起点になる ${default}（統合先）がリモートに無い。CCNAVI_INTEGRATION_BRANCH を確かめてください" ||
			continue

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
			printf '%s: ワークツリーの起点になる %s がリモートと分岐している。人に合流させてもらってください\n' \
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
				printf '%s: ワークツリーの起点になる %s を進められなかった。人に合流させてもらってください\n' \
					"$name" "$default"
				continue
			}
		else
			git -C "$repo" update-ref -m ccnavi-fetch "refs/heads/$default" "$ref" "$old" 2>/dev/null || {
				printf '%s: ワークツリーの起点になる %s を進められなかった。人に合流させてもらってください\n' \
					"$name" "$default"
				continue
			}
		fi
		printf '%s: ワークツリーの起点になる %s を %s 件分だけ新しくした\n' "$name" "$default" "$behind"
	done
)

[ -n "$report" ] || exit 0
printf '[ccnavi] 承認済みチケットとマーカーは親ブランチに含まれて届き、ワークツリーの起点はデフォルトブランチになる。セッションの開始時に取ってきた結果:\n'
printf '%s\n' "$report"
exit 0

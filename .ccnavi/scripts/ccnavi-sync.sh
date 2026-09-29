#!/bin/sh
# ccnavi-sync — 親のブランチをリモートから取り込み、家族の控えと統合先の控えを書く
# （ADR-0093 の 4.2・3.6。段階 2b）。
#
#   sh .ccnavi/scripts/ccnavi-sync.sh [<P>...]
#
# 人が打つ（ボードのボタン、「承認した」と言われたエージェント）。セッションの頭の
# ccnavi-fetch.sh は早送りしかしないので、分かれた家族を取り込むのと、親のブランチが
# リモートから消えたかを確かめるのはここだけ（D12・D13）。
#
# <P> は親のブランチ名（= 親の識別子 = .claude/worktrees/<P>）。省けば、.claude/worktrees/ の下の
# 親のワークツリー（ディレクトリ名 = ブランチ名で、親の写しか提案がある）を全部。
#
# 始めに、親のワークツリーが無くなった家族の控えを消す（片付けた家族。同じ名前で切り直したときに
# 古い控えで push が止まり続けないため）。
#
# リポジトリ（ワークスペース自身と、家族の元のプロジェクト）ごとに 1 回:
#
#   1. `ls-remote --heads origin` で全ブランチの有無を得る。落ちたら（オフライン・認証）止める
#   2. 統合先の名前を決める。CCNAVI_INTEGRATION_BRANCH（環境変数、無ければ
#      .claude/settings.local.json の env）、空ならホストのデフォルトブランチ（`ls-remote --symref
#      origin HEAD`、読めなければ origin/HEAD・main・master）。設定した名前がリモートに無ければ、
#      既定に落とさずに止める（D30）
#   3. 統合先を fetch し、家族ごとの取り込みの後、判定に要るもの（done/・共通層・自身の層・
#      .claude/settings.json）を統合先の控え sync/<リポジトリ>/integration/ へ同じ並びで写し、head に
#      remote・branch・source・sha・fetched_at を書く（D26）。統合先の先頭が前と同じなら写さない
#
# 家族ごと（ロックを待って取る。D32）:
#
#   - 途中の操作（merge・cherry-pick・revert・rebase）があれば何もせず止める（利用者の途中の
#     merge を取りやめない）
#   - P がリモートにある: fetch して、早送りできれば早送り、分かれていれば merge。merge は
#     索引が HEAD と同じときだけ（D35）で、衝突したら、この sh が始めた merge だけを取りやめて
#     人に回す。家族の控えを present で書く
#   - P がリモートに無い: まず統合先の done/ にこの家族の親の写し（識別子と承認の時刻が同じ）が
#     あれば閉じた家族（closed）。無く、送った跡（控え・origin/<P>・追跡の設定）も無ければ、
#     一度も送っていない家族で今のまま。送った跡があれば観測ずれを疑い、統合先を取り直して
#     確かめ直す（既定 3 回、5 秒おき）。それでも無ければ ccnavi-review.sh merged に聞き、
#     マージ済みなら「反映待ち」、答えが得られなければ「確かめられなかった」で止める（控えは
#     書き換えない）。マージされていないと分かったときだけ、控えがあれば gone を書いて止める
#     （控えが無ければ gone は書かずに止める）
#
# この段（2b）では控えを書くだけで、判定はまだ控えを読まない（2c）。
#
# 実行ファイルはネットワークに出ない（docs/claude/exe-boundary.md）。ここが git で取ってくる。
# 置き場の綴りと settings.local.json の読みだけを実行ファイル（`ccnavi sync paths`）に聞く。
# 実行ファイルが無ければ、ほかの sh と同じく環境変数の綴り（無ければ既定）を使う。在るのに
# 答えなかったときは、統合先を取り違えないよう止める。
#
# 環境変数: CCNAVI_INTEGRATION_BRANCH / CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/
#   CCNAVI_SYNC_RETRIES（観測ずれの確かめ直しの回数、既定 3）/ CCNAVI_SYNC_RETRY_WAIT（その間隔の秒、既定 5）/
#   CCNAVI_SYNC_TIMEOUT（ls-remote・fetch 1 回の見張りの秒、既定 60）
# 終了コード: 0 全部取り込んだ（取り込むものが無いを含む） / 1 止めた家族かリポジトリがある /
#           2 引数か環境の誤り

set -eu

. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-sync.sh [<P>...]

  親のブランチ <P>（省けば .claude/worktrees/ の下の親のワークツリー全部）をリモートから取り込み、
  家族の控えと統合先の控えを書く。分かれていれば merge し、衝突したら取りやめて人に回す。
  リモートから消えた親のブランチは、統合先の done/ を見て「閉じた」か「消えた」かを決める。
  親のワークツリーが無くなった家族の控えは消す。

  統合先: CCNAVI_INTEGRATION_BRANCH（環境変数か .claude/settings.local.json の env）、
          空ならホストのデフォルトブランチ
USAGE
}

case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
-*)
	printf 'ccnavi-sync: %s は受けません。\n' "$1" >&2
	usage >&2
	exit 2
	;;
esac

for want in ${1+"$@"}; do
	case "$want" in
	'' | -* | *..* | */* | *[!A-Za-z0-9._-]*)
		printf 'ccnavi-sync: %s は親のブランチ名（識別子）の形ではありません。\n' "$want" >&2
		exit 2
		;;
	esac
done

root=$(ccnavi_workspace) || {
	printf 'ccnavi-sync: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}
ccnavi_log_root="$root"
state=$(ccnavi_state "$root")
here_sh="$(dirname "$0")"

number_or() {
	case "${1:-}" in
	'' | *[!0-9]*) printf '%s\n' "$2" ;;
	*) printf '%s\n' "$1" ;;
	esac
}
lock_wait=$(number_or "${CCNAVI_LOCK_WAIT:-}" 120)
retries=$(number_or "${CCNAVI_SYNC_RETRIES:-}" 3)
retry_wait=$(number_or "${CCNAVI_SYNC_RETRY_WAIT:-}" 5)
timeout=$(number_or "${CCNAVI_SYNC_TIMEOUT:-}" 60)

scratch=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-sync) || {
	printf 'ccnavi-sync: 一時ディレクトリが作れません。\n' >&2
	exit 2
}
: >"$scratch/failed"
tab=$(printf '\t')

# 後始末。この sh が始めた merge の途中（印 sync_merging）で切られたら取りやめ、ロックを外す。
# dash は EXIT の trap を INT・TERM・HUP で走らせないので、そちらにも置く。2 度走っても害は無い。
sync_merging=""
cleaned=""
cleanup() {
	[ -z "$cleaned" ] || return 0
	cleaned=yes
	if [ -n "$sync_merging" ] && [ -e "$(git_path "$sync_merging" MERGE_HEAD)" ]; then
		git -C "$sync_merging" merge --abort >/dev/null 2>&1 || :
	fi
	ccnavi_lock_drop
	rm -rf "$scratch"
}
trap 'cleanup' EXIT
trap 'cleanup; exit 130' INT TERM HUP

log_info 受け付けた -- "args=$#"

# `git rev-parse --git-path <名前>` をツリーからの綴りにする（ワークツリーでは git ディレクトリが別）。
git_path() {
	gp_rel=$(git -C "$1" rev-parse --git-path "$2" 2>/dev/null || :)
	case "$gp_rel" in
	'') printf '%s\n' "$1/.git/$2" ;;
	/* | [A-Za-z]:*) printf '%s\n' "$gp_rel" ;;
	*) printf '%s\n' "$1/$gp_rel" ;;
	esac
}

# ---- 置き場の綴りと、settings.local.json の統合先。実行ファイルに聞く（D33）。

sync_info=""
info_from=""
if bin=$(ccnavi_bin "$root"); then
	info_from="$bin"
	sync_info=$("$bin" --root "$root" sync paths 2>"$scratch/info" </dev/null) || info_from="failed:$bin"
elif [ -f "$root/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
	info_from="uv run python -m ccnavi"
	sync_info=$(cd "$root" && uv run --quiet python -m ccnavi --root "$root" sync paths 2>"$scratch/info" </dev/null) ||
		info_from="failed:uv run python -m ccnavi"
fi
case "$info_from" in
failed:*)
	printf 'ccnavi-sync: 実行ファイル（%s）が置き場の綴りと統合先の設定を答えなかった（%s）。統合先を取り違えないよう止めた。\n' \
		"${info_from#failed:}" "$(head -n 1 "$scratch/info" 2>/dev/null)" >&2
	exit 2
	;;
esac
info() {
	printf '%s\n' "$sync_info" | sed -n "s/^$1 //p" | head -n 1
}
approved=$(info approved)
proposals=$(info proposals)
home=$(info home)
integration_local=$(info integration)
[ -n "$approved" ] || approved="${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}"
[ -n "$proposals" ] || proposals="${CCNAVI_TICKETS_PROPOSAL:-wip/proposals}"
[ -n "$home" ] || home="${CCNAVI_PROJECT_HOME:-.ccnavi}"
approved="${approved%/}"
proposals="${proposals%/}"
home="${home%/}"
projects="${CCNAVI_PROJECTS:-projects}"
projects="${projects%/}"

# ---- 統合先の名前（D30）

if [ -n "${CCNAVI_INTEGRATION_BRANCH:-}" ]; then
	integration_want="$CCNAVI_INTEGRATION_BRANCH"
	integration_source=env
elif [ -n "$integration_local" ]; then
	integration_want="$integration_local"
	integration_source=settings.local.json
else
	integration_want=""
	integration_source=default
fi

# 出力に出す、統合先をどこから決めたか。
source_words() {
	case "$1" in
	env) printf '環境変数 CCNAVI_INTEGRATION_BRANCH' ;;
	settings.local.json) printf '.claude/settings.local.json の CCNAVI_INTEGRATION_BRANCH' ;;
	*) printf 'ホストのデフォルトブランチ' ;;
	esac
}

# ls-remote の一覧に refs/heads/<名前> があるか。前方一致で取り違えないよう、2 列目を
# grep -F -x で完全一致に比べる（i0131 と i0131-x）。<一覧> <名前>
has_head() {
	cut -f 2 "$1" | grep -F -x -q -- "refs/heads/$2"
}

# ホストのデフォルトブランチ。`ls-remote --symref origin HEAD` が指すもの（一覧にあるもの）、
# 読めなければ手元の origin/HEAD、無ければ一覧にある main・master。どれも無ければ 1。<リポジトリ> <一覧>
default_branch() {
	db_head=""
	if ccnavi_git_timed "$timeout" "$scratch/err-symref" "$1" ls-remote --symref origin HEAD >"$scratch/symref"; then
		db_head=$(sed -n "s|^ref: refs/heads/\\(.*\\)${tab}HEAD\$|\\1|p" "$scratch/symref" | head -n 1)
	fi
	if [ -z "$db_head" ]; then
		db_head=$(git -C "$1" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || :)
		db_head="${db_head#origin/}"
	fi
	if [ -n "$db_head" ] && has_head "$2" "$db_head"; then
		printf '%s\n' "$db_head"
		return 0
	fi
	for db_try in main master; do
		if has_head "$2" "$db_try"; then
			printf '%s\n' "$db_try"
			return 0
		fi
	done
	return 1
}

# fetch 1 本。単一ブランチの clone でも origin/<ブランチ> が進むよう、行き先を書いて取る
# （sh の中の git。ccnavi-git.sh の入口の refspec の拒否とは別の話）。
# 落ちた理由（git の標準エラー）は $scratch/err に残る。<リポジトリ> <ブランチ>
fetch_one() {
	ccnavi_git_timed "$timeout" "$scratch/err" "$1" \
		fetch --quiet --no-tags origin "+refs/heads/$2:refs/remotes/origin/$2" >/dev/null
}

fail_note() {
	printf 'fail\n' >>"$scratch/failed"
}

# 途中の操作（merge・cherry-pick・revert・sequencer・rebase）の名前。無ければ空。<ツリー>
busy_state() {
	for bs_name in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD sequencer rebase-merge rebase-apply; do
		if [ -e "$(git_path "$1" "$bs_name")" ]; then
			printf '%s\n' "$bs_name"
			return 0
		fi
	done
	return 0
}

# ---- 統合先の控え

# 統合先の控えを書く。<リポジトリ> <控えの名前> <統合先>
#
# 統合先の先頭から、判定に要るものをファイルのまま同じ並びで写す（Python の読み方を変えずに
# 済む形）。`git archive` は .gitattributes（export-ignore・export-subst・eol）で中身を変えるので使わず、
# `ls-tree` と `cat-file blob` でコミットのバイト列そのものを書く。シンボリックリンク（120000）と
# サブモジュールは写さない（読む側がリンクを辿らない決まりと二重にする）。
# リポジトリごとのロックの中で、一時の置き場に組んでから入れ替える。先頭が前と同じなら写さない。
write_integration() {
	wi_repo="$1"
	wi_key="$2"
	wi_branch="$3"
	wi_ref="refs/remotes/origin/$3"
	wi_sha=$(git -C "$wi_repo" rev-parse --verify --quiet "$wi_ref^{commit}" 2>/dev/null) || return 1
	wi_dest="$state/sync/$wi_key/integration"
	wi_lock_rc=0
	ccnavi_lock_take "$root" "$wi_key" _integration "$lock_wait" || wi_lock_rc=$?
	[ "$wi_lock_rc" -eq 0 ] || return 1
	# 前に落ちた回の残り。
	for wi_left in "$wi_dest".tmp.* "$wi_dest".old.*; do
		[ -e "$wi_left" ] && rm -rf "$wi_left"
	done
	if [ -f "$wi_dest/head" ] &&
		[ "$(ccnavi_record_get "$wi_dest/head" sha)" = "$wi_sha" ] &&
		[ "$(ccnavi_record_get "$wi_dest/head" branch)" = "$wi_branch" ] &&
		[ "$(ccnavi_record_get "$wi_dest/head" source)" = "$integration_source" ]; then
		ccnavi_record_write "$wi_dest/head" remote origin branch "$wi_branch" source "$integration_source" \
			sha "$wi_sha" fetched_at "$(date +%s)" || :
		ccnavi_lock_drop
		return 0
	fi
	wi_tmp="$wi_dest.tmp.$$"
	mkdir -p "$wi_tmp" || {
		ccnavi_lock_drop
		return 1
	}
	wi_ok=yes
	for wi_path in "$approved/done" ".ccnavi/common" "$home/config" ".claude/settings.json"; do
		git -C "$wi_repo" cat-file -e "$wi_ref:$wi_path" 2>/dev/null || continue
		if ! git -C "$wi_repo" -c core.quotepath=false ls-tree -r --full-tree "$wi_ref" -- "$wi_path" \
			>"$scratch/tree" 2>/dev/null; then
			wi_ok=no
			break
		fi
		while IFS= read -r wi_line; do
			wi_mode="${wi_line%% *}"
			wi_rest="${wi_line#* }"
			wi_rest="${wi_rest#* }"
			wi_blob="${wi_rest%%"$tab"*}"
			wi_file="${wi_rest#*"$tab"}"
			case "$wi_mode" in
			100644 | 100755) ;;
			*) continue ;; # リンク・サブモジュールは写さない
			esac
			case "$wi_file" in
			'"'* | /* | *../*)
				# 引用された綴り（改行や制御文字を含む名前）と、外へ出る綴りは写さない。
				log_warn 統合先の控えに写せない名前を飛ばした -- "repo=$wi_key"
				continue
				;;
			esac
			mkdir -p "$wi_tmp/$(dirname "$wi_file")" &&
				git -C "$wi_repo" cat-file blob "$wi_blob" </dev/null >"$wi_tmp/$wi_file" 2>/dev/null || {
				wi_ok=no
				break
			}
		done <"$scratch/tree"
		[ "$wi_ok" = yes ] || break
	done
	# 念のため、リンクが紛れていれば消す（読む側もリンクを辿らない）。
	if [ "$wi_ok" = yes ] && [ -n "$(find "$wi_tmp" -type l 2>/dev/null | head -n 1)" ]; then
		find "$wi_tmp" -type l -exec rm -f {} + 2>/dev/null || wi_ok=no
	fi
	if [ "$wi_ok" = yes ]; then
		ccnavi_record_write "$wi_tmp/head" remote origin branch "$wi_branch" source "$integration_source" \
			sha "$wi_sha" fetched_at "$(date +%s)" || wi_ok=no
	fi
	if [ "$wi_ok" = yes ] && [ -d "$wi_dest" ]; then
		mv "$wi_dest" "$wi_dest.old.$$" || wi_ok=no
	fi
	if [ "$wi_ok" = yes ]; then
		if mv "$wi_tmp" "$wi_dest"; then
			rm -rf "$wi_dest.old.$$"
		else
			# 入れ替えに落ちたら、前の控えを戻す。
			[ -d "$wi_dest.old.$$" ] && mv "$wi_dest.old.$$" "$wi_dest" 2>/dev/null
			wi_ok=no
		fi
	fi
	rm -rf "$wi_tmp"
	ccnavi_lock_drop
	[ "$wi_ok" = yes ]
}

# ---- 家族を集める。<P><タブ><ツリー><タブ><リポジトリの控えの名前> を 1 行ずつ。

: >"$scratch/families"
: >"$scratch/repos"
repo_dir_of() {
	# <控えの名前> → そのリポジトリのディレクトリ
	if [ "$1" = self ]; then
		printf '%s\n' "$root"
	else
		case "$projects" in
		/* | [A-Za-z]:*) printf '%s/%s\n' "$projects" "$1" ;;
		*) printf '%s/%s/%s\n' "$root" "$projects" "$1" ;;
		esac
	fi
}
has_family_line() {
	awk -F "$tab" -v p="$1" '$1 == p { found = 1 } END { exit found ? 0 : 1 }' "$scratch/families"
}
repo_has_families() {
	awk -F "$tab" -v k="$1" '$3 == k { found = 1 } END { exit found ? 0 : 1 }' "$scratch/families"
}
add_family() {
	has_family_line "$1" && return 0
	af_key=$(ccnavi_repo_key "$2" "$root")
	printf '%s\t%s\t%s\n' "$1" "$2" "$af_key" >>"$scratch/families"
	grep -F -x -q -- "$af_key" "$scratch/repos" 2>/dev/null || printf '%s\n' "$af_key" >>"$scratch/repos"
}

# 親のワークツリーが無くなった家族の控えを消す（D11 の寿命。片付けた家族）。
for fam_record in "$state"/sync/*/families/*; do
	[ -f "$fam_record" ] || continue
	case "$fam_record" in
	*.tmp.*) continue ;;
	esac
	fam_name="${fam_record##*/}"
	if [ ! -d "$root/.claude/worktrees/$fam_name" ]; then
		rm -f "$fam_record"
		printf '%s: 親のワークツリーが無いので家族の控えを消した（片付けた家族）\n' "$fam_name"
	fi
done

if [ "$#" -gt 0 ]; then
	for want in "$@"; do
		has_family_line "$want" && continue
		tree="$root/.claude/worktrees/$want"
		if [ ! -d "$tree" ] || [ -L "$tree" ]; then
			printf '%s: 親のワークツリー（.claude/worktrees/%s）が無い。切り直してから打ち直す（sh %s/ccnavi-git.sh fetch origin %s のあと worktree add .claude/worktrees/%s -b %s origin/%s）\n' \
				"$want" "$want" "$here_sh" "$want" "$want" "$want" "$want"
			fail_note
			continue
		fi
		if ! ccnavi_parent_tree "$tree" "$want"; then
			printf '%s: .claude/worktrees/%s に親の写しも提案も無い。親のワークツリーではないので取り込まない\n' "$want" "$want"
			fail_note
			continue
		fi
		add_family "$want" "$tree"
	done
else
	printf 'self\n' >"$scratch/repos"
	case "$projects" in
	/* | [A-Za-z]:*) project_root="$projects" ;;
	*) project_root="$root/$projects" ;;
	esac
	for dir in "$project_root"/*; do
		[ -e "$dir/.git" ] || continue
		[ -L "$dir" ] && continue
		printf '%s\n' "$(basename "$dir")" >>"$scratch/repos"
	done
	for tree in "$root/.claude/worktrees"/*; do
		[ -d "$tree" ] || continue
		[ -L "$tree" ] && continue
		name=$(basename "$tree")
		branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
		[ "$branch" = "$name" ] || continue
		ccnavi_parent_tree "$tree" "$name" || continue
		add_family "$name" "$tree"
	done
fi

# ---- 家族 1 つ。<P> <ツリー> <控えの名前> <リポジトリ> <統合先> <一覧>

sync_family() {
	P="$1"
	tree="$2"
	key="$3"
	repo="$4"
	integ="$5"
	heads="$6"
	record=$(ccnavi_family_record "$root" "$key" "$P")

	branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ "$branch" != "$P" ]; then
		printf '%s: 親のワークツリーが %s の上に居る。%s に戻してから打ち直す\n' "$P" "${branch:-（ブランチの外）}" "$P"
		fail_note
		return 0
	fi

	# set -e の下なので、戻り値は || で受ける（そのまま打つと、取れなかった時点で sh ごと抜ける）。
	lock_rc=0
	ccnavi_lock_take "$root" "$key" "$P" "$lock_wait" || lock_rc=$?
	case "$lock_rc" in
	0) ;;
	2)
		printf '%s: 古いロック（%s/locks/%s/%s）を奪いかけて戻せなかった。人が中身を見て片付ける\n' "$P" "$state" "$key" "$P"
		fail_note
		return 0
		;;
	*)
		printf '%s: 他の操作がロックを持っている（%s）。終わってから打ち直す\n' "$P" \
			"$(ccnavi_lock_owner "$state/locks/$key/$P")"
		fail_note
		return 0
		;;
	esac

	busy=$(busy_state "$tree")
	if [ -n "$busy" ]; then
		printf '%s: 親のワークツリーに途中の操作（%s）がある。済ませるか取りやめてから打ち直す（この sh は触らない）\n' "$P" "$busy"
		fail_note
	elif has_head "$heads" "$P"; then
		sync_present
	else
		sync_absent
	fi
	ccnavi_lock_drop
	return 0
}

# P がリモートにある。取ってきて、早送りか merge。
sync_present() {
	if ! fetch_one "$tree" "$P"; then
		printf '%s: 親のブランチを取ってこられなかった（%s）\n' "$P" "$(head -n 1 "$scratch/err")"
		fail_note
		return 0
	fi
	remote_sha=$(git -C "$tree" rev-parse --verify --quiet "refs/remotes/origin/$P^{commit}" 2>/dev/null || :)
	local_sha=$(git -C "$tree" rev-parse --verify --quiet "HEAD^{commit}" 2>/dev/null || :)
	if [ -z "$remote_sha" ] || [ -z "$local_sha" ]; then
		printf '%s: 先頭を読めなかった\n' "$P"
		fail_note
		return 0
	fi
	if [ "$remote_sha" = "$local_sha" ]; then
		printf '%s: リモートと同じ\n' "$P"
	elif git -C "$tree" merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
		ahead=$(git -C "$tree" rev-list --count "$remote_sha..$local_sha" 2>/dev/null || echo '?')
		printf '%s: 手元が %s 件先に居る（まだ送っていない）。取り込むものは無い\n' "$P" "$ahead"
	elif git -C "$tree" merge-base --is-ancestor "$local_sha" "$remote_sha" 2>/dev/null; then
		behind=$(git -C "$tree" rev-list --count "$local_sha..$remote_sha" 2>/dev/null || echo '?')
		if LC_ALL=C git -C "$tree" merge --ff-only --quiet "$remote_sha" </dev/null >/dev/null 2>"$scratch/err"; then
			printf '%s: リモートの %s 件を早送りで取り込んだ\n' "$P" "$behind"
		else
			printf '%s: 早送りできなかった。%s\n' "$P" "$(ccnavi_git_refusal "$scratch/err")"
			fail_note
		fi
	else
		# 分かれている。非 ff の merge は、重ならないステージ済みの変更があっても拒む（git 2.43）ので、
		# 先に見て言う（D35）。
		if ! git -C "$tree" diff --cached --quiet 2>/dev/null; then
			printf '%s: リモートと分かれていて merge が要るが、ステージ済みの変更がある。コミットするか sh %s/ccnavi-git.sh restore --staged <パス> で外してから打ち直す\n' \
				"$P" "$here_sh"
			fail_note
		else
			# 途中の操作が無いのは確かめてあるので、この後の MERGE_HEAD はこの merge のもの。
			sync_merging="$tree"
			if LC_ALL=C git -C "$tree" merge --no-edit --quiet -m "ccnavi: origin/$P を取り込む" "$remote_sha" \
				</dev/null >"$scratch/out" 2>"$scratch/err"; then
				printf '%s: リモートと分かれていたので merge で取り込んだ\n' "$P"
			else
				conflicted=$(git -C "$tree" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')
				if [ -e "$(git_path "$tree" MERGE_HEAD)" ]; then
					git -C "$tree" merge --abort >/dev/null 2>&1 || :
				fi
				if [ -n "$conflicted" ]; then
					printf '%s: リモートと分かれていて merge が衝突した（%s）。取り込みをやめた（merge --abort）。どちらを採るかは人が決める\n' \
						"$P" "$conflicted"
				else
					cat "$scratch/out" >>"$scratch/err"
					printf '%s: リモートと分かれていて merge できなかった。%s\n' "$P" "$(ccnavi_git_refusal "$scratch/err")"
				fi
				fail_note
			fi
			sync_merging=""
		fi
	fi
	ccnavi_record_write "$record" remote origin branch "$P" sha "$remote_sha" \
		fetched_at "$(date +%s)" state present reason "" ||
		printf '%s: 家族の控え（%s）を書けなかった\n' "$P" "$record"
	return 0
}

# 承認の時刻（`approved_at`）。ブロックの形（`  approved_at: X`）と流れの形（`{approved_at: "X", ...}`）。標準入力から。
approved_at_of() {
	sed -n "s/.*approved_at:[[:space:]]*[\"']\\{0,1\\}\\([^\"',}[:space:]]*\\).*/\\1/p" | head -n 1
}

# 統合先の done/ に、この家族の親の写しがあるか。統合先は取ってきた後の refs/remotes/origin/<統合先>。
#
# 在るだけでは見ない。識別子（`ticket:`）が P で、子（`parent:`）でなく、承認の時刻が親のワークツリーの
# 写しと同じときだけ「閉じた」とする（同じ識別子の古い家族の写しを、この家族のものと読まない）。
# 親のワークツリーに承認済みの写しが無い（承認前の提案だけ）なら、この家族は閉じようがない。
closed_in_integration() {
	ci_body=$(git -C "$repo" show "refs/remotes/origin/$integ:$approved/done/$P.md" 2>/dev/null) || return 1
	ci_id=$(printf '%s\n' "$ci_body" | sed -n 's/^ticket:[[:space:]]*//p' | head -n 1 |
		sed -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']\$//")
	[ "$ci_id" = "$P" ] || return 1
	printf '%s\n' "$ci_body" | grep -q '^parent:' && return 1
	ci_mine=""
	for ci_file in "$tree/$approved/doing/$P.md" "$tree/$approved/done/$P.md"; do
		[ -f "$ci_file" ] || continue
		ci_mine=$(approved_at_of <"$ci_file")
		[ -z "$ci_mine" ] || break
	done
	[ -n "$ci_mine" ] || return 1
	[ "$(printf '%s\n' "$ci_body" | approved_at_of)" = "$ci_mine" ]
}

# P がリモートに無い。閉じたか、消えたか（3.6）。
sync_absent() {
	kept_sha=$(ccnavi_record_get "$record" sha)
	trace=no
	if [ -f "$record" ]; then
		trace=record
	elif git -C "$tree" show-ref --verify --quiet "refs/remotes/origin/$P" ||
		git -C "$tree" config --get "branch.$P.remote" >/dev/null 2>&1; then
		trace=ref
		[ -n "$kept_sha" ] || kept_sha=$(git -C "$tree" rev-parse --verify --quiet "refs/remotes/origin/$P" 2>/dev/null || :)
	fi
	tries=0
	while :; do
		# 控えの有無より先に、統合先で閉じているかを見る。
		if closed_in_integration; then
			ccnavi_record_write "$record" remote origin branch "$P" sha "$kept_sha" \
				fetched_at "$(date +%s)" state closed reason "統合先の done/ に親の写しがある" || :
			printf '%s: リモートから消えたが、統合先（%s）の done/ に閉じた記録がある（閉じた家族）。親のワークツリーは片付けてよい\n' "$P" "$integ"
			return 0
		fi
		if [ "$trace" = no ]; then
			printf '%s: リモートに無い（まだ送っていない家族）。今の手元の動きのまま\n' "$P"
			return 0
		fi
		[ "$tries" -lt "$retries" ] || break
		tries=$((tries + 1))
		# 観測ずれ。ホストはマージの後に P を消すが、読み取りの複製が遅れて done/ がまだ見えないことがある。
		sleep "$retry_wait"
		fetch_one "$repo" "$integ" || :
	done
	# ccnavi-review.sh の道具（gh / glab / curl とトークン）で、MR がマージ済みかを聞く。答えは
	# merged <番号> / none（マージされた MR が無い）/ それ以外（道具・トークンが無い、API が落ちた）。
	merged_rc=0
	merged=$(cd "$tree" && sh "$here_sh/ccnavi-review.sh" merged 2>/dev/null </dev/null) || merged_rc=$?
	merged=$(printf '%s\n' "$merged" | head -n 1)
	case "$merged_rc:$merged" in
	*:'merged '*)
		printf '%s: MR（%s）はマージ済みだが、統合先（%s）への反映がまだ見えない。少し待って sh %s/ccnavi-sync.sh %s を打ち直す\n' \
			"$P" "${merged#merged }" "$integ" "$here_sh" "$P"
		fail_note
		return 0
		;;
	0:none) ;;
	*)
		printf '%s: 親のブランチがリモートに無く、統合先（%s）にも閉じた記録が無い。MR がマージ済みかを確かめられなかった（ccnavi-review.sh merged が答えなかった。gh・glab か curl とトークンが要る）。控えは変えずに止めた。確かめられる道具を用意して打ち直すか、人が確かめる\n' "$P" "$integ"
		fail_note
		return 0
		;;
	esac
	if [ "$trace" = record ]; then
		ccnavi_record_write "$record" remote origin branch "$P" sha "$kept_sha" \
			fetched_at "$(date +%s)" state gone reason "リモートにも統合先の done/ にも無い" ||
			printf '%s: 家族の控え（%s）を書けなかった\n' "$P" "$record"
		printf '%s: 親のブランチ %s がリモートに無い。統合先（%s）にも閉じた記録が無いので、この家族の状態を決められない。家族を止めた（控えは gone。この P への push は通らない）\n' "$P" "$P" "$integ"
	else
		printf '%s: 親のブランチ %s がリモートに無い。統合先（%s）にも閉じた記録が無いので、この家族の状態を決められない。送った跡（origin/%s か追跡の設定）はあるが控えが無いので、控えは作らずに止めた\n' "$P" "$P" "$integ" "$P"
	fi
	printf '  戻し方 1（改名・消し間違い）: 元の名前 %s でブランチを作り直す。端末なら git push origin %s:refs/heads/%s（控えにある、最後に取り込んだか送った %s の先頭）、GitHub なら PR の画面の「Restore branch」、GitLab なら MR の refs/merge-requests/<番号>/head から %s を作る。戻したら sh %s/ccnavi-sync.sh %s を打ち直す\n' \
		"$P" "${kept_sha:-<最後に取り込んだ sha>}" "$P" "$P" "$P" "$here_sh" "$P"
	printf '  戻し方 2（家族を捨てた）: 親のワークツリーを片付け（sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s）、sh %s/ccnavi-sync.sh を打つと家族の控えも消える（同じ名前で切り直せる）\n' \
		"$here_sh" "$P" "$here_sh"
	fail_note
	return 0
}

# ---- リポジトリごと

while IFS= read -r key <&4; do
	[ -n "$key" ] || continue
	repo=$(repo_dir_of "$key")
	has_family=no
	repo_has_families "$key" && has_family=yes
	# 家族の無いリポジトリ（引数を省いた回のプロジェクトなど）で落ちても、名指しして続け、1 にしない。
	repo_fail() {
		[ "$has_family" = yes ] && fail_note
		return 0
	}
	if ! git -C "$repo" remote get-url origin >/dev/null 2>&1; then
		# origin の無いリポジトリは取り込みの対象外（今の手元の動きのまま）。
		if [ "$has_family" = yes ]; then
			printf '%s: origin が無い。取り込みの対象外（今の手元の動きのまま）\n' "$key"
		fi
		continue
	fi
	label=""
	[ "$key" = self ] || label="$key: "
	heads="$scratch/heads-$key"
	if ! ccnavi_git_timed "$timeout" "$scratch/err" "$repo" ls-remote --heads origin >"$heads"; then
		printf '%sリモートのブランチの一覧を取ってこられなかった（オフラインか認証。%s）。止める\n' "$label" \
			"$(head -n 1 "$scratch/err")"
		repo_fail
		continue
	fi
	if [ -n "$integration_want" ]; then
		if ! has_head "$heads" "$integration_want"; then
			printf '%s統合先 %s（%s）がリモートに無い。設定を直す。取り込みを止めた\n' "$label" \
				"$integration_want" "$(source_words "$integration_source")"
			repo_fail
			continue
		fi
		integ="$integration_want"
	elif ! integ=$(default_branch "$repo" "$heads"); then
		printf '%s統合先が決まらない（ホストのデフォルトブランチが読めず、main・master もリモートに無い）。CCNAVI_INTEGRATION_BRANCH を設定する。取り込みを止めた\n' "$label"
		repo_fail
		continue
	fi
	printf '%s統合先: %s（%s）\n' "$label" "$integ" "$(source_words "$integration_source")"
	if ! fetch_one "$repo" "$integ"; then
		printf '%s統合先 %s を取ってこられなかった（%s）。止める\n' "$label" "$integ" "$(head -n 1 "$scratch/err")"
		repo_fail
		continue
	fi
	awk -F "$tab" -v k="$key" '$3 == k' "$scratch/families" >"$scratch/these"
	# 読む先は fd 3。中で起こす git が標準入力を読んでも、家族の並びを食べない。
	while IFS="$tab" read -r fam_p fam_tree fam_key <&3; do
		[ -n "$fam_p" ] || continue
		sync_family "$fam_p" "$fam_tree" "$fam_key" "$repo" "$integ" "$heads"
	done 3<"$scratch/these"
	if ! write_integration "$repo" "$key" "$integ"; then
		printf '%s統合先の控え（%s/sync/%s/integration）を書けなかった\n' "$label" "$state" "$key"
		repo_fail
	fi
done 4<"$scratch/repos"

if [ -s "$scratch/failed" ]; then
	log_info 終わった -- "exit=1"
	exit 1
fi
log_info 終わった -- "exit=0"
exit 0

#!/bin/sh
# ccnavi-sync 親のブランチをリモートから取り込み、親子のチケットの取り込み状態と統合先の取り込み結果を書く。
#
#   sh .ccnavi/scripts/ccnavi-sync.sh [<P>...]
#   sh .ccnavi/scripts/ccnavi-sync.sh --forget <P>...   （ユーザが打つ。親子のチケットの取り込み状態を消す）
#
# ユーザが打つ（ボードのボタン、「承認した」と言われたエージェント）。セッションの頭の
# ccnavi-fetch.sh は早送りしかしないので、分かれた親子のチケットを取り込むのと、親のブランチが
# リモートから消えたかを確かめるのはここだけ（セッションの頭を待たせず、merge の書きかけも残さないため）。
#
# <P> は親の識別子（= .claude/worktrees/<P>）。省けば、.claude/worktrees/ の下の親のワークツリー
# （親チケットか提案があり、親のブランチをチェックアウトしているもの）を全部。
#
# 親のブランチ名は親チケットの `branch:`、無ければ識別子と同じ名前。sh はチケットを読まず、
# 実行ファイルの `c1 family <P>` の `branch` の行で知る。取り込み状態の鍵は識別子のままで、中の
# `branch` に親のブランチ名を書く（`/` を含む名前を state の置き場のパスに入れない）。実行ファイルが無ければ
# 識別子をブランチ名とする（前の動き）。在るのに答えなければ、その親子のチケットは取り込まずに止める。
#
# 親子のチケットの取り込み状態は削除せずに残す。親のワークツリーを片付けても消さない。
# 消すと、決まらないで止めていた親子のチケット（gone など）が取り込み状態の無いものに戻り、止めが外れるため。
# 消すのはユーザが打つ `--forget <P>` だけ（親のワークツリーを片付けた後に限る。ネットワークは使わない）。
# エージェントからは組み込みの deny（builtin-guard-ticket-approval）が止める。
#
# リポジトリ（ワークスペース自身と、親子のチケットの元のプロジェクト）ごとに 1 回、次の順に行う。
#
#   1. `ls-remote --heads origin` で全ブランチの有無を得る。落ちたら（オフライン・認証）止める
#   2. 統合先の名前を決める。CCNAVI_INTEGRATION_BRANCH（環境変数、無ければ
#      .claude/settings.local.json の env）、空ならホストのデフォルトブランチ（`ls-remote --symref
#      origin HEAD`、読めなければ origin/HEAD・main・master）。設定した名前がリモートに無ければ、
#      既定に落とさずに止める
#   3. 統合先を fetch し、親子のチケットごとの取り込みの後、判定に要るもの（done/・共通層・自身の層・
#      .claude/settings.json）を統合先の取り込み結果 sync/<リポジトリ>/integration/ へ同じ構成でコピーし、head に
#      remote・branch・source・sha・fetched_at を書く。統合先の先頭が前と同じならコピーしない
#
# 親子のチケットごとに次を行う（ロックを待って取る）。
#
#   - 途中の操作（merge・cherry-pick・revert・rebase）があれば何もせず止める（ユーザの途中の
#     merge を取りやめない）
#   - P がリモートにある: fetch して、早送りできれば早送り、分かれていれば merge。merge は
#     索引が HEAD と同じときだけで、衝突したら、この sh が始めた merge だけを取りやめて
#     ユーザに回す。親子のチケットの取り込み状態を present で書く
#   - P がリモートに無い: まず統合先の done/ にこの親子のチケットの親チケット（識別子が同じで、着手と取り消しの欄で
#     同じ親と言えるもの。closed_in_integration）があれば閉じた親子のチケット（closed）。統合先には閉じたチケットを
#     残さない（ready が退避して消し、squash でマージする）ので、ふつうは統合先の done/ には無い。
#     無く、送った形跡（取り込み状態・origin/<P>・追跡の設定）も無ければ、一度も送っていない親子のチケットで
#     今のまま。送った形跡があれば、先に ccnavi-review.sh merged に聞き、マージ済みなら閉じた親子の
#     チケット（closed）。答えが得られなければ観測ずれを疑い、統合先を取り直して確かめ直す（既定 3 回、
#     5 秒おき）。それでも無ければ、手元の退避（logs/archive/<リポジトリ>/done/<P>.md。ready が閉じた
#     親子のチケットを移した先）に親チケット（識別子が P で子でなく、ツリーにチケットが残っていれば着手と
#     取り消しの欄でも同じ親と言えるもの）があるときだけ closed で補い、無ければ「確かめられなかった」で止める
#     （取り込み状態は書き換えない）。
#     マージされていないと分かったときだけ、取り込み状態があれば gone を書いて止める
#     （取り込み状態が無ければ gone は書かずに止める）
#
# 統合先の取り込み結果を書いた後、present の親子のチケットごとに実行ファイルの `ccnavi sync check <P> <リポジトリ>` で
# 判定し直す（どのチケットを本物とするかの検査と、承認済みチケットの判定し直し）。error があれば
# 親子のチケットの取り込み状態を blocked にして、理由を reason に書いて止める。判定（hook・承認・状態の操作）は
# blocked の親子のチケットを止める。解き方は、理由を直してから同じ P でこの sh をオンラインで打ち直すこと（取り込みで
# present に書き直してから検査し直すので、通れば present に戻る）。書く前にロックを取り直し、取り込み状態が
# まだ present かを確かめる（並行する sync が書いた gone・closed を上書きしない）。ロックが取れない・
# 書けないときは 3 回まで試し、それでも書けなければ終了コード 3 で終わる（止めるべき親子のチケットが止まって
# いない）。実行ファイルが検査を実行できなかった（古い実行ファイルが `sync check` を知らない、など。
# 答えの頭に `check 1` が無い）ときは、親子のチケットを止めずに警告だけ出す（検査の error とは分ける）。
#
# 実行ファイルはネットワークに出ない（docs/claude/exe-boundary.md）。ここが git で取ってくる。
# 置き場のパスと settings.local.json の読みだけを実行ファイル（`ccnavi sync paths`）に聞く。
# 実行ファイルが無ければ、ほかの sh と同じく環境変数のパス（無ければ既定）を使う。在るのに
# 答えなかったときは、統合先を取り違えないよう止める。
#
# 環境変数: CCNAVI_INTEGRATION_BRANCH / CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/
#   CCNAVI_SYNC_RETRIES（観測ずれの確かめ直しの回数、既定 3）/ CCNAVI_SYNC_RETRY_WAIT（その間隔の秒、既定 5）/
#   CCNAVI_SYNC_TIMEOUT（ls-remote・fetch 1 回のタイムアウト監視の秒、既定 60）
# 終了コード: 0 全部取り込んだ（取り込むものが無いを含む） / 1 止めた親子のチケットかリポジトリがある /
#           2 引数か環境の誤り / 3 取り込みの後の検査で止める理由があったのに取り込み状態を書けなかった

set -eu

. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-sync.sh [<P>...]
sh .ccnavi/scripts/ccnavi-sync.sh --forget <P>...

  親チケット <P>（識別子。省けば .claude/worktrees/ の下の親のワークツリー全部）の親のブランチ
  （親チケットの branch:、無ければ <P> と同じ名前）をリモートから取り込み、
  親子のチケットの取り込み状態と統合先の取り込み結果を書く。分かれていれば merge し、衝突したら取りやめてユーザに回す。
  リモートから消えた親のブランチは、統合先の done/ を見て「閉じた」か「消えた」かを決める。
  取り込んだ後、親子のチケットを判定し直し、止める理由があれば取り込み状態を blocked にする。
  取り込み状態は親のワークツリーを片付けても消えない（削除せずに残す）。

  --forget <P>...  ユーザが打つ。捨てた親子のチケットの取り込み状態を消す（親のワークツリーを片付けた後だけ）。
                   消すと、その名前で切り直した親子のチケットは取り込み状態の無いものとして扱われる。

  統合先: CCNAVI_INTEGRATION_BRANCH（環境変数か .claude/settings.local.json の env）、
          空ならホストのデフォルトブランチ
USAGE
}

forget=no
case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
--forget)
	forget=yes
	shift
	if [ "$#" -eq 0 ]; then
		printf 'ccnavi-sync: --forget には親の識別子が要ります。\n' >&2
		exit 2
	fi
	;;
-*)
	printf 'ccnavi-sync: %s は受けません。\n' "$1" >&2
	usage >&2
	exit 2
	;;
esac

for want in ${1+"$@"}; do
	if ! ccnavi_is_ident "$want"; then
		printf 'ccnavi-sync: %s は親の識別子の形ではありません（親のブランチ名ではなく識別子を渡します）。\n' "$want" >&2
		exit 2
	fi
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

# 後始末。この sh が始めた merge の途中（目印の変数 sync_merging）で切られたら取りやめ、ロックを外す。
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

log_info 受け付けた -- "args=$#" "forget=$forget"

# ---- ユーザが打つ --forget（親子のチケットの取り込み状態を消す）。ネットワークも実行ファイルも使わない。

if [ "$forget" = yes ]; then
	forget_rc=0
	for want in "$@"; do
		if [ -e "$root/.claude/worktrees/$want" ] || [ -L "$root/.claude/worktrees/$want" ]; then
			printf '%s: 親のワークツリー（.claude/worktrees/%s）がまだある。取り込み状態を消すのは捨てた親子のチケットだけ。先に片付けてから打ってください（sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s）\n' \
				"$want" "$want" "$here_sh" "$want"
			forget_rc=1
			continue
		fi
		fg_found=no
		for fg_record in "$state"/sync/*/families/"$want"; do
			[ -e "$fg_record" ] || [ -L "$fg_record" ] || continue
			fg_key="${fg_record%/families/*}"
			fg_key="${fg_key##*/}"
			if [ -L "$state/sync/$fg_key" ] || [ -L "$state/sync/$fg_key/families" ]; then
				printf '%s: 取り込み状態の置き場（sync/%s）がシンボリックリンク。辿らないので消さない。ユーザが中身を確かめる\n' "$want" "$fg_key"
				forget_rc=1
				continue
			fi
			fg_found=yes
			fg_lock_rc=0
			ccnavi_lock_take "$root" "$fg_key" "$want" "$lock_wait" || fg_lock_rc=$?
			if [ "$fg_lock_rc" -ne 0 ]; then
				printf '%s: 他の操作がロックを持っている。終わってから打ち直してください\n' "$want"
				forget_rc=1
				continue
			fi
			fg_state=$(ccnavi_record_get "$fg_record" state)
			if rm -f "$fg_record"; then
				printf '%s: 親子のチケットの取り込み状態（sync/%s/families/%s。state %s）を消した。この名前の親子のチケットは取り込み状態の無いものとして扱われる\n' \
					"$want" "$fg_key" "$want" "${fg_state:-?}"
				log_info 親子のチケットの取り込み状態を消した -- "family=$want" "repo=$fg_key" "state=$fg_state"
			else
				printf '%s: 親子のチケットの取り込み状態（sync/%s/families/%s）を消せなかった\n' "$want" "$fg_key" "$want"
				forget_rc=1
			fi
			ccnavi_lock_drop
		done
		[ "$fg_found" = yes ] || printf '%s: 親子のチケットの取り込み状態は無い（消すものは無い）\n' "$want"
	done
	exit "$forget_rc"
fi

# `git rev-parse --git-path <名前>` をツリーからのパスにする（ワークツリーでは git ディレクトリが別）。
git_path() {
	gp_rel=$(git -C "$1" rev-parse --git-path "$2" 2>/dev/null || :)
	case "$gp_rel" in
	'') printf '%s\n' "$1/.git/$2" ;;
	/* | [A-Za-z]:*) printf '%s\n' "$gp_rel" ;;
	*) printf '%s\n' "$1/$gp_rel" ;;
	esac
}

# ---- 置き場のパスと、settings.local.json の統合先。実行ファイルに聞く（JSON は sh で読まない）。

sync_info=""
info_from=""
if bin=$(ccnavi_bin "$root"); then
	info_from="$bin"
	sync_info=$("$bin" --root "$root" sync paths 2>"$scratch/info" </dev/null) || info_from="failed:$bin"
elif [ -f "$root/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
	info_from="uv run python -m ccnavi"
	sync_info=$(cd "$root" && uv run --quiet python -m ccnavi --root "$root" sync paths 2>"$scratch/info" </dev/null) ||
		info_from="failed:uv run python -m ccnavi"
fi
# Windows の実行ファイルの CRLF を落とす（1 行 1 項目の値の末尾に CR を残さない）。
sync_info=$(printf '%s\n' "$sync_info" | tr -d '\r')
case "$info_from" in
failed:*)
	printf 'ccnavi-sync: 実行ファイル（%s）が置き場のパスと統合先の設定を返さなかった（%s）。統合先を取り違えないよう止めた。\n' \
		"${info_from#failed:}" "$(head -n 1 "$scratch/info" 2>/dev/null)" >&2
	exit 2
	;;
esac
info() {
	printf '%s\n' "$sync_info" | sed -n "s/^$1 //p" | head -n 1
}

# 実行ファイルを起こす。`sync paths` を答えたのと同じもの。<引数>...
run_ccnavi() {
	case "$info_from" in
	'uv run python -m ccnavi')
		(cd "$root" && uv run --quiet python -m ccnavi --root "$root" "$@" </dev/null)
		;;
	*)
		"$info_from" --root "$root" "$@" </dev/null
		;;
	esac
}

# 親子のチケットの親のブランチ名。<P>
#
# 実行ファイルの `c1 family <P>` の `branch` の行（承認済みの親チケットの `branch:`、無ければ識別子）。
# 実行ファイルが無ければ識別子（前の動き）。在るのに答えない・`branch` の行が無い（`branch_refused`）・
# 答えが親のブランチ名の形でなければ 1（呼ぶ側はその親子のチケットを取り込まずに止めたと言う）。
family_branch() {
	if [ -z "$info_from" ]; then
		printf '%s\n' "$1"
		return 0
	fi
	fb_out=$(run_ccnavi c1 family "$1" 2>/dev/null | tr -d '\r') || fb_out=""
	[ "$(printf '%s\n' "$fb_out" | head -n 1)" = "c1 1" ] || return 1
	fb_name=$(printf '%s\n' "$fb_out" | sed -n 's/^branch //p' | head -n 1)
	[ -n "$fb_name" ] || return 1
	ccnavi_branch_ok "$fb_name" "$1" || return 1
	printf '%s\n' "$fb_name"
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

# ---- 統合先の名前

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

# ---- 統合先の取り込み結果

# 統合先の取り込み結果を書く。<リポジトリ> <取り込み状態の名前> <統合先>
#
# 統合先の先頭から、判定に要るものをファイルのまま同じ構成でコピーする（Python の読み方を変えずに
# 済む形）。`git archive` は .gitattributes（export-ignore・export-subst・eol）で中身を変えるので使わず、
# `ls-tree` と `cat-file blob` でコミットのバイト列そのものを書く。シンボリックリンク（120000）と
# サブモジュールはコピーしない（読む側がリンクを辿らない決まりと二重にする）。
# リポジトリごとのロックの中で、一時の置き場に組んでから入れ替える。先頭が前と同じならコピーしない。
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
			*) continue ;; # リンク・サブモジュールはコピーしない
			esac
			case "$wi_file" in
			'"'* | /* | *../*)
				# 引用された名前（改行や制御文字を含む名前）と、外へ出るパスはコピーしない。
				log_warn 統合先の取り込み結果にコピーできない名前を飛ばした -- "repo=$wi_key"
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
			# 入れ替えに落ちたら、前の取り込み結果を戻す。
			[ -d "$wi_dest.old.$$" ] && mv "$wi_dest.old.$$" "$wi_dest" 2>/dev/null
			wi_ok=no
		fi
	fi
	rm -rf "$wi_tmp"
	ccnavi_lock_drop
	[ "$wi_ok" = yes ]
}

# 親のブランチ名が決まらなかった親子のチケットの文面。<P>
branch_refused() {
	br_why=$(run_ccnavi c1 family "$1" 2>/dev/null | tr -d '\r' | sed -n 's/^branch_refused //p' | head -n 1)
	if [ -n "$br_why" ]; then
		printf '%s: 親のブランチ名が使えない（%s）。取り込まずに止めた。承認済みの親チケットの branch: をユーザが確かめてください\n' "$1" "$br_why"
	else
		printf '%s: 実行ファイルが親のブランチ名を答えない（c1 family）。取り込まずに止めた。実行ファイルを新しくしてください\n' "$1"
	fi
}

# ---- 親子のチケットを集める。<P><タブ><ツリー><タブ><リポジトリの取り込み状態の名前><タブ><親のブランチ名> を 1 行ずつ。

: >"$scratch/families"
: >"$scratch/repos"
repo_dir_of() {
	# <取り込み状態の名前> → そのリポジトリのディレクトリ
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
	printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$af_key" "$3" >>"$scratch/families"
	grep -F -x -q -- "$af_key" "$scratch/repos" 2>/dev/null || printf '%s\n' "$af_key" >>"$scratch/repos"
}

if [ "$#" -gt 0 ]; then
	for want in "$@"; do
		has_family_line "$want" && continue
		tree="$root/.claude/worktrees/$want"
		if ! want_branch=$(family_branch "$want"); then
			branch_refused "$want"
			fail_note
			continue
		fi
		if [ ! -d "$tree" ] || [ -L "$tree" ]; then
			printf '%s: 親のワークツリー（.claude/worktrees/%s）が無い。切り直してから打ち直してください（sh %s/ccnavi-git.sh fetch origin %s のあと worktree add .claude/worktrees/%s -b %s origin/%s）\n' \
				"$want" "$want" "$here_sh" "$want_branch" "$want" "$want_branch" "$want_branch"
			fail_note
			continue
		fi
		if ! ccnavi_parent_tree "$tree" "$want"; then
			printf '%s: .claude/worktrees/%s に親チケットも提案も無い。親のワークツリーではないので取り込まない\n' "$want" "$want"
			fail_note
			continue
		fi
		add_family "$want" "$tree" "$want_branch"
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
		[ -n "$branch" ] && [ "$branch" != HEAD ] || continue
		ccnavi_parent_tree "$tree" "$name" || continue
		# 親のブランチ（承認済みの親チケットの branch:、無ければ識別子）をチェックアウトしているものだけ。
		if ! name_branch=$(family_branch "$name"); then
			branch_refused "$name"
			fail_note
			continue
		fi
		[ "$branch" = "$name_branch" ] || continue
		add_family "$name" "$tree" "$name_branch"
	done
fi

# ---- 親子のチケット 1 つ。<P> <ツリー> <取り込み状態の名前> <リポジトリ> <統合先> <一覧> <親のブランチ名>
#
# 取り込み状態の鍵とロックは識別子 P、ref・fetch・ls-remote の一覧と取り込み状態の branch は親のブランチ名 B。

sync_family() {
	P="$1"
	tree="$2"
	key="$3"
	repo="$4"
	integ="$5"
	heads="$6"
	B="${7:-$1}"
	record=$(ccnavi_family_record "$root" "$key" "$P")

	branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ "$branch" != "$B" ]; then
		printf '%s: 親のワークツリーが %s の上に居る。親のブランチ %s に戻してから打ち直してください\n' "$P" "${branch:-（ブランチの外）}" "$B"
		fail_note
		return 0
	fi
	kept_branch=$(ccnavi_record_get "$record" branch)
	if [ "$kept_branch" = "$P" ] && [ "$B" != "$P" ]; then
		printf '%s: 親のブランチを承認済みの branch: の %s へ移した後、まだ送っていない（取り込み状態は %s のまま）。親のワークツリーで sh %s/ccnavi-git.sh push -u origin %s を打つと取り込み状態が書き直る。取り込まずに止めた\n' \
			"$P" "$B" "$P" "$here_sh" "$B"
		fail_note
		# 移った後の push を待つだけなので、取り込みの後の検査で blocked にしない。
		printf '%s\n' "$P" >>"$scratch/unchecked"
		return 0
	fi
	if [ -n "$kept_branch" ] && [ "$kept_branch" != "$B" ]; then
		printf '%s: 取り込み状態の親のブランチ（%s）と、親チケットが名乗る親のブランチ（%s）が違う。取り込まずに止めた。branch: を取り込み状態の名前に戻すか、親子のチケットを捨てるならユーザが sh %s/ccnavi-sync.sh --forget %s で取り込み状態を消す\n' \
			"$P" "$kept_branch" "$B" "$here_sh" "$P"
		fail_note
		return 0
	fi

	# set -e の下なので、戻り値は || で受ける（そのまま打つと、取れなかった時点で sh ごと抜ける）。
	lock_rc=0
	ccnavi_lock_take "$root" "$key" "$P" "$lock_wait" || lock_rc=$?
	case "$lock_rc" in
	0) ;;
	2)
		printf '%s: 古いロック（%s/locks/%s/%s）を強制取得する途中で止まり、元に戻せなかった。ユーザが中身を見て片付ける\n' "$P" "$state" "$key" "$P"
		fail_note
		return 0
		;;
	*)
		printf '%s: 他の操作がロックを持っている（%s）。終わってから打ち直してください\n' "$P" \
			"$(ccnavi_lock_owner "$state/locks/$key/$P")"
		fail_note
		return 0
		;;
	esac

	busy=$(busy_state "$tree")
	if [ -n "$busy" ]; then
		printf '%s: 親のワークツリーに途中の操作（%s）がある。済ませるか取りやめてから打ち直してください（この sh は触らない）\n' "$P" "$busy"
		fail_note
	elif has_head "$heads" "$B"; then
		sync_present
	else
		sync_absent
	fi
	ccnavi_lock_drop
	return 0
}

# P がリモートにある。取ってきて、早送りか merge。
sync_present() {
	if ! fetch_one "$tree" "$B"; then
		printf '%s: 親のブランチ %s を取ってこられなかった（%s）\n' "$P" "$B" "$(head -n 1 "$scratch/err")"
		fail_note
		return 0
	fi
	remote_sha=$(git -C "$tree" rev-parse --verify --quiet "refs/remotes/origin/$B^{commit}" 2>/dev/null || :)
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
		printf '%s: 手元が %s 件先に進んでいる（まだ送っていない）。取り込むものは無い\n' "$P" "$ahead"
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
		# 先に見て言う。
		if ! git -C "$tree" diff --cached --quiet 2>/dev/null; then
			printf '%s: リモートと分かれていて merge が要るが、ステージ済みの変更がある。コミットするか sh %s/ccnavi-git.sh restore --staged <パス> で外してから打ち直してください\n' \
				"$P" "$here_sh"
			fail_note
		else
			# 途中の操作が無いのは確かめてあるので、この後の MERGE_HEAD はこの merge のもの。
			sync_merging="$tree"
			if LC_ALL=C git -C "$tree" merge --no-edit --quiet -m "ccnavi: origin/$B を取り込む" "$remote_sha" \
				</dev/null >"$scratch/out" 2>"$scratch/err"; then
				printf '%s: リモートと分かれていたので merge で取り込んだ\n' "$P"
			else
				conflicted=$(git -C "$tree" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')
				if [ -e "$(git_path "$tree" MERGE_HEAD)" ]; then
					git -C "$tree" merge --abort >/dev/null 2>&1 || :
				fi
				if [ -n "$conflicted" ]; then
					printf '%s: リモートと分かれていて merge が衝突した（%s）。取り込みをやめた（merge --abort）。どちらを採るかはユーザが決める\n' \
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
	ccnavi_record_write "$record" remote origin branch "$B" sha "$remote_sha" \
		fetched_at "$(date +%s)" state present reason "" ||
		printf '%s: 親子のチケットの取り込み状態（%s）を書けなかった\n' "$P" "$record"
	return 0
}

# frontmatter の行だけ（1 行目の `---` から次の `---` の手前まで）。行末の CR は落とす。標準入力から。
# 1 行目が `---` でなければ何も出さない。本文に同じ語を書いた行を欄と読まないために、欄はここから拾う。
frontmatter_of() {
	awk '
		{ sub(/\r$/, "") }
		NR == 1 { if ($0 !~ /^---[ \t]*$/) exit; next }
		/^---[ \t]*$/ { exit }
		{ print }
	'
}

# frontmatter の行頭にある欄 <名前> の値（最初の 1 つ）。前後の空白と、値を囲む引用符を外す。
# 値の無い欄・空文字（`""`・`''`）は空を出す。YAML と同じく、引用符の外の ` #` から後ろは注記として落とし、
# 引用符の無い `null`・`Null`・`NULL`・`~` は値が無いとみなす（Python の syncstate._text と同じ結果にする）。
# 標準入力は frontmatter_of の出力。
front_field() {
	awk -v key="$1" -v q="'" '
		index($0, key ":") == 1 {
			v = substr($0, length(key) + 2)
			sub(/^[ \t]+/, "", v)
			c = substr(v, 1, 1)
			if (c == "\"" || c == q) {
				rest = substr(v, 2)
				end = index(rest, c)
				v = end > 0 ? substr(rest, 1, end - 1) : rest
			} else {
				if (c == "#") v = ""
				p = match(v, /[ \t]#/)
				if (p > 0) v = substr(v, 1, p - 1)
				sub(/[ \t]+$/, "", v)
				if (v == "null" || v == "Null" || v == "NULL" || v == "~") v = ""
			}
			sub(/^[ \t]+/, "", v)
			sub(/[ \t]+$/, "", v)
			print v
			exit
		}
	'
}

# 承認の時刻（`approved_at`）。承認で欄を書いていた頃の古い形だけが持つ。拾うのは frontmatter の行頭の
# `ccnavi_approved:` の中だけで、Python（syncstate._copy_fields）と同じ範囲にする。本文や block scalar
# （`rationale: |` の下の行など）に同じ語を書いた行は拾わない。中の形はブロックの形（次の行から字下げした
# `  approved_at: X`。字下げがいちばん浅い行だけを見る）と流れの形（`{approved_at: "X", ...}`）。
# `ccnavi_approved: |` のように対応表でない値なら何も出さない。標準入力は frontmatter_of の出力。
approved_at_of() {
	awk -v q="'" '
		function value(v) {
			sub(/^[ \t]+/, "", v)
			c = substr(v, 1, 1)
			if (c == "\"" || c == q) v = substr(v, 2)
			n = match(v, "[\"" q ",} \t]")
			if (n > 0) v = substr(v, 1, n - 1)
			print v
			exit
		}
		state == "" {
			if (index($0, "ccnavi_approved:") != 1) next
			rest = substr($0, length("ccnavi_approved:") + 1)
			sub(/^[ \t]+/, "", rest)
			if (substr(rest, 1, 1) == "#") rest = ""
			if (rest == "") { state = "block"; next }
			if (substr(rest, 1, 1) != "{") exit
			state = "flow"
			if (match(rest, /[{,][ \t]*approved_at:/)) {
				rest = substr(rest, RSTART + RLENGTH)
				value(rest)
			}
			if (index(rest, "}") > 0) exit
			next
		}
		/^[ \t]*(#.*)?$/ { next }
		$0 !~ /^[ \t]/ { exit }
		state == "flow" {
			line = $0
			if (match(line, /(^|[{,])[ \t]*approved_at:/)) value(substr(line, RSTART + RLENGTH))
			if (index(line, "}") > 0) exit
			next
		}
		{
			match($0, /^[ \t]*/)
			if (depth == "") depth = RLENGTH
			if (RLENGTH != depth) next
			line = substr($0, RLENGTH + 1)
			if (index(line, "approved_at:") == 1) value(substr(line, length("approved_at:") + 1))
		}
	'
}

# 2 つの frontmatter（$1 が親のワークツリーの親チケット、$2 が統合先か退避の親チケット）が同じ親か。
# スクリプトだけが書く欄を base_sha → started_at → cancelled_at の順に見て、最初に両方が値を持つ
# 欄が同じかで決める。空どうしは一致としない。どの欄でも照合できないときは同じとしない。
# 承認で欄を書いていた頃の古い形どうし（両方に 3 つとも無い）だけは、両方にある approved_at で比べる。
# Python の syncstate.same_parent と同じ見方。
same_parent() {
	sp_seen=""
	for sp_key in base_sha started_at cancelled_at; do
		sp_a=$(printf '%s\n' "$1" | front_field "$sp_key")
		sp_b=$(printf '%s\n' "$2" | front_field "$sp_key")
		if [ -n "$sp_a" ] && [ -n "$sp_b" ]; then
			[ "$sp_a" = "$sp_b" ]
			return
		fi
		[ -z "$sp_a$sp_b" ] || sp_seen=yes
	done
	[ -z "$sp_seen" ] || return 1
	sp_a=$(printf '%s\n' "$1" | approved_at_of)
	[ -n "$sp_a" ] || return 1
	[ "$(printf '%s\n' "$2" | approved_at_of)" = "$sp_a" ]
}

# 統合先の done/ に、この親子のチケットの親チケットがあるか。統合先は取ってきた後の refs/remotes/origin/<統合先>。
#
# 在るだけでは見ない。識別子（`ticket:`）が P で、子（`parent:`）でなく、親のワークツリーの親チケットと
# 同じ親だと言えるときだけ「閉じた」とする（同じ識別子の古い親子のチケットを、今のものと読まない）。
# 同じ親かは、スクリプトだけが書く欄を base_sha → started_at → cancelled_at の順に見て、最初に両方が値を持つ
# 欄が同じかで決める。空どうしは一致としない。どの欄でも照合できないとき、親のワークツリーに承認済みの
# 親チケットが無いときは閉じていない側に倒す（止めて戻し方を出す）。
# 承認で欄を書いていた頃の古い形どうし（両方に 3 つとも無い）だけは、両方にある approved_at で比べる。
closed_in_integration() {
	ci_body=$(git -C "$repo" show "refs/remotes/origin/$integ:$approved/done/$P.md" 2>/dev/null) || return 1
	ci_theirs=$(printf '%s\n' "$ci_body" | frontmatter_of)
	ci_id=$(printf '%s\n' "$ci_theirs" | front_field ticket)
	[ "$ci_id" = "$P" ] || return 1
	printf '%s\n' "$ci_theirs" | grep -q '^parent:' && return 1
	ci_file=""
	for ci_try in "$tree/$approved/doing/$P.md" "$tree/$approved/done/$P.md"; do
		[ -f "$ci_try" ] || continue
		ci_file="$ci_try"
		break
	done
	[ -n "$ci_file" ] || return 1
	same_parent "$(frontmatter_of <"$ci_file")" "$ci_theirs"
}

# 手元の退避（ready が閉じた親子のチケットを移した先）に、この親子のチケットの親があるか。
# 識別子（`ticket:`）が P で、子（`parent:`）でないときだけ。退避は手元にしか無いので、別の機械では
# 見えない（そのときは ccnavi-review.sh merged に聞く）。途中のリンクは辿らない。
archived_locally() {
	al_dir="$root/logs/archive/$key/done"
	for al_part in "$root/logs" "$root/logs/archive" "$root/logs/archive/$key" "$al_dir" "$al_dir/$P.md"; do
		[ -L "$al_part" ] && return 1
	done
	[ -f "$al_dir/$P.md" ] || return 1
	# 欄は frontmatter の行頭から拾う（closed_in_integration と同じ。本文や block scalar の行は読まない）。
	al_front=$(frontmatter_of <"$al_dir/$P.md")
	al_id=$(printf '%s\n' "$al_front" | front_field ticket)
	[ "$al_id" = "$P" ] || return 1
	[ -z "$(printf '%s\n' "$al_front" | front_field parent)" ] || return 1
	# 親のワークツリーに同じ識別子の承認済みチケットが残っていれば、着手と取り消しの欄でも同じ親と
	# 言えるときだけ（same_parent。同じ識別子の別の親子のチケットの退避を、今のものと読まない）。
	for al_file in "$tree/$approved/doing/$P.md" "$tree/$approved/done/$P.md"; do
		[ -f "$al_file" ] || continue
		same_parent "$(frontmatter_of <"$al_file")" "$al_front"
		return
	done
	return 0
}

# 統合先の done/ に閉じた記録があった。取り込み状態を closed にして言う。
closed_by_integration() {
	ccnavi_record_write "$record" remote origin branch "$B" sha "$kept_sha" \
		fetched_at "$(date +%s)" state closed reason "統合先の done/ に親チケットがある" || :
	printf '%s: リモートから消えたが、統合先（%s）の done/ に閉じた記録がある（閉じた親子のチケット）。親のワークツリーは片付けてよい\n' "$P" "$integ"
}

# P がリモートに無い。閉じたか、消えたか。
sync_absent() {
	kept_sha=$(ccnavi_record_get "$record" sha)
	trace=no
	if [ -f "$record" ]; then
		trace=record
	elif git -C "$tree" show-ref --verify --quiet "refs/remotes/origin/$B" ||
		git -C "$tree" config --get "branch.$B.remote" >/dev/null 2>&1; then
		trace=ref
		[ -n "$kept_sha" ] || kept_sha=$(git -C "$tree" rev-parse --verify --quiet "refs/remotes/origin/$B" 2>/dev/null || :)
	fi
	# 取り込み状態の有無より先に、統合先で閉じているかを見る。
	if closed_in_integration; then
		closed_by_integration
		return 0
	fi
	if [ "$trace" = no ]; then
		printf '%s: リモートに無い（まだ送っていない親子のチケット）。今の手元の動きのまま\n' "$P"
		return 0
	fi
	# ccnavi-review.sh の道具（gh / glab / curl とトークン）で、MR がマージ済みかを先に聞く。答えは
	# merged <番号> / none（マージされた MR が無い）/ それ以外（道具・トークンが無い、API が落ちた）。
	# 統合先の done/ を待つ確かめ直しは、答えが得られないときだけ行う（統合先には閉じたチケットを
	# 残さないので、マージ済みと分かれば待つものは無い）。
	merged_rc=0
	merged=$(cd "$tree" && sh "$here_sh/ccnavi-review.sh" merged 2>/dev/null </dev/null) || merged_rc=$?
	merged=$(printf '%s\n' "$merged" | head -n 1)
	case "$merged_rc:$merged" in
	*:'merged '*)
		ccnavi_record_write "$record" remote origin branch "$B" sha "$kept_sha" \
			fetched_at "$(date +%s)" state closed reason "マージリクエスト（${merged#merged }）がマージ済み" || :
		printf '%s: リモートから消えたが、マージリクエスト（%s）はマージ済み（閉じた親子のチケット）。親のワークツリーは片付けてよい\n' \
			"$P" "${merged#merged }"
		return 0
		;;
	0:none) ;;
	*)
		tries=0
		while [ "$tries" -lt "$retries" ]; do
			tries=$((tries + 1))
			# 観測ずれ。ホストはマージの後に P を消すが、読み取りの複製が遅れて done/ がまだ見えないことがある。
			sleep "$retry_wait"
			fetch_one "$repo" "$integ" || :
			if closed_in_integration; then
				closed_by_integration
				return 0
			fi
		done
		# マージ済みかを確かめられないときだけ、手元の退避（ready が移した先）を閉じた証拠として補う。
		if archived_locally; then
			ccnavi_record_write "$record" remote origin branch "$B" sha "$kept_sha" \
				fetched_at "$(date +%s)" state closed reason "手元の退避（logs/archive/）に親がある" || :
			printf '%s: リモートから消えた。マージ済みかは確かめられなかったが、手元の退避（logs/archive/%s/done/）に閉じた記録がある（閉じた親子のチケット）。親のワークツリーは片付けてよい\n' "$P" "$key"
			return 0
		fi
		printf '%s: 親のブランチがリモートに無く、統合先（%s）にも閉じた記録が無い。マージリクエストがマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった。gh・glab か、curl とトークンが要る）。取り込み状態は変えずに止めた。確かめられる道具を用意して打ち直すか、ユーザが確かめてください\n' "$P" "$integ"
		fail_note
		return 0
		;;
	esac
	if [ "$trace" = record ]; then
		ccnavi_record_write "$record" remote origin branch "$B" sha "$kept_sha" \
			fetched_at "$(date +%s)" state gone reason "リモートにも統合先の done/ にも無い" ||
			printf '%s: 親子のチケットの取り込み状態（%s）を書けなかった\n' "$P" "$record"
		printf '%s: 親のブランチ %s がリモートに無い。統合先（%s）にも閉じた記録が無いので、この親子のチケットの状態を決められない。親子のチケットを止めた（取り込み状態は gone。このブランチへの push は通らない）\n' "$P" "$B" "$integ"
	else
		printf '%s: 親のブランチ %s がリモートに無い。統合先（%s）にも閉じた記録が無いので、この親子のチケットの状態を決められない。送った形跡（origin/%s か追跡の設定）はあるが取り込み状態が無いので、取り込み状態は作らずに止めた\n' "$P" "$B" "$integ" "$B"
	fi
	printf '  戻し方 1（改名・消し間違い）: 元の名前 %s でブランチを作り直す。端末なら git push origin %s:refs/heads/%s（取り込み状態にある、最後に取り込んだか送った %s の先頭）、GitHub ならマージリクエストの画面の「Restore branch」、GitLab ならマージリクエストの refs/merge-requests/<番号>/head から %s を作る。戻したら sh %s/ccnavi-sync.sh %s を打ち直す\n' \
		"$B" "${kept_sha:-<最後に取り込んだ sha>}" "$B" "$B" "$B" "$here_sh" "$P"
	printf '  戻し方 2（親子のチケットを捨てた）: 親のワークツリーを片付け（sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s）、ユーザが sh %s/ccnavi-sync.sh --forget %s を打つと親子のチケットの取り込み状態が消える（同じ名前で切り直せる。エージェントは打たない）\n' \
		"$here_sh" "$P" "$here_sh" "$P"
	fail_note
	return 0
}

# ---- 取り込みの後の検査

# present の親子のチケットを判定し直し、error があれば blocked にする。<P> <取り込み状態の名前>
check_family() {
	cf_record=$(ccnavi_family_record "$root" "$2" "$1")
	[ "$(ccnavi_record_get "$cf_record" state)" = present ] || return 0
	if [ -z "$info_from" ]; then
		printf '%s: 実行ファイルが無いので、取り込みの後の検査（判定し直し）はしなかった\n' "$1"
		return 0
	fi
	cf_rc=0
	run_ccnavi sync check "$1" "$2" >"$scratch/check-raw" 2>"$scratch/check-err" || cf_rc=$?
	tr -d '\r' <"$scratch/check-raw" >"$scratch/check"
	if [ "$(head -n 1 "$scratch/check" 2>/dev/null)" != "check 1" ]; then
		# 検査を実行できなかった（古い実行ファイルが副命令を知らない、落ちた）。検査の error ではない
		# ので親子のチケットは止めない。この検査が入る前と同じ動き。
		printf '%s: 注意: 取り込みの後の検査を実行できなかった（%s）。親子のチケットは止めていない。実行ファイルを新しくして打ち直してください\n' \
			"$1" "$(head -n 1 "$scratch/check-err" 2>/dev/null)"
		log_warn 取り込みの後の検査を実行できなかった -- "family=$1" "rc=$cf_rc"
		return 0
	fi
	sed -n 's/^warn /  注意: /p' "$scratch/check"
	[ "$cf_rc" -ne 0 ] || return 0
	cf_reason=$(sed -n 's/^error //p' "$scratch/check" | head -n 1)
	[ -n "$cf_reason" ] || cf_reason="取り込みの後の検査が落ちた（$(head -n 1 "$scratch/check-err" 2>/dev/null)）"
	cf_try=0
	cf_done=""
	while [ "$cf_try" -lt 3 ] && [ -z "$cf_done" ]; do
		cf_try=$((cf_try + 1))
		cf_lock_rc=0
		ccnavi_lock_take "$root" "$2" "$1" "$lock_wait" || cf_lock_rc=$?
		[ "$cf_lock_rc" -eq 0 ] || continue
		# 検査の間に並行する sync が取り込み状態を書き換えていたら（gone・closed・blocked）、上書きしない。
		cf_now=$(ccnavi_record_get "$cf_record" state)
		if [ "$cf_now" != present ]; then
			printf '%s: 取り込みの後の検査で止める理由があったが、その間に親子のチケットの取り込み状態が %s に変わった。書き換えない（%s）\n' \
				"$1" "${cf_now:-（無い）}" "$cf_reason"
			ccnavi_lock_drop
			fail_note
			return 0
		fi
		cf_branch=$(ccnavi_record_get "$cf_record" branch)
		if ccnavi_record_write "$cf_record" remote origin branch "${cf_branch:-$1}" sha "$(ccnavi_record_get "$cf_record" sha)" \
			fetched_at "$(ccnavi_record_get "$cf_record" fetched_at)" state blocked reason "$cf_reason"; then
			cf_done=yes
		fi
		ccnavi_lock_drop
		[ -n "$cf_done" ] || sleep 1
	done
	if [ -n "$cf_done" ]; then
		printf '%s: 取り込みの後の検査で親子のチケットを止めた（取り込み状態を blocked にした）。%s\n' "$1" "$cf_reason"
		sed -n 's/^error /  - /p' "$scratch/check"
		printf '  直してから、オンラインで sh %s/ccnavi-sync.sh %s を打ち直してください（検査し直して通れば present に戻る）\n' "$here_sh" "$1"
		fail_note
	else
		printf '%s: 取り込みの後の検査で止める理由があったが、ロックが取れないか親子のチケットの取り込み状態（%s）を書けず、3 回試しても止められなかった（%s）。親子のチケットはまだ止まっていない。打ち直してください\n' \
			"$1" "$cf_record" "$cf_reason"
		printf 'fail\n' >>"$scratch/unblocked"
		fail_note
	fi
	return 0
}

# ---- リポジトリごと

while IFS= read -r key <&4; do
	[ -n "$key" ] || continue
	repo=$(repo_dir_of "$key")
	has_family=no
	repo_has_families "$key" && has_family=yes
	# 親子のチケットの無いリポジトリ（引数を省いた回のプロジェクトなど）で落ちても、名指しして続け、1 にしない。
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
		printf '%sリモートのブランチの一覧を取ってこられなかった（オフラインか、認証の失敗。%s）。止める\n' "$label" \
			"$(head -n 1 "$scratch/err")"
		repo_fail
		continue
	fi
	if [ -n "$integration_want" ]; then
		if ! has_head "$heads" "$integration_want"; then
			printf '%s統合先 %s（%s）がリモートに無い。設定を直してください。取り込みを止めた\n' "$label" \
				"$integration_want" "$(source_words "$integration_source")"
			repo_fail
			continue
		fi
		integ="$integration_want"
	elif ! integ=$(default_branch "$repo" "$heads"); then
		printf '%s統合先が決まらない（ホストのデフォルトブランチが読めず、main・master もリモートに無い）。CCNAVI_INTEGRATION_BRANCH を設定してください。取り込みを止めた\n' "$label"
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
	# 読む先は fd 3。中で起こす git が標準入力を読んでも、親子のチケットの一覧を読み取ってしまわない。
	while IFS="$tab" read -r fam_p fam_tree fam_key fam_branch <&3; do
		[ -n "$fam_p" ] || continue
		sync_family "$fam_p" "$fam_tree" "$fam_key" "$repo" "$integ" "$heads" "$fam_branch"
	done 3<"$scratch/these"
	if ! write_integration "$repo" "$key" "$integ"; then
		printf '%s統合先の取り込み結果（%s/sync/%s/integration）を書けなかった\n' "$label" "$state" "$key"
		repo_fail
	fi
	while IFS="$tab" read -r fam_p fam_tree fam_key fam_branch <&3; do
		[ -n "$fam_p" ] || continue
		grep -F -x -q -- "$fam_p" "$scratch/unchecked" 2>/dev/null && continue
		check_family "$fam_p" "$fam_key"
	done 3<"$scratch/these"
done 4<"$scratch/repos"

if [ -s "$scratch/unblocked" ]; then
	log_info 終わった -- "exit=3"
	exit 3
fi
if [ -s "$scratch/failed" ]; then
	log_info 終わった -- "exit=1"
	exit 1
fi
log_info 終わった -- "exit=0"
exit 0

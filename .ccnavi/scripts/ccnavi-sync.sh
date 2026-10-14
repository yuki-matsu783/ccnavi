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
# （親チケットか提案があり、HEAD のブランチ名を変換した識別子がフォルダ名と合うもの）を全部。
#
# 親のブランチ名は親のワークツリーの HEAD から読む（識別子からは逆算しない）。識別子はブランチ名の
# `/` を `-` に置き換えたもの。取り込み状態の鍵は識別子で、中の `branch` に親のブランチ名を書く
# （キャッシュ。判定には使わない。`/` を含む名前を state の置き場のパスに入れない）。HEAD から読めない・
# 識別子と合わなければ、その親子のチケットは取り込まずに止める。
#
# 親子のチケットの取り込み状態は、親のワークツリーを片付けても消さない。消すと、決まらないで止めていた
# 親子のチケット（gone など）が取り込み状態の無いものに戻り、止めが外れるため。消すのは、MR がマージされて
# 手元を片付けたとき（下の手元の片付け）と、ユーザが打つ `--forget <P>`（親のワークツリーを片付けた後に限る。
# ネットワークは使わない）だけ。
# エージェントからは組み込みの deny（builtin:guard-ticket-approval）が止める。
#
# リポジトリ（ワークスペース自身と、親子のチケットの元のプロジェクト）ごとに 1 回、次の順に行う。
#
#   1. `ls-remote --heads origin` で全ブランチの有無を得る。失敗したら（オフライン・認証）止める
#   2. 統合先の名前を決める。CCNAVI_INTEGRATION_BRANCH（環境変数、無ければ
#      .claude/settings.local.json の env）、空ならホストのデフォルトブランチ（`ls-remote --symref
#      origin HEAD`、読めなければ origin/HEAD・main・master）。設定した名前がリモートに無ければ、
#      既定にせずに止める
#   3. 統合先を fetch し、親子のチケットごとの取り込みの後、判定に要るもの（done/・共通レイヤー・自身のレイヤー・
#      .claude/settings.json）を統合先の取り込み結果 sync/<リポジトリ>/integration/ へ同じ構成でコピーし、head に
#      remote・branch・source・sha・fetched_at を書く。統合先の先頭が前と同じならコピーしない
#
# 親子のチケットごとに次を行う（ロックを待って取る）。
#
#   - 途中の操作（merge・cherry-pick・revert・rebase）があれば何もせず止める（ユーザの途中の
#     merge を取りやめない）
#   - P がリモートにある: fetch して、早送りできれば早送り、分かれていれば merge。merge は
#     索引が HEAD と同じときだけで、衝突したら、この sh が始めた merge だけを取りやめて
#     ユーザに回す。親子のチケットの取り込み状態を present で書く。続けて、取り込み記録の MR の番号で
#     MR がマージ済みかを聞き、マージ済みなら下の片付けをする（ホストはマージの後もブランチを残すことがある）
#   - P がリモートに無い: まず統合先の done/ にこの親子のチケットの親チケット（識別子が同じで、着手と取り消しの欄で
#     同じ親と言えるもの。closed_in_integration）があれば閉じた親子のチケット。統合先には閉じたチケットを
#     残さない（ready が退避して消し、squash でマージする）ので、ふつうは統合先の done/ には無い。
#     無く、送った形跡（取り込み状態・origin/<P>・追跡の設定）も無ければ、一度も送っていない親子のチケットで
#     今のまま。送った形跡があれば、先に ccnavi-review.sh merged <番号> に聞く。閉じたかの第一の根拠は MR がマージ済みか
#     で、手元の退避（logs/archive/）や取り込み状態の closed からは推定しない。答えが得られなければ観測ずれを疑い、
#     統合先を取り直して確かめ直す（既定 3 回、5 秒おき）。それでも得られなければ、取り込み状態は書き換えずに止める。
#     マージされていないと分かったときだけ、取り込み状態があれば gone を書いて止める
#     （取り込み状態が無ければ gone は書かずに止める）
#   - マージ済みと分かったら、手元を片付ける（実行ファイルの `sync tidy`。条件の確認も削除も実行ファイルが行う）。
#     親のワークツリー（未コミットの変更や git が無視しているファイルがあれば残す）と、親の手元のブランチ
#     （手元の先頭が MR の先頭に含まれ、未 push のコミットが無いときだけ）を消し、残ったものは理由と手で消す
#     コマンドを 1 行ずつ言う。何も残らなければ親の取り込み記録（とロック）も消す。MR の先頭の sha はホストが
#     答えた（ccnavi-review.sh merged <番号> の <sha>）。MR の番号は取り込み記録（ccnavi-start.sh が保存）から引く。
#     番号が無ければ、ブランチから MR を探す推定はせず、閉じたか判断できないと 1 行言う
#   - 親のワークツリーを片付けた後（ready の後）で取り込み記録が残る親子のチケットも（present でも gone・
#     blocked・closed でも）、同じく MR がマージ済みかを聞き、マージ済みなら同じ片付けをする。ブランチ名は
#     ホストが答えた MR の元ブランチを使う（取り込み記録の branch はキャッシュで使わない）。マージされていなければ
#     片付けない（present でなければ理由を 1 行言う）。確かめられなければ推定せずに止める。<P> を渡したときも同じ
#
# 統合先の取り込み結果を書いた後、present の親子のチケットごとに実行ファイルの `ccnavi sync check <P> <リポジトリ>` で
# 判定し直す（どのチケットを正とするかの検査と、承認済みチケットの判定し直し）。error があれば
# 親子のチケットの取り込み状態を blocked にして、理由を reason に書いて止める。判定（hook・承認・状態の操作）は
# blocked の親子のチケットを止める。解き方は、理由を直してから同じ P でこの sh をオンラインで打ち直すこと（取り込みで
# present に書き直してから検査し直すので、通れば present に戻る）。書く前にロックを取り直し、取り込み状態が
# まだ present かを確かめる（並行する sync が書いた gone・closed を上書きしない）。ロックが取れない・
# 書けないときは 3 回まで試し、それでも書けなければ終了コード 3 で終わる（止めるべき親子のチケットが止まって
# いない）。実行ファイルが検査を実行できなかった（古い実行ファイルが `sync check` を知らない、など。
# 答えの頭に `check 1` が無い）ときは、親子のチケットを止めずに警告だけ出す（検査の error とは分ける）。
#
# 実行ファイルはネットワークに出ない。ここが git で取ってくる。
# 置き場のパスと settings.local.json の読みは実行ファイル（`ccnavi sync paths`）に、閉じたかの見分け（チケットの
# YAML）は `ccnavi sync closed` に、親のワークツリーかは `ccnavi tree parent` に聞く（sh はチケットを読まない）。
# 実行ファイルが無ければ、ほかの sh と同じく既定のパス（固定）を使う。在るのに
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
  （親のワークツリーの HEAD のブランチ。/ を - に置き換えたものが <P>）をリモートから取り込み、
  親子のチケットの取り込み状態と統合先の取り込み結果を書く。分かれていれば merge し、衝突したら取りやめてユーザに回す。
  リモートから消えた親のブランチは、統合先の done/ を見て「閉じた」か「消えた」かを決める。
  取り込んだ後、親子のチケットを判定し直し、止める理由があれば取り込み状態を blocked にする。
  リモートから消えたブランチの MR がマージ済みなら、手元の親のブランチとワークツリー、取り込み状態を片付ける
  （未 push のコミットや未コミットの変更があれば消さずに理由を言う）。それ以外では、取り込み状態は親のワークツリーを
  片付けても消えない（削除せずに残す）。

  --forget <P>...  ユーザが実行します。捨てた親子のチケットの取り込み状態を削除します（親のワークツリーを片付けた後だけ）。
                   削除すると、その名前で切り直した親子のチケットは取り込み状態の無いものとして扱われる。

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
# 打った場所。手元の片付けが、自分の居るワークツリーを消さないために渡す。
sync_cwd="$(pwd -W 2>/dev/null || pwd)"

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
			printf '%s: 親のワークツリー（.claude/worktrees/%s）がまだある。取り込み状態を削除するのは捨てた親子のチケットだけ。先に片付けてから実行してください（sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s）\n' \
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
				printf '%s: 取り込み状態の置き場（sync/%s）がシンボリックリンク。辿らないので削除しません。ユーザが中身を確かめる\n' "$want" "$fg_key"
				forget_rc=1
				continue
			fi
			fg_found=yes
			fg_lock_rc=0
			ccnavi_lock_take "$root" "$fg_key" "$want" "$lock_wait" || fg_lock_rc=$?
			if [ "$fg_lock_rc" -ne 0 ]; then
				printf '%s: 他の操作がロックを持っている。終わってから再実行してください\n' "$want"
				forget_rc=1
				continue
			fi
			fg_state=$(ccnavi_record_get "$fg_record" state)
			if rm -f "$fg_record"; then
				printf '%s: 親子のチケットの取り込み状態（sync/%s/families/%s。state %s）を削除しました。この名前の親子のチケットは取り込み状態の無いものとして扱われる\n' \
					"$want" "$fg_key" "$want" "${fg_state:-?}"
				log_info 親子のチケットの取り込み状態を削除した -- "family=$want" "repo=$fg_key" "state=$fg_state"
			else
				printf '%s: 親子のチケットの取り込み状態（sync/%s/families/%s）を削除できませんでした。\n' "$want" "$fg_key" "$want"
				forget_rc=1
			fi
			ccnavi_lock_drop
		done
		[ "$fg_found" = yes ] || printf '%s: 親子のチケットの取り込み状態はありません（削除するものはありません）\n' "$want"
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
# Windows の実行ファイルの CRLF の CR を除く（1 行 1 項目の値の末尾に CR を残さない）。
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
# 親のワークツリー（.claude/worktrees/<P>）の HEAD から読む（ccnavi_head_branch）。HEAD のブランチ名を
# 変換（/ を - に置き換え）した識別子が <P> と一致するときだけ出す。ワークツリーが無い・ブランチの外なら 1、
# 一致しない・使えない名前なら 2（呼ぶ側はその親子のチケットを取り込まずに止めたと言う）。
family_branch() {
	ccnavi_head_branch "$root/.claude/worktrees/$1" "$1"
}
approved=$(info approved)
home=$(info home)
integration_local=$(info integration)
[ -n "$approved" ] || approved=.ccnavi/approved # 固定
[ -n "$home" ] || home=.ccnavi # 固定
approved="${approved%/}"
home="${home%/}"
projects=projects # 固定
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
# 失敗した理由（git の標準エラー）は $scratch/err に残る。<リポジトリ> <ブランチ>
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
	# 前に失敗した回の残り。
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
	for wi_path in "$approved/done" ".ccnavi/config/common" "$home/config/project" ".claude/settings.json"; do
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
			# 入れ替えに失敗したら、前の取り込み結果を戻す。
			[ -d "$wi_dest.old.$$" ] && mv "$wi_dest.old.$$" "$wi_dest" 2>/dev/null
			wi_ok=no
		fi
	fi
	rm -rf "$wi_tmp"
	ccnavi_lock_drop
	[ "$wi_ok" = yes ]
}

# 親のブランチ名が決まらなかった親子のチケットの文面。<P> <family_branch の終了コード>
branch_refused() {
	if [ "$2" = 1 ]; then
		printf '%s: 親のワークツリー（.claude/worktrees/%s）の HEAD からブランチ名を読めない（ブランチの外にいる）。取り込まずに止めた。ブランチ名の / を - に置き換えたものが %s になるブランチに戻してから再実行してください\n' "$1" "$1" "$1"
	else
		printf '%s: 親のワークツリー（.claude/worktrees/%s）の HEAD のブランチ（%s）の / を - に置き換えたものが %s と合わない。取り込まずに止めた。ブランチ名の / を - に置き換えたものが %s になるブランチに戻してから再実行してください\n' \
			"$1" "$1" "$(git -C "$root/.claude/worktrees/$1" symbolic-ref --quiet --short HEAD 2>/dev/null || :)" "$1" "$1"
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

# 親のワークツリーの無い親子のチケットのうち、取り込み記録が残っているもの。<P><タブ><取り込み状態の名前>
: >"$scratch/retired"
add_retired() {
	for ar_record in "$state"/sync/*/families/"$1"; do
		[ -f "$ar_record" ] && [ ! -L "$ar_record" ] || continue
		ar_key="${ar_record%/families/*}"
		ar_key="${ar_key##*/}"
		[ ! -L "$state/sync/$ar_key" ] && [ ! -L "$state/sync/$ar_key/families" ] || continue
		printf '%s\t%s\n' "$1" "$ar_key" >>"$scratch/retired"
		grep -F -x -q -- "$ar_key" "$scratch/repos" 2>/dev/null || printf '%s\n' "$ar_key" >>"$scratch/repos"
		return 0
	done
	return 1
}

if [ "$#" -gt 0 ]; then
	for want in "$@"; do
		has_family_line "$want" && continue
		tree="$root/.claude/worktrees/$want"
		if [ ! -e "$tree" ] && [ ! -L "$tree" ] && add_retired "$want"; then
			continue
		fi
		if [ ! -d "$tree" ] || [ -L "$tree" ]; then
			printf '%s: 親のワークツリー（.claude/worktrees/%s）がありません。切り直してから再実行してください（sh %s/ccnavi-git.sh fetch origin <ブランチ> のあと worktree add .claude/worktrees/%s <ブランチ>。ブランチ名の / を - に置き換えたものが %s になる）\n' \
				"$want" "$want" "$here_sh" "$want" "$want"
			fail_note
			continue
		fi
		fb_rc=0
		want_branch=$(family_branch "$want") || fb_rc=$?
		if [ "$fb_rc" -ne 0 ]; then
			branch_refused "$want" "$fb_rc"
			fail_note
			continue
		fi
		pt_rc=0
		ccnavi_parent_tree "$tree" "$want" || pt_rc=$?
		case "$pt_rc" in
		1)
			printf '%s: .claude/worktrees/%s に親チケットも提案もありません。親のワークツリーではないので取り込まない\n' "$want" "$want"
			fail_note
			# 親子のチケットは取り込まないが、そのリポジトリの統合先の取り込み結果は書く。取り込み記録が
			# （ccnavi-start.sh が MR の番号を保存して）チケットより先にあると、統合先の取り込み結果が
			# 無いままでは最初の承認（--agree）が止まる。
			nt_key=$(ccnavi_repo_key "$tree" "$root")
			grep -F -x -q -- "$nt_key" "$scratch/repos" 2>/dev/null || printf '%s\n' "$nt_key" >>"$scratch/repos"
			continue
			;;
		2)
			printf '%s: .claude/worktrees/%s に親チケットか提案があるかを実行ファイルに確かめられなかった（実行ファイルが無い・古い、またはチケットの frontmatter を読めない）。取り込まない\n' "$want" "$want"
			fail_note
			continue
			;;
		esac
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
		# HEAD のブランチ名を変換した識別子がフォルダ名と合うものだけ。
		name_branch=$(family_branch "$name") || continue
		[ "$branch" = "$name_branch" ] || continue
		add_family "$name" "$tree" "$name_branch"
	done
	for ar_path in "$state"/sync/*/families/*; do
		[ -f "$ar_path" ] && [ ! -L "$ar_path" ] || continue
		ar_name="${ar_path##*/}"
		case "$ar_name" in
		*.tmp.*) continue ;;
		esac
		ccnavi_is_ident "$ar_name" || continue
		has_family_line "$ar_name" && continue
		[ -e "$root/.claude/worktrees/$ar_name" ] || [ -L "$root/.claude/worktrees/$ar_name" ] && continue
		add_retired "$ar_name" || :
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
	# 親のブランチ名は、呼び手が親のワークツリーの HEAD から読んだもの（識別子からは逆算しない）。
	B="${7:-}"
	record=$(ccnavi_family_record "$root" "$key" "$P")
	if [ -z "$B" ]; then
		printf '%s: 親のブランチ名を親のワークツリーの HEAD から読めなかった。取り込まない\n' "$P"
		fail_note
		return 0
	fi

	branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ "$branch" != "$B" ]; then
		printf '%s: 親のワークツリーが %s の上に居る。親のブランチ %s に戻してから再実行してください\n' "$P" "${branch:-（ブランチの外）}" "$B"
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
		printf '%s: 他の操作がロックを持っている（%s）。終わってから再実行してください\n' "$P" \
			"$(ccnavi_lock_owner "$state/locks/$key/$P")"
		fail_note
		return 0
		;;
	esac

	busy=$(busy_state "$tree")
	if [ -n "$busy" ]; then
		printf '%s: 親のワークツリーに途中の操作（%s）がある。済ませるか取りやめてから再実行してください（この sh は変更しない）\n' "$P" "$busy"
		fail_note
	elif has_head "$heads" "$B"; then
		sync_present
		merged_present
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
		printf '%s: 手元が %s 件先に進んでいる（まだ送っていない）。取り込むものはありません。\n' "$P" "$ahead"
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
			printf '%s: リモートと分かれていて merge が必要ですが、ステージ済みの変更がある。コミットするか sh %s/ccnavi-git.sh restore --staged <パス> で外してから再実行してください\n' \
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
		printf '%s: 親子のチケットの取り込み状態（%s）を保存できませんでした。\n' "$P" "$record"
	return 0
}

# P がリモートにあっても、MR がマージ済みなら閉じている。ホストはマージの後もブランチを残すことがあり
# （GitHub の既定）、ready が親のワークツリーを片付けられなかった（未コミットの変更など）ときは、
# ブランチが残る限り片付けの機会が無くなる。取り込み記録の MR の番号で聞き、マージ済みなら片付ける。
# 番号が無ければ推定せず 1 行言う。マージされていなければ何も言わない。確かめられなければ 1 行言うが、
# 取り込みそのものは済んでいるので失敗にはしない（次の取り込みでまた聞く）。
merged_present() {
	merged=$(ask_merged "$tree")
	merged_rc="${merged%%:*}"
	merged="${merged#*:}"
	case "$merged_rc:$merged" in
	nomr:*) no_mr_note "（親のブランチはリモートにある）" ;;
	*:'merged '*)
		split_merged "${merged#merged }"
		if [ -n "$merged_branch" ] && [ "$merged_branch" != "$B" ]; then
			printf '%s: マージリクエスト（%s）の元ブランチ %s が親のブランチ %s と違う。手元は片付けない（取り込み記録の MR の番号を確かめてください）\n' \
				"$P" "$merged_mr" "$merged_branch" "$B"
			fail_note
			return 0
		fi
		printf '%s: 親のブランチ %s はリモートに残っているが、マージリクエスト（%s）はマージ済み（クローズ済みの親子のチケット）。手元を片付ける\n' \
			"$P" "$B" "$merged_mr"
		tidy_merged "$merged_sha"
		;;
	0:none) ;;
	*)
		printf '%s: マージリクエストがマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった）。閉じていれば次の取り込みで片付ける\n' "$P"
		;;
	esac
	return 0
}

# 統合先の done/ に、この親子のチケットの親チケットがあるか。
#
# sh はチケットの YAML を読まない。読むのは実行ファイルの `sync closed` で、答えは 1 行 1 項目
# （`integration yes|no`）。統合先は取ってきた後の refs/remotes/origin/<統合先>。識別子が P で子（`parent:`）
# でなく、親のワークツリーの親チケットと同じ親と言える親チケットがあれば yes。同じ親かは、スクリプトだけが
# 書く欄を base_sha → started_at → cancelled_at の順に見て、最初に両方が値を持つ欄が同じかで決める
# （同じ識別子の古い親子のチケットを、今のものと読まない）。手元の退避（logs/archive/）からは推定しない。
# 実行ファイルが無い・答えない（古くて副命令を知らない）ときは閉じていない側に倒す（止めて戻し方を出す）。
closed_in_integration() {
	[ -n "$info_from" ] || return 1
	cb_out=$(run_ccnavi sync closed "$P" "$key" "$integ" "$tree" 2>/dev/null | tr -d '\r') || cb_out=""
	[ "$(printf '%s\n' "$cb_out" | head -n 1)" = "closed 1" ] || return 1
	[ "$(printf '%s\n' "$cb_out" | sed -n "s/^integration //p" | head -n 1)" = yes ]
}

# 親の MR がマージ済みかをホストに聞く（ccnavi-review.sh merged <番号>）。<リポジトリかツリーのディレクトリ>
#
# 聞くのは取り込み記録の MR の番号（ccnavi-start.sh が保存する）だけ。番号が無ければ聞かずに nomr: を返す
# （ディレクトリのブランチから MR を探す推定はしない。HEAD が別のブランチなら別の MR を拾う）。
# 答えは「終了コード:1 行目」で、1 行目は merged <番号> <先頭の sha> <元ブランチ> / none /
# それ以外（道具・トークンが無い、API が失敗した）。
ask_merged() {
	am_mr=$(ccnavi_record_get "$record" mr)
	case "$am_mr" in
	'' | *[!0-9]*)
		printf 'nomr:\n'
		return 0
		;;
	esac
	am_rc=0
	am_out=$(cd "$1" && sh "$here_sh/ccnavi-review.sh" merged "$am_mr" 2>/dev/null </dev/null) || am_rc=$?
	printf '%s:%s\n' "$am_rc" "$(printf '%s\n' "$am_out" | head -n 1)"
}

# ask_merged の merged の行を分ける。<merged の後ろ（<番号> <先頭の sha> <元ブランチ>）>
# merged_mr・merged_sha・merged_branch に入れる。無い欄は空。
split_merged() {
	merged_mr="${1%% *}"
	merged_sha=""
	merged_branch=""
	case "$1" in
	*' '*)
		sm_rest="${1#* }"
		merged_sha="${sm_rest%% *}"
		case "$sm_rest" in
		*' '*) merged_branch="${sm_rest#* }" ;;
		esac
		;;
	esac
	merged_branch="${merged_branch%% *}"
}

# MR の番号が無いときの 1 行。<添える語>
no_mr_note() {
	printf '%s: 取り込み記録に MR の番号が無いので、閉じたか（MR がマージ済みか）判断できない%s\n' "$P" "$1"
}

# MR がマージ済みと分かった後の手元の片付け。<MR の先頭の sha>
#
# 親のワークツリーと手元のブランチは実行ファイル（`sync tidy`）が消す。手元の先頭が MR の先頭に含まれ
# （未 push のコミットが無い）、未コミットの変更が無いときだけ。消さなかったものは理由と手で消すコマンドを
# 1 行ずつ言う。親の取り込み記録は、実行ファイルが「残ったものは無い」（record yes）と答えたときだけ消す。
# sha が分からない・実行ファイルが無いときは、含まれるか確かめられないので何も消さない。
tidy_merged() {
	if [ -z "$1" ]; then
		printf '%s: マージリクエストの先頭の sha をホストから取れなかったので、手元のブランチは片付けない（含まれるか確かめられない）\n' "$P"
		return 0
	fi
	if [ -z "$info_from" ]; then
		printf '%s: 実行ファイルが無いので、手元の片付けはしなかった\n' "$P"
		return 0
	fi
	tm_rc=0
	run_ccnavi --cwd "$sync_cwd" sync tidy "$P" "$key" "$B" "$1" >"$scratch/tidy-raw" 2>"$scratch/tidy-err" || tm_rc=$?
	tr -d '\r' <"$scratch/tidy-raw" >"$scratch/tidy"
	if [ "$tm_rc" -ne 0 ]; then
		printf '%s: 手元の片付けを実行できなかった（%s）。実行ファイルを新しくして再実行してください\n' "$P" "$(head -n 1 "$scratch/tidy-err" 2>/dev/null)"
		fail_note
		return 0
	fi
	sed -e '/^record /d' -e 's/^/  /' "$scratch/tidy"
	if [ "$(tail -n 1 "$scratch/tidy")" = "record yes" ]; then
		if rm -f "$record"; then
			printf '%s: 片付けた。取り込み記録を削除した\n' "$P"
			log_info マージ済みの親を片付けた -- "family=$P" "repo=$key"
		else
			printf '%s: 取り込み記録（%s）を削除できませんでした\n' "$P" "$record"
			fail_note
		fi
	else
		printf '%s: 消さなかったものがある。取り込み記録は残す（理由は上。直してから再実行すれば片付く）\n' "$P"
	fi
}

# 統合先の done/ に閉じた記録があった。取り込み状態を closed にして言う。
closed_by_integration() {
	ccnavi_record_write "$record" remote origin branch "$B" sha "$kept_sha" \
		fetched_at "$(date +%s)" state closed reason "統合先の done/ に親チケットがある" || :
	printf '%s: リモートから消えたが、統合先（%s）の done/ にクローズ済みの記録がある（クローズ済みの親子のチケット）。親のワークツリーは片付けてよい\n' "$P" "$integ"
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
		printf '%s: リモートにありません（まだ送っていない親子のチケット）。今の手元の動きのまま\n' "$P"
		return 0
	fi
	# ccnavi-review.sh の道具（gh / glab / curl とトークン）で、MR がマージ済みかを先に聞く。答えは
	# merged <番号> / none（マージされた MR が無い）/ それ以外（道具・トークンが無い、API が失敗した）。
	# 統合先の done/ を待つ確かめ直しは、答えが得られないときだけ行う（統合先には閉じたチケットを
	# 残さないので、マージ済みと分かれば待つものは無い）。
	merged=$(ask_merged "$tree")
	merged_rc="${merged%%:*}"
	merged="${merged#*:}"
	case "$merged_rc:$merged" in
	nomr:*)
		no_mr_note "。取り込み状態は変えずに止めた（ccnavi-start.sh で始めると番号が記録される。ユーザが確かめてください）"
		fail_note
		return 0
		;;
	*:'merged '*)
		split_merged "${merged#merged }"
		if [ -n "$merged_branch" ] && [ "$merged_branch" != "$B" ]; then
			printf '%s: マージリクエスト（%s）の元ブランチ %s が親のブランチ %s と違う。手元は片付けずに止めた（取り込み記録の MR の番号を確かめてください）\n' \
				"$P" "$merged_mr" "$merged_branch" "$B"
			fail_note
			return 0
		fi
		printf '%s: リモートから消えたが、マージリクエスト（%s）はマージ済み（クローズ済みの親子のチケット）。手元を片付ける\n' \
			"$P" "$merged_mr"
		tidy_merged "$merged_sha"
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
		# マージ済みかを確かめられないときは、手元の退避（logs/archive/）からは推定せず、取り込み状態も
		# 変えずに止める。
		printf '%s: 親のブランチがリモートに無く、統合先（%s）にもクローズ済みの記録がありません。マージリクエストがマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった。gh・glab か、curl とトークンが必要です）。取り込み状態は変えずに止めた。確かめられる道具を用意して再実行するか、ユーザが確かめてください\n' "$P" "$integ"
		fail_note
		return 0
		;;
	esac
	if [ "$trace" = record ]; then
		ccnavi_record_write "$record" remote origin branch "$B" sha "$kept_sha" \
			fetched_at "$(date +%s)" state gone reason "リモートにも統合先の done/ にもありません。" ||
			printf '%s: 親子のチケットの取り込み状態（%s）を保存できませんでした。\n' "$P" "$record"
		printf '%s: 親のブランチ %s がリモートにありません。統合先（%s）にもクローズ済みの記録が無いので、この親子のチケットの状態を決められない。親子のチケットを止めた（取り込み状態は gone。このブランチへの push は通らない）\n' "$P" "$B" "$integ"
	else
		printf '%s: 親のブランチ %s がリモートにありません。統合先（%s）にもクローズ済みの記録が無いので、この親子のチケットの状態を決められない。送った形跡（origin/%s か追跡の設定）はあるが取り込み状態が無いので、取り込み状態は作らずに止めた\n' "$P" "$B" "$integ" "$B"
	fi
	printf '  戻し方 1（改名・消し間違い）: 元の名前 %s でブランチを作り直す。端末なら git push origin %s:refs/heads/%s（取り込み状態にある、最後に取り込んだか送った %s の先頭）、GitHub ならマージリクエストの画面の「Restore branch」、GitLab ならマージリクエストの refs/merge-requests/<番号>/head から %s を作る。戻したら sh %s/ccnavi-sync.sh %s を再実行します。\n' \
		"$B" "${kept_sha:-<最後に取り込んだ sha>}" "$B" "$B" "$B" "$here_sh" "$P"
	printf '  戻し方 2（親子のチケットを捨てた）: 親のワークツリーを片付け（sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s）、ユーザが sh %s/ccnavi-sync.sh --forget %s を実行すると親子のチケットの取り込み状態が消える（同じ名前で切り直せる。エージェントは実行しません）\n' \
		"$here_sh" "$P" "$here_sh" "$P"
	fail_note
	return 0
}

# ---- 親のワークツリーを片付けた後の親子のチケット（取り込み記録だけが残っている）
#
# `ready` が親のワークツリーを片付けた後は、取り込み記録と手元のブランチだけが残る。MR がマージ済みかを
# 聞き（取り込み記録の MR の番号を使う。ホストは、マージ後にブランチを消さないこともある）、マージ済みなら
# 手元を片付ける。取り込み状態が present でない（gone・blocked・closed）記録も、番号があれば聞いて片付ける。
# マージされていないと分かったときは何もしない（present でなければ、片付けない理由を 1 行言う）。
# 確かめられなければ、推定せずに止める。
#
# ブランチ名は取り込み記録の branch（キャッシュ）からは取らない。ワークツリーが無いので HEAD も無く、
# ホストが答えた MR の元ブランチを使う。
# <P> <取り込み状態の名前> <リポジトリ> <一覧>
sync_retired() {
	P="$1"
	key="$2"
	repo="$3"
	heads="$4"
	record=$(ccnavi_family_record "$root" "$key" "$P")
	sr_state=$(ccnavi_record_get "$record" state)
	B=""
	sr_mr=$(ccnavi_record_get "$record" mr)
	case "$sr_mr" in
	'' | *[!0-9]*)
		if [ "$sr_state" = present ]; then
			no_mr_note "（ワークツリーは片付け済み）。手元は片付けない"
		else
			printf '%s: 取り込み状態が %s なので片付けない（取り込み記録に MR の番号が無く、閉じたか判断できない）\n' \
				"$P" "${sr_state:-（読めない）}"
		fi
		return 0
		;;
	esac
	lock_rc=0
	ccnavi_lock_take "$root" "$key" "$P" "$lock_wait" || lock_rc=$?
	if [ "$lock_rc" -ne 0 ]; then
		printf '%s: 他の操作がロックを持っている。終わってから再実行してください\n' "$P"
		fail_note
		return 0
	fi
	merged=$(ask_merged "$repo")
	merged_rc="${merged%%:*}"
	merged="${merged#*:}"
	case "$merged_rc:$merged" in
	*:'merged '*)
		split_merged "${merged#merged }"
		B="$merged_branch"
		if [ -z "$B" ]; then
			printf '%s: マージリクエスト（%s）はマージ済みだが、元ブランチの名前をホストから取れなかった。手元は片付けない\n' "$P" "$merged_mr"
			fail_note
		else
			if has_head "$heads" "$B"; then
				sr_where="リモートに残っている"
			else
				sr_where="リモートから消えた"
			fi
			printf '%s: ワークツリーは片付け済み。親のブランチ %s は%s。マージリクエスト（%s）はマージ済み。手元を片付ける\n' \
				"$P" "$B" "$sr_where" "$merged_mr"
			tree="$repo"
			tidy_merged "$merged_sha"
		fi
		;;
	0:none)
		[ "$sr_state" = present ] ||
			printf '%s: 取り込み状態が %s で、マージリクエスト（%s）はマージされていないので片付けない\n' \
				"$P" "${sr_state:-（読めない）}" "$sr_mr"
		;;
	*)
		printf '%s: ワークツリーは片付け済みだが、マージリクエスト（%s）がマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった。gh・glab か、curl とトークンが必要です）。手元は片付けずに止めた\n' "$P" "$sr_mr"
		fail_note
		;;
	esac
	# ロックは自分が取ったものだけを外す（持ち主を確かめる。入れ子で外側が持つロックは外さない）。
	ccnavi_lock_drop
	return 0
}

# ---- 取り込みの後の検査

# present の親子のチケットを判定し直し、error があれば blocked にする。<P> <取り込み状態の名前>
check_family() {
	cf_record=$(ccnavi_family_record "$root" "$2" "$1")
	[ "$(ccnavi_record_get "$cf_record" state)" = present ] || return 0
	# 手元の片付けで親のワークツリーを消した親子のチケットは、判定し直す先が無い。
	[ -d "$root/.claude/worktrees/$1" ] || return 0
	if [ -z "$info_from" ]; then
		printf '%s: 実行ファイルが無いので、取り込みの後の検査（判定し直し）はしなかった\n' "$1"
		return 0
	fi
	cf_rc=0
	run_ccnavi sync check "$1" "$2" >"$scratch/check-raw" 2>"$scratch/check-err" || cf_rc=$?
	tr -d '\r' <"$scratch/check-raw" >"$scratch/check"
	if [ "$(head -n 1 "$scratch/check" 2>/dev/null)" != "check 1" ]; then
		# 検査を実行できなかった（古い実行ファイルが副命令を知らない、失敗した）。検査の error ではない
		# ので親子のチケットは止めない。この検査が入る前と同じ動き。
		printf '%s: 注意: 取り込みの後の検査を実行できなかった（%s）。親子のチケットは止めていない。実行ファイルを新しくして再実行してください\n' \
			"$1" "$(head -n 1 "$scratch/check-err" 2>/dev/null)"
		log_warn 取り込みの後の検査を実行できなかった -- "family=$1" "rc=$cf_rc"
		return 0
	fi
	sed -n 's/^warn /  注意: /p' "$scratch/check"
	[ "$cf_rc" -ne 0 ] || return 0
	cf_reason=$(sed -n 's/^error //p' "$scratch/check" | head -n 1)
	[ -n "$cf_reason" ] || cf_reason="取り込みの後の検査が失敗した（$(head -n 1 "$scratch/check-err" 2>/dev/null)）"
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
		printf '  直してから、オンラインで sh %s/ccnavi-sync.sh %s を再実行してください（検査し直して通れば present に戻る）\n' "$here_sh" "$1"
		fail_note
	else
		printf '%s: 取り込みの後の検査で止める理由があったが、ロックが取れないか親子のチケットの取り込み状態（%s）を書けず、3 回試しても止められなかった（%s）。親子のチケットはまだ止まっていない。再実行してください\n' \
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
	# 親子のチケットの無いリポジトリ（引数を省いた回のプロジェクトなど）で失敗しても、名指しして続け、1 にしない。
	repo_fail() {
		[ "$has_family" = yes ] && fail_note
		return 0
	}
	if ! git -C "$repo" remote get-url origin >/dev/null 2>&1; then
		# origin の無いリポジトリは取り込みの対象外（今の手元の動きのまま）。
		if [ "$has_family" = yes ]; then
			printf '%s: origin がありません。取り込みの対象外（今の手元の動きのまま）\n' "$key"
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
			printf '%s 統合先 %s（%s）がリモートにありません。設定を直してください。取り込みを止めた\n' "$label" \
				"$integration_want" "$(source_words "$integration_source")"
			repo_fail
			continue
		fi
		integ="$integration_want"
	elif ! integ=$(default_branch "$repo" "$heads"); then
		printf '%s 統合先が決まらない（ホストのデフォルトブランチが読めず、main・master もリモートにありません）。CCNAVI_INTEGRATION_BRANCH を設定してください。取り込みを止めた\n' "$label"
		repo_fail
		continue
	fi
	printf '%s 統合先: %s（%s）\n' "$label" "$integ" "$(source_words "$integration_source")"
	if ! fetch_one "$repo" "$integ"; then
		printf '%s 統合先 %s を取ってこられなかった（%s）。止める\n' "$label" "$integ" "$(head -n 1 "$scratch/err")"
		repo_fail
		continue
	fi
	awk -F "$tab" -v k="$key" '$3 == k' "$scratch/families" >"$scratch/these"
	# 読む先は fd 3。中で起こす git が標準入力を読んでも、親子のチケットの一覧を読み取ってしまわない。
	while IFS="$tab" read -r fam_p fam_tree fam_key fam_branch <&3; do
		[ -n "$fam_p" ] || continue
		sync_family "$fam_p" "$fam_tree" "$fam_key" "$repo" "$integ" "$heads" "$fam_branch"
	done 3<"$scratch/these"
	awk -F "$tab" -v k="$key" '$2 == k' "$scratch/retired" >"$scratch/these-retired"
	while IFS="$tab" read -r ret_p ret_key <&3; do
		[ -n "$ret_p" ] || continue
		sync_retired "$ret_p" "$ret_key" "$repo" "$heads"
	done 3<"$scratch/these-retired"
	if ! write_integration "$repo" "$key" "$integ"; then
		printf '%s 統合先の取り込み結果（%s/sync/%s/integration）を保存できませんでした。\n' "$label" "$state" "$key"
		repo_fail
	fi
	while IFS="$tab" read -r fam_p fam_tree fam_key fam_branch <&3; do
		[ -n "$fam_p" ] || continue
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

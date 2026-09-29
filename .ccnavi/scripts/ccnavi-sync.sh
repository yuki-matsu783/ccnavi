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
# リポジトリ（ワークスペース自身と、家族の元のプロジェクト）ごとに 1 回:
#
#   1. `ls-remote --heads origin` で全ブランチの有無を得る。落ちたら（オフライン・認証）止める
#   2. 統合先の名前を決める。CCNAVI_INTEGRATION_BRANCH（環境変数、無ければ
#      .claude/settings.local.json の env）、空ならホストのデフォルトブランチ（origin/HEAD、
#      無ければ main・master）。設定した名前がリモートに無ければ、既定に落とさずに止める（D30）
#   3. 統合先を fetch し、判定に要るもの（done/・共通層・自身の層・.claude/settings.json）を
#      統合先の控え sync/<リポジトリ>/integration/ へ同じ並びで写し、head に remote・branch・
#      source・sha・fetched_at を書く（D26）
#
# 家族ごと（ロックを待って取る。D32）:
#
#   - P がリモートにある: fetch して、早送りできれば早送り、分かれていれば merge。merge は
#     索引が HEAD と同じときだけ（D35）で、衝突したら merge --abort して人に回す。家族の控えを
#     present で書く
#   - P がリモートに無く、家族の控えも無い: 一度も送っていない家族。今の手元の動きのまま
#   - P がリモートに無く、控えがある: 統合先の done/ に親の写しがあれば閉じた家族（closed）。
#     無ければ観測ずれを疑い、統合先を取り直して確かめ直す（既定 3 回、5 秒おき）。それでも
#     無く、ccnavi-review.sh で MR がマージ済みと分かれば「反映待ち」で止める。どれにも
#     当たらなければ消えた（gone）として控えに書き、戻し方を出して止める
#
# この段（2b）では控えを書くだけで、判定はまだ控えを読まない（2c）。
#
# 実行ファイルはネットワークに出ない（docs/claude/exe-boundary.md）。ここが git で取ってくる。
# 置き場の綴りと settings.local.json の読みだけを実行ファイル（`ccnavi sync paths`）に聞く。
# 実行ファイルが無ければ、ほかの sh と同じく環境変数の綴り（無ければ既定）を使う。
#
# 環境変数: CCNAVI_INTEGRATION_BRANCH / CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/
#   CCNAVI_SYNC_RETRIES（観測ずれの確かめ直しの回数、既定 3）/ CCNAVI_SYNC_RETRY_WAIT（その間隔の秒、既定 5）
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

# 認証を尋ねない。端末から打たれても、hook と同じく尋ねずに落として理由を言う。
GIT_TERMINAL_PROMPT=0
GCM_INTERACTIVE=never
export GIT_TERMINAL_PROMPT GCM_INTERACTIVE

lock_wait="${CCNAVI_LOCK_WAIT:-120}"
case "$lock_wait" in
'' | *[!0-9]*) lock_wait=120 ;;
esac
retries="${CCNAVI_SYNC_RETRIES:-3}"
case "$retries" in
'' | *[!0-9]*) retries=3 ;;
esac
retry_wait="${CCNAVI_SYNC_RETRY_WAIT:-5}"
case "$retry_wait" in
'' | *[!0-9]*) retry_wait=5 ;;
esac

scratch=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-sync) || {
	printf 'ccnavi-sync: 一時ディレクトリが作れません。\n' >&2
	exit 2
}
trap 'ccnavi_lock_drop; rm -rf "$scratch"' EXIT
: >"$scratch/failed"

log_info 受け付けた -- "args=$#"

# ---- 置き場の綴りと、settings.local.json の統合先。実行ファイルに聞く（D33）。

sync_info=""
if bin=$(ccnavi_bin "$root"); then
	sync_info=$("$bin" --root "$root" sync paths 2>/dev/null </dev/null) || sync_info=""
elif [ -f "$root/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
	sync_info=$(cd "$root" && uv run --quiet python -m ccnavi --root "$root" sync paths 2>/dev/null </dev/null) || sync_info=""
fi
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

# ls-remote の一覧から refs/heads/<名前> の sha。<一覧> <名前>
head_sha() {
	awk -F '\t' -v want="refs/heads/$2" '$2 == want { print $1; exit }' "$1"
}

# ホストのデフォルトブランチ。origin/HEAD が指すもの（リモートの一覧にあるもの）、無ければ
# 一覧にある main・master。どれも無ければ 1。<リポジトリ> <一覧>
default_branch() {
	db_head=$(git -C "$1" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || :)
	db_head="${db_head#origin/}"
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

# fetch 1 本。落ちた理由（git の標準エラー）は $scratch/err に残る。<リポジトリ> <ブランチ>
fetch_one() {
	LC_ALL=C git -C "$1" fetch --quiet origin "$2" </dev/null >/dev/null 2>"$scratch/err"
}

# 統合先の控えを書く。<リポジトリ> <控えの名前> <統合先>
#
# 統合先の先頭から、判定に要るものをファイルのまま同じ並びで写す（Python の読み方を変えずに
# 済む形）。一時の置き場に組んでから入れ替えるので、読む側は古いか新しいかのどちらかを見る。
write_integration() {
	wi_repo="$1"
	wi_branch="$3"
	wi_ref="refs/remotes/origin/$3"
	wi_sha=$(git -C "$wi_repo" rev-parse --verify --quiet "$wi_ref^{commit}" 2>/dev/null) || return 1
	wi_dest="$state/sync/$2/integration"
	wi_tmp="$wi_dest.tmp.$$"
	rm -rf "$wi_tmp"
	mkdir -p "$wi_tmp" || return 1
	set --
	for wi_path in "$approved/done" ".ccnavi/common" "$home/config" ".claude/settings.json"; do
		if git -C "$wi_repo" cat-file -e "$wi_ref:$wi_path" 2>/dev/null; then
			set -- "$@" "$wi_path"
		fi
	done
	if [ "$#" -gt 0 ]; then
		git -C "$wi_repo" archive --format=tar "$wi_ref" -- "$@" 2>/dev/null | tar -xf - -C "$wi_tmp" 2>/dev/null || {
			rm -rf "$wi_tmp"
			return 1
		}
	fi
	ccnavi_record_write "$wi_tmp/head" remote origin branch "$wi_branch" source "$integration_source" \
		sha "$wi_sha" fetched_at "$(date +%s)" || {
		rm -rf "$wi_tmp"
		return 1
	}
	if [ -d "$wi_dest" ]; then
		rm -rf "$wi_dest.old.$$"
		mv "$wi_dest" "$wi_dest.old.$$" || {
			rm -rf "$wi_tmp"
			return 1
		}
	fi
	mv "$wi_tmp" "$wi_dest" || return 1
	rm -rf "$wi_dest.old.$$"
	return 0
}

# 書きかけと重なって git が拒んだときの、重なったパス（git が字下げして並べる行）。標準入力から読む。
overlapping() {
	sed -n 's/^[[:space:]][[:space:]]*\([^[:space:]].*\)$/\1/p' | tr '\n' ' ' | sed 's/ *$//'
}

tab=$(printf '\t')

fail_note() {
	printf 'fail\n' >>"$scratch/failed"
}

# ---- 家族を集める。<P>:<ツリー>:<リポジトリの控えの名前>:<リポジトリ> を 1 行ずつ。

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
add_family() {
	af_key=$(ccnavi_repo_key "$2" "$root")
	printf '%s\t%s\t%s\n' "$1" "$2" "$af_key" >>"$scratch/families"
	grep -F -x -q -- "$af_key" "$scratch/repos" 2>/dev/null || printf '%s\n' "$af_key" >>"$scratch/repos"
}

if [ "$#" -gt 0 ]; then
	for want in "$@"; do
		tree="$root/.claude/worktrees/$want"
		if [ ! -d "$tree" ] || [ -L "$tree" ]; then
			printf '%s: 親のワークツリー（.claude/worktrees/%s）が無い。切り直してから打ち直す（sh %s/ccnavi-git.sh fetch origin %s のあと worktree add .claude/worktrees/%s -b %s origin/%s）\n' \
				"$want" "$want" "$(dirname "$0")" "$want" "$want" "$want" "$want"
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

	if has_head "$heads" "$P"; then
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
			printf '%s: 書きかけの %s と重なるので進めない。コミットか退避をしてから打ち直す\n' "$P" \
				"$(overlapping <"$scratch/err")"
			fail_note
		fi
	else
		# 分かれている。非 ff の merge は、重ならないステージ済みの変更があっても拒む（git 2.43）ので、
		# 先に見て言う（D35）。
		if ! git -C "$tree" diff --cached --quiet 2>/dev/null; then
			printf '%s: リモートと分かれていて merge が要るが、ステージ済みの変更がある。コミットするか sh %s/ccnavi-git.sh restore --staged <パス> で外してから打ち直す\n' \
				"$P" "$(dirname "$0")"
			fail_note
		elif LC_ALL=C git -C "$tree" merge --no-edit --quiet -m "ccnavi: origin/$P を取り込む" "$remote_sha" \
			</dev/null >"$scratch/out" 2>"$scratch/err"; then
			printf '%s: リモートと分かれていたので merge で取り込んだ\n' "$P"
		else
			conflicted=$(git -C "$tree" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')
			merging=$(git -C "$tree" rev-parse --git-path MERGE_HEAD 2>/dev/null || :)
			case "$merging" in
			/* | [A-Za-z]:*) ;;
			*) merging="$tree/$merging" ;;
			esac
			if [ -f "$merging" ]; then
				git -C "$tree" merge --abort >/dev/null 2>&1 || :
			fi
			if [ -n "$conflicted" ]; then
				printf '%s: リモートと分かれていて merge が衝突した（%s）。取り込みをやめた（merge --abort）。どちらを採るかは人が決める\n' \
					"$P" "$conflicted"
			else
				printf '%s: リモートと分かれていて merge できなかった。書きかけの %s と重なる。コミットか退避をしてから打ち直す\n' \
					"$P" "$(cat "$scratch/out" "$scratch/err" | overlapping)"
			fi
			fail_note
		fi
	fi
	ccnavi_record_write "$record" remote origin branch "$P" sha "$remote_sha" \
		fetched_at "$(date +%s)" state present reason "" ||
		printf '%s: 家族の控え（%s）を書けなかった\n' "$P" "$record"
	return 0
}

# 統合先の done/ に親の写しがあるか。統合先は取ってきた後の refs/remotes/origin/<統合先>。
closed_in_integration() {
	git -C "$repo" cat-file -e "refs/remotes/origin/$integ:$approved/done/$P.md" 2>/dev/null
}

# P がリモートに無い。閉じたか、消えたか（3.6）。
sync_absent() {
	if [ ! -f "$record" ]; then
		printf '%s: リモートに無い（まだ送っていない家族）。今の手元の動きのまま\n' "$P"
		return 0
	fi
	kept_sha=$(ccnavi_record_get "$record" sha)
	tries=0
	while :; do
		if closed_in_integration; then
			ccnavi_record_write "$record" remote origin branch "$P" sha "$kept_sha" \
				fetched_at "$(date +%s)" state closed reason "統合先の done/ に親の写しがある" || :
			printf '%s: リモートから消えたが、統合先（%s）の done/ に閉じた記録がある（閉じた家族）。親のワークツリーは片付けてよい\n' "$P" "$integ"
			return 0
		fi
		[ "$tries" -lt "$retries" ] || break
		tries=$((tries + 1))
		# 観測ずれ。ホストはマージの後に P を消すが、読み取りの複製が遅れて done/ がまだ見えないことがある。
		sleep "$retry_wait"
		fetch_one "$repo" "$integ" || :
	done
	# ccnavi-review.sh の道具（gh / glab / curl とトークン）が使えれば、MR がマージ済みかを聞く。
	merged=$(cd "$tree" && sh "$(dirname "$0")/ccnavi-review.sh" merged 2>/dev/null </dev/null | head -n 1) || merged=""
	case "$merged" in
	'merged '*)
		printf '%s: MR（%s）はマージ済みだが、統合先（%s）への反映がまだ見えない。少し待って sh %s/ccnavi-sync.sh %s を打ち直す\n' \
			"$P" "${merged#merged }" "$integ" "$(dirname "$0")" "$P"
		fail_note
		return 0
		;;
	esac
	ccnavi_record_write "$record" remote origin branch "$P" sha "$kept_sha" \
		fetched_at "$(date +%s)" state gone reason "リモートにも統合先の done/ にも無い" ||
		printf '%s: 家族の控え（%s）を書けなかった\n' "$P" "$record"
	printf '%s: 親のブランチ %s がリモートに無い。統合先（%s）にも閉じた記録が無いので、この家族の状態を決められない。家族を止めた（控えは gone。この P への push は通らない）\n' "$P" "$P" "$integ"
	printf '  戻し方 1（改名・消し間違い）: 元の名前 %s でブランチを作り直す。端末なら git push origin %s:refs/heads/%s、GitHub なら PR の画面の「Restore branch」、GitLab なら MR の refs/merge-requests/<番号>/head から %s を作る。戻したら sh %s/ccnavi-sync.sh %s を打ち直す\n' \
		"$P" "${kept_sha:-<最後に取り込んだ sha>}" "$P" "$P" "$(dirname "$0")" "$P"
	printf '  戻し方 2（家族を捨てた）: 親のワークツリーを片付ける（sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s）\n' \
		"$(dirname "$0")" "$P"
	fail_note
	return 0
}

# ---- リポジトリごと

while IFS= read -r key <&4; do
	[ -n "$key" ] || continue
	repo=$(repo_dir_of "$key")
	grep -q "$tab$key\$" "$scratch/families" && has_family=yes || has_family=no
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
	if ! LC_ALL=C git -C "$repo" ls-remote --heads origin </dev/null >"$heads" 2>"$scratch/err"; then
		printf '%sリモートのブランチの一覧を取ってこられなかった（オフラインか認証。%s）。止める\n' "$label" \
			"$(head -n 1 "$scratch/err")"
		fail_note
		continue
	fi
	if [ -n "$integration_want" ]; then
		if ! has_head "$heads" "$integration_want"; then
			printf '%s統合先 %s（%s）がリモートに無い。設定を直す。取り込みを止めた\n' "$label" \
				"$integration_want" "$(source_words "$integration_source")"
			fail_note
			continue
		fi
		integ="$integration_want"
	elif ! integ=$(default_branch "$repo" "$heads"); then
		printf '%s統合先が決まらない（origin/HEAD が無く、main・master もリモートに無い）。CCNAVI_INTEGRATION_BRANCH を設定する。取り込みを止めた\n' "$label"
		fail_note
		continue
	fi
	printf '%s統合先: %s（%s）\n' "$label" "$integ" "$(source_words "$integration_source")"
	if ! fetch_one "$repo" "$integ"; then
		printf '%s統合先 %s を取ってこられなかった（%s）。止める\n' "$label" "$integ" "$(head -n 1 "$scratch/err")"
		fail_note
		continue
	fi
	grep "$tab$key\$" "$scratch/families" >"$scratch/these" 2>/dev/null || :
	# 読む先は fd 3。中で起こす git が標準入力を読んでも、家族の並びを食べない。
	while IFS="$tab" read -r fam_p fam_tree fam_key <&3; do
		[ -n "$fam_p" ] || continue
		sync_family "$fam_p" "$fam_tree" "$fam_key" "$repo" "$integ" "$heads"
	done 3<"$scratch/these"
	write_integration "$repo" "$key" "$integ" ||
		{
			printf '%s統合先の控え（%s/sync/%s/integration）を書けなかった\n' "$label" "$state" "$key"
			fail_note
		}
done 4<"$scratch/repos"

if [ -s "$scratch/failed" ]; then
	log_info 終わった -- "exit=1"
	exit 1
fi
log_info 終わった -- "exit=0"
exit 0

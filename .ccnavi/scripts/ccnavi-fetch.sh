#!/bin/sh
# ccnavi-fetch セッションの頭で、リモートに合わせるものを 2 つ取ってくる。
#
#   sh .ccnavi/scripts/ccnavi-fetch.sh
#
# 1 つめは承認済みチケットとマーカー。これらは親チケットのブランチにコミットされ、A の機械から
# push されて届く。取ってこないと、B の機械は古い版で判定する。承認したのに
# 範囲が反映されない、レビュー済みなのに止まったまま、という形になる。
#
# 2 つめはワークツリーの起点になる統合先（そのリポジトリの手元の `origin/HEAD` が指すブランチ。
# 無ければ `origin/main`・`origin/master`。多くは `main`）。親のワークツリーは `ccnavi-git.sh worktree add <行き先> -b <名前> <統合先>` で
# 切り、起点は `<統合先>` の HEAD になる。手元の `main` が古いと、そこから切るブランチも古いコミットから
# 始まる。統合先に取り込むときに fast-forward が通らず、承認済みチケットも古い版で判定することになる。
#
# 手元の `origin/HEAD` は clone のときに作られたまま、普通の fetch では更新されない。ホストの既定ブランチが
# 変わっていたら、第 1 周（承認済みチケット）の後、第 2 周（統合先を進める）の前に向け直す。
# `ls-remote --symref origin HEAD` でホストに既定ブランチを聞き、手元の統合先と違えばそのブランチを fetch して、
# 成功してから `refs/remotes/origin/HEAD` を向け直す（`git remote set-head origin -a` は手元に
# `origin/<名前>` が無いと失敗するため、自分で順を踏む）。向け直したときだけ 1 行出す。ホストに聞けない・
# fetch できない・時間切れのときは、何も出さず何も変えない。
#
# デフォルトブランチは、チェックアウトされていなくても進める。 ワークスペースルートが
# `main` 以外に居るセッションでは、上の 1 つめ（そのツリーがチェックアウトしているブランチを
# 進める）では `main` に届かない。
#
# 進めるのは fast-forward だけ。ワークツリーに未コミットの
# 変更があるツリーは触らない。そこに居るのはユーザか別のセッションの書きかけで、
# セッションの頭に走る hook が動かしてよいものではない（.ccnavi/docs/worktree.md の「他セッションの
# 作業を踏まないために」）。進められなかったツリーは理由を 1 行で言う。
#
# 出力はモデルに届く。何も動かなかったときは何も出さない。毎回同じ行を返すと、
# セッションの頭の文脈がそれで埋まる。
#
# 待たせない。 認証を尋ねる画面を出させず（GIT_TERMINAL_PROMPT・GCM_INTERACTIVE）、
# fetch 1 回にタイムアウトを付けて CCNAVI_FETCH_TIMEOUT 秒（既定 15）で切る。hook の上限（60 秒）に
# 重なると、報せごと破棄される。一度失敗した origin には、この回ではもう取りに行かない。
#
# 認証で失敗したときは、そう言う。 尋ねないので、資格情報が無いか切れていると毎回失敗する。
# オフラインと同じ 1 行では、ユーザは理由を調べることになる。認証はユーザがターミナルで打つ git（承認の
# sh を含む）で一度済ませれば保存され、次のセッションから hook の fetch も通る。見分けと案内の文は実行ファイル
# （`fetch auth`）が持ち、git の文言に頼るので、LC_ALL=C で英語に揃えて打った標準エラーを渡す。見分けられなければ、
# ただの「取ってこられなかった」に戻るだけ。
#
# 親子のチケットは早送りだけ。親のワークツリー（`.claude/worktrees/<P>` で、親チケットか
# 提案があり、HEAD のブランチ名の `/` を `-` に置き換えたものがディレクトリ名になっている）は、`origin/<親のブランチ>` の
# 祖先なら早送りする。早送りが要るときだけロックを 1 回試し（待たない。取れたときだけ早送りする）。書きかけとの
# 重なりは git に任せ、拒まれたら重なったパスを言う。分かれていれば「取り込みが要る」と 1 行言う。
# 取り込み（merge）・消えたかの確かめは、手で打つ ccnavi-sync.sh の仕事。
# 開始から CCNAVI_FETCH_BUDGET 秒（既定 45）を過ぎたら、残りの fetch と早送りはせずに名指しする。
# fetch 1 回のタイムアウトも枠の残りに収める（hook の上限は 60 秒）。
#
# 分担: どれを進めるかの判定（親のワークツリーの選び方とそのロックの置き場・第 1 周が見たツリー・統合先）、早送り・
# update-ref・origin/HEAD の向け直し、認証の失敗の見分けと案内、その出力の文は実行ファイル（`ccnavi fetch <段取り>`。
# go/internal/entry/catchup）が持つ。この sh に残すのは、fetch と ls-remote（ccnavi_fetch_git・ccnavi_fetch_retarget）と
# その失敗の報せ、時間の枠、ロック、段取り。実行ファイルの入れ替え中も fetch できるよう、実行ファイルが無くても fetch は
# 止まらない作りにしてある。
#
# 実行ファイルが無い・答えないときは、取ってくる（fetch）だけにして、次を省く: 親の早送り（親子のチケットも、承認済み
# チケットを持つツリーのブランチも）、origin/HEAD の向け直し（ホストに聞く ls-remote ごと）、統合先の取得と早送り、
# 認証に失敗したときの案内（「取ってこられなかった」の 1 行だけにする）。省いたことは言わない（毎回同じ行を返さない）。
#
# 実行ファイルが無くても取ってくるために、sh が写しを持つものは 2 つ。どちらも実行ファイルとの一致をテストで確かめる。
# - 見るツリーの置き場（ccnavi-common.sh の ccnavi_worktrees_dir・ccnavi_projects_dir・ccnavi_approved_dir。
#   tests/fixtures/layout.json）
# - 「リモートにその ref が無い」の見分け（ccnavi-common-state.sh の ccnavi_no_remote_ref。tests/fixtures/no-remote-ref.json）
#
# 「リモートにその ref が無い」で失敗した fetch は、その origin を失敗したものに数えない。 数えると、
# 同じ origin の残りの取得まで行われなくなる。消えたかどうかはここでは決めず、ccnavi-sync.sh に回す。
#
# 終了コード: 常に 0。取ってこられないことは失敗ではない（オフラインでも作業は続く）。

set -u

# 共通部分。ワークスペースルートの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

approved="$ccnavi_approved_dir"
projects="$ccnavi_projects_dir"
worktrees="$ccnavi_worktrees_dir"

# 見つからなければ何も出さずに終わる。セッションの頭に走るので、ここで止めても得るものが無い。
root=$(ccnavi_workspace) || exit 0

# 認証を尋ねない。hook にはターミナルが無く、尋ねれば失敗するか、画面を開いて誰かが閉じるまで待つ。
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

# 失敗した origin を書いておく場所。同じ origin には取りに行かない。周はパイプの中（サブシェル）で
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
# 向け直しのための ls-remote が失敗・時間切れだった origin。第 2 周で同じ origin を二重に待たないために
# 覚える（failed とは別。ここは第 1 周の取得の失敗ではない）。
: >"$scratch/rt-failed"

# 時間の枠の残り（秒）。負なら 0。
ccnavi_fetch_left() {
	ccnavi_fl_now=$(date +%s)
	ccnavi_fl_left=$((budget - (ccnavi_fl_now - started)))
	[ "$ccnavi_fl_left" -gt 0 ] || ccnavi_fl_left=0
	printf '%s\n' "$ccnavi_fl_left"
}

# origin へ 1 本取りに行く。取れたら 0、失敗したら 1。取りに行かなかったら 2。
# リモートにその ref が無くて失敗したら 4（その origin を失敗したものに数えない）。
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
	# 向け直しのための ls-remote が届かなかった origin は、ここでも待たない。失敗した origin として
	# 数え直し、この 1 本は失敗として返す（報せは今までどおり、その origin で初めて失敗したときの 1 行）。
	if [ -n "$ccnavi_fg_url" ] && grep -qxF -- "$ccnavi_fg_url" "$scratch/rt-failed" 2>/dev/null; then
		printf '%s\n' "$ccnavi_fg_url" >>"$scratch/failed"
		return 1
	fi
	ccnavi_fg_left=$(ccnavi_fetch_left)
	[ "$ccnavi_fg_left" -gt 0 ] || return 5
	ccnavi_fg_limit="$limit"
	[ "$ccnavi_fg_left" -lt "$ccnavi_fg_limit" ] && ccnavi_fg_limit="$ccnavi_fg_left"
	ccnavi_fg_rc=0
	ccnavi_git_timed "$ccnavi_fg_limit" "$scratch/err" "$1" \
		fetch --quiet origin "+refs/heads/$2:refs/remotes/origin/$2" >/dev/null || ccnavi_fg_rc=$?
	if [ "$ccnavi_fg_rc" -ne 0 ]; then
		ccnavi_no_remote_ref "$scratch/err" && return 4
		printf '%s\n' "$ccnavi_fg_url" >>"$scratch/failed"
		return 1
	fi
	return 0
}

# 取りに行く。取れたら 0。<ツリー> <ブランチ> <失敗したときの 1 行> [<リモートに無いときの 1 行>]
#
# 失敗したときの 1 行は、その origin で初めて失敗したときだけ出す。同じ origin の 2 件目は
# 取りに行かず、何も出さない。分け方に要る判定を、報せの `$( )` の外に置くための関数。
ccnavi_fetch_or_note() {
	ccnavi_fetch_git "$1" "$2"
	ccnavi_fn_rc=$?
	[ "$ccnavi_fn_rc" -eq 0 ] && return 0
	[ "$ccnavi_fn_rc" -eq 2 ] && return 1
	if [ "$ccnavi_fn_rc" -eq 5 ]; then
		printf '%s: 時間の枠（%s 秒）を過ぎたので %s を取得しませんでした。後で sh %s/.ccnavi/scripts/ccnavi-sync.sh を実行するか、もう一度セッションを始めてください\n' \
			"$(basename "$1")" "$budget" "$2" "$root"
		return 1
	fi
	if [ "$ccnavi_fn_rc" -eq 4 ]; then
		printf '%s\n' "${4:-$3}"
		return 1
	fi
	printf '%s\n' "$3"
	# 認証の失敗なら、その案内を添える（見分けと文は実行ファイル。答えなければ省く）。
	[ -z "$fetch_bin" ] || ccnavi_fetch_ask auth "$1" "$scratch/err" || :
	return 1
}

# 報せを組み立てる `$( )` の中に `case` は書けない（macOS の bash 3.2 が `)` を読み違える。
# tests/core/test_sh_portability.py）。`case` の要るものはここで関数にしておく。

# 実行ファイルに聞く（`ccnavi fetch <段取り> <引数>...`）。答えの out の行を出し、次の一手（next の語）を
# ccnavi_fx_next に入れる。答えない（実行ファイルが無い・入れ替え中）なら 1。
ccnavi_fetch_ask() {
	ccnavi_fx_next=""
	ccnavi_fx_out=$(ccnavi_ask "$root" fetch "$@") || return 1
	[ "$(printf '%s\n' "$ccnavi_fx_out" | head -n 1)" = "fetch 1" ] || return 1
	ccnavi_val out "$ccnavi_fx_out"
	ccnavi_fx_next=$(ccnavi_val1 next "$ccnavi_fx_out")
	return 0
}

# 手元の origin/HEAD を、ホストの既定ブランチに向け直す。<リポジトリ>
#
# 外との出入り（ls-remote と fetch）だけをここで打ち、ホストの答えを読んで手元の統合先と比べるのと、向け直すのは
# 実行ファイル（`fetch retarget`）。実行ファイルが答えなければ、ホストにも聞かない（向け直せないので）。
# 何も出さないのは、ホストに聞けない・fetch できない・時間切れ・ホストに聞いた名前が手元と同じ、のとき。
# 失敗は $scratch/failed に書かない（ここは「向け直せなかった」だけで、第 1 周の取得の失敗とは別）。
# ただし ls-remote が時間切れになった origin は $scratch/rt-failed に覚え、第 2 周の取得で二重に待たない。
# 第 1 周でその origin への接続に失敗していたら、同じ origin で二重に待たないよう聞かない。
# 向け直すのは fetch が成功した後にする（存在しない ref を指す状態を残さないため）。
ccnavi_fetch_retarget() {
	[ -n "$fetch_bin" ] || return 0
	ccnavi_rt_url=$(git -C "$1" remote get-url origin 2>/dev/null || :)
	[ -n "$ccnavi_rt_url" ] || return 0
	if grep -qxF -- "$ccnavi_rt_url" "$scratch/failed" 2>/dev/null; then
		return 0
	fi
	ccnavi_rt_left=$(ccnavi_fetch_left)
	[ "$ccnavi_rt_left" -gt 0 ] || return 0
	ccnavi_rt_limit="$limit"
	[ "$ccnavi_rt_left" -lt "$ccnavi_rt_limit" ] && ccnavi_rt_limit="$ccnavi_rt_left"
	ccnavi_git_timed "$ccnavi_rt_limit" "$scratch/rt-err" "$1" ls-remote --symref origin HEAD \
		>"$scratch/rt-out" || {
		ccnavi_rt_rc=$?
		# 時間切れ（ccnavi_git_timed は 124 を返す）のときだけ覚える。
		# すぐ失敗した origin（認証の失敗・オフライン）は待っていないので、第 2 周で今までどおり取りに行って理由を言う。
		case "$ccnavi_rt_rc" in
		124) printf '%s\n' "$ccnavi_rt_url" >>"$scratch/rt-failed" ;;
		esac
		return 0
	}
	ccnavi_fetch_ask retarget "$1" "$scratch/rt-out" || return 0
	case "$ccnavi_fx_next" in
	'fetch '*) ccnavi_rt_new="${ccnavi_fx_next#fetch }" ;;
	*) return 0 ;;
	esac
	# そのブランチを取る。成功してから向け直す。
	ccnavi_rt_left=$(ccnavi_fetch_left)
	[ "$ccnavi_rt_left" -gt 0 ] || return 0
	ccnavi_rt_limit="$limit"
	[ "$ccnavi_rt_left" -lt "$ccnavi_rt_limit" ] && ccnavi_rt_limit="$ccnavi_rt_left"
	ccnavi_git_timed "$ccnavi_rt_limit" "$scratch/rt-err" "$1" \
		fetch --quiet origin "+refs/heads/$ccnavi_rt_new:refs/remotes/origin/$ccnavi_rt_new" >/dev/null || return 0
	ccnavi_fetch_ask retarget "$1" "$scratch/rt-out" fetched || :
	return 0
}

# 親子のチケットを早送りする。<ツリー> <P> <親のブランチ> <ロックの置き場の名前（plan の答え）>
#
# 取ってくるのと時間の枠とロックはここ、早送りするかの判定と早送りは実行ファイル（`fetch forward`）。早送りが要ると
# 答えたときだけ、ロックを 1 回だけ試し（取れなければ他の操作の最中）、取れたかを渡して聞き直す。
ccnavi_fetch_forward() {
	ccnavi_fw_key="$4"
	ccnavi_fetch_or_note "$1" "$3" \
		"${2}: リモートを取ってこられなかった。ローカルの版で判定する" \
		"${2}: 親のブランチ ${3} をリモートから取ってこられなかった（リモートにありません）。sh ${root}/.ccnavi/scripts/ccnavi-sync.sh ${2} で確かめてください" ||
		return 0
	ccnavi_fw_now=$(date +%s)
	if [ "$((ccnavi_fw_now - started))" -ge "$budget" ]; then
		printf '%s: 時間の枠（%s 秒）を過ぎたので早送りを省略した。sh %s/.ccnavi/scripts/ccnavi-sync.sh %s で取り込んでください\n' \
			"$2" "$budget" "$root" "$2"
		return 0
	fi
	ccnavi_fetch_ask forward "$2" "$3" || return 0
	[ "$ccnavi_fx_next" = lock ] || return 0
	if ! ccnavi_lock_take "$root" "$ccnavi_fw_key" "$2" 0; then
		ccnavi_fetch_ask forward "$2" "$3" busy || :
		return 0
	fi
	ccnavi_fetch_ask forward "$2" "$3" locked || :
	ccnavi_lock_drop
	return 0
}

# 承認済みチケットを持ちうるツリー。ワークスペース、プロジェクト、ワークツリー。1 行に「<w|-> <ディレクトリ>」。
# w はワークツリーの置き場の下（親子のチケットの親のワークツリーでありうる）。
trees="- $root"
for dir in "$root/$projects"/*; do
	[ -d "$dir" ] || continue
	trees="$trees
- $dir"
done
for dir in "$root/$worktrees"/*; do
	[ -d "$dir" ] || continue
	trees="$trees
w $dir"
done

# デフォルトブランチを持つリポジトリ。ワークスペース自身と、プロジェクト。
#
# ワークツリーは数えない。元リポジトリと ref を共有しているので、元で進めれば届く。
#
# `.ccnavi/approved/` は問わない。 第 1 周が見るのは「承認済みチケットを持つツリー」だが、
# ここで欲しいのは「これから切るワークツリーの起点」で、いま何を持っているかとは別。
repos="$root"
for dir in "$root/$projects"/*; do
	[ -e "$dir/.git" ] || continue
	repos="$repos
$dir"
done

# 実行ファイル。無ければ（入れ替え中など）fetch だけにする（先頭の説明）。
fetch_bin=$(ccnavi_bin "$root") || fetch_bin=""
# 親子のチケットの親のワークツリー（<P> <ロックの置き場の名前> <親のブランチ> を 1 行ずつ）。
: >"$scratch/families"
if [ -n "$fetch_bin" ]; then
	ccnavi_val family "$(ccnavi_ask "$root" fetch plan 2>/dev/null)" >"$scratch/families" || :
fi

# ワークツリーの名前とブランチが親子のチケットの親のワークツリーなら、ロックの置き場の名前を出して 0。<名前> <ブランチ>
ccnavi_fetch_family() {
	while read -r ccnavi_ff_p ccnavi_ff_key ccnavi_ff_branch; do
		if [ "$ccnavi_ff_p" = "$1" ] && [ "$ccnavi_ff_branch" = "$2" ]; then
			printf '%s\n' "$ccnavi_ff_key"
			return 0
		fi
	done <"$scratch/families"
	return 1
}

# 第 1 周が承認済みチケットを持つツリーとして見たツリー（第 2 周は、統合先をチェックアウトしているのがこれなら見ない）。
: >"$scratch/seen"

report=$(
	# 第 1 周。そのツリーがチェックアウトしているブランチを upstream まで進める。
	printf '%s\n' "$trees" | while read -r place tree; do
		[ -n "$tree" ] || continue
		git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
		branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
		[ -n "$branch" ] && [ "$branch" != "HEAD" ] || continue
		name=$(basename "$tree")
		# 親子のチケットは早送りだけ（upstream の設定に依らず origin/<親のブランチ> を見る）。
		if [ "$place" = w ] && key=$(ccnavi_fetch_family "$name" "$branch"); then
			ccnavi_fetch_forward "$tree" "$name" "$branch" "$key"
			continue
		fi

		# どれを取ってくるかは、実行ファイルが無くても決める（fetch はこの sh の本来の仕事）。
		[ -d "$tree/$approved" ] || continue
		git -C "$tree" rev-parse --abbrev-ref "@{u}" >/dev/null 2>&1 || continue
		printf '%s\n' "$tree" >>"$scratch/seen"
		ccnavi_fetch_or_note "$tree" "$branch" \
			"${name}: リモートを取ってこられなかった。ローカルの版で判定する" \
			"${name}: ${branch} がリモートに無いので取ってこられなかった。ローカルの版で判定する" || continue
		ccnavi_fetch_ask track "$tree" "$branch" || :
	done

	# 第 1.5 周。リポジトリごとに、手元の origin/HEAD をホストの既定ブランチに合わせる。
	# 第 2 周は向け直した後の名前で進める。
	printf '%s\n' "$repos" | while IFS= read -r repo; do
		[ -n "$repo" ] || continue
		git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
		git -C "$repo" remote get-url origin >/dev/null 2>&1 || continue
		ccnavi_fetch_retarget "$repo"
	done

	# 第 2 周。リポジトリごとに、ワークツリーの起点になる統合先ブランチを進める。
	printf '%s\n' "$repos" | while IFS= read -r repo; do
		[ -n "$repo" ] || continue
		git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
		git -C "$repo" remote get-url origin >/dev/null 2>&1 || continue
		ccnavi_fetch_ask base "$repo" "$scratch/seen" || continue
		# 次の一手が fetch <統合先> のときだけ進める（`$( )` の中なので case は使わない）。
		default="${ccnavi_fx_next#fetch }"
		[ -n "$default" ] && [ "$default" != "$ccnavi_fx_next" ] || continue
		name=$(basename "$repo")
		ccnavi_fetch_or_note "$repo" "$default" \
			"${name}: 統合先ブランチ ${default} を取ってこられなかった。ローカルの版から切ることになる" \
			"${name}: 統合先ブランチ ${default} がリモートにありません。ユーザに git fetch origin のあと git remote set-head origin -a を頼んでください" ||
			continue
		ccnavi_fetch_ask advance "$repo" "$default" || :
	done
)

[ -n "$report" ] || exit 0
printf '[ccnavi] セッション開始時に、統合先ブランチと親ブランチをリモートの最新に合わせようとした結果です。\n'
printf '%s\n' "$report"
exit 0

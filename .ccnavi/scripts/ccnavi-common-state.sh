# ccnavi-common-state.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# state の置き場とロック、実行ファイルに聞く口、親ブランチの push のゲートの ls-remote、タイムアウト監視つきの git。
# 判定と解釈（統合先・親か・送った形跡・ゲートの次の一手・git が断った理由・同じ識別子のブランチ）は実行ファイルが持つ。
# ここに残すのは、sh で実装するもののうち「外との出入り」（ls-remote と時間の上限）と、実行ファイルに聞く口だけ。

# ---- state の置き場とロック
#
# 1 行 1 項目の `<キー> <値>` のファイルは、sh が `sed -n 's/^<キー> //p'` で読み、jq を使わない
# （JSON とチケットの YAML は実行ファイルが読んで、sh には 1 行で返す）。
# 置き場はワークスペースルートの `logs/state`（固定。ccnavi-review.sh と同じ）。
#
#   locks/<リポジトリ>/<P>/          ロック。中の owner に持ち主を 1 行で書く
#
# <リポジトリ> はワークスペース自身なら `self`、プロジェクトならその名前。
# 親子ごとの取り込み状態や統合先のコピーは持たない（判定は親ブランチとチケットの置き場だけで決まる）。

# state の置き場（`logs/state/`）の絶対パス。
ccnavi_state() {
	printf '%s\n' "$1/logs/state" # 固定
}

# そのツリーが属するリポジトリを分ける名前（ロックの置き場）。<ツリー> <ワークスペースルート>
ccnavi_repo_key() {
	ccnavi_rk_name=$(ccnavi_project "$1" "$2")
	if [ -n "$ccnavi_rk_name" ]; then
		printf '%s\n' "$ccnavi_rk_name"
	else
		printf 'self\n'
	fi
}

# 親のブランチ名として <名前> を受けてよいなら 0。<名前> <識別子>
#
# 識別子と同じ名前は識別子の検査（ccnavi_is_ident）で見る（前からの識別子は ccnavi_is_branch の予約に
# ヒットすることがある）。違う名前は ccnavi_is_branch で見る。どちらも実行ファイルの検査（`ids.IDProblem`・
# `branch_problem`）の、ASCII で見分けられる分だけの複製で、受けたものを使う側の 2 つ目の確認にあたる。
ccnavi_branch_ok() {
	if [ "$1" = "$2" ]; then
		ccnavi_is_ident "$1"
	else
		ccnavi_is_branch "$1"
	fi
}

# ワークツリーの HEAD が指すブランチ名。<ワークツリー> <識別子>
#
# HEAD のブランチ名を変換（ccnavi_branch_to_id。/ を - に置き換え）
# したものがフォルダ名の識別子と一致し、ブランチ名として受けてよいときだけ出す。
# ワークツリーが無い・ブランチの外（detached HEAD）なら 1、一致しない・使えない名前なら 2。
# git は読み取りの 1 回だけ。
ccnavi_head_branch() {
	{ [ -d "$1" ] && [ ! -L "$1" ]; } || return 1
	ccnavi_hb_name=$(git -C "$1" symbolic-ref --quiet --short HEAD 2>/dev/null) || ccnavi_hb_name=""
	[ -n "$ccnavi_hb_name" ] || return 1
	[ "$(ccnavi_branch_to_id "$ccnavi_hb_name")" = "$2" ] || return 2
	ccnavi_branch_ok "$ccnavi_hb_name" "$2" || return 2
	printf '%s\n' "$ccnavi_hb_name"
}

# 実行ファイルに聞く。答え（標準出力）を、Windows の実行ファイルの CR を除いて出す。<ワークスペースルート> <引数>...
#
# 実行ファイルが無ければ 1。実行ファイルが
# 断った・答えなかったときは空を出して 0。答えの頭の行を確かめるのは呼ぶ側。
ccnavi_ask() {
	ccnavi_ak_root="$1"
	shift
	if ccnavi_ak_bin=$(ccnavi_bin "$ccnavi_ak_root"); then
		ccnavi_ak_out=$("$ccnavi_ak_bin" --root "$ccnavi_ak_root" "$@" 2>/dev/null </dev/null) || ccnavi_ak_out=""
	else
		return 1
	fi
	printf '%s\n' "$ccnavi_ak_out" | tr -d '\r'
}

# 文面ファイルの文を、実行ファイル（`ccnavi text`）に引かせて標準出力に出す。
# <ワークスペースルート> <識別子> [<名前>=<値>]... [@<欄>=<場合>]...
#
# 値は引用せずそのまま渡す。コマンドの一部になる値は、文面ファイルの側が `{名前:sh}` で引用する。引数の並びなど
# 呼ぶ側が引用を済ませた値は、名前を `_q` で終える。sh は文面ファイルを直接読まない。
#
# 引けないとき（実行ファイルが無い・版が合わない・識別子や値が合わない・文面ファイルが壊れている）は、
# 識別子の種類に合わせた短い既定の文を出す。どちらでも 0 を返す（呼ぶ側の `set -e` を起こさない）。
# 止める・止めないは呼ぶ側が決めたままで、変えない。
ccnavi_text() {
	ccnavi_tx_root="$1"
	ccnavi_tx_id="$2"
	shift 2
	if ccnavi_tx_out=$(ccnavi_ask "$ccnavi_tx_root" text "$ccnavi_tx_id" "$@") && [ -n "$ccnavi_tx_out" ]; then
		printf '%s\n' "$ccnavi_tx_out"
		return 0
	fi
	ccnavi_tx_gone="ユーザに、ccnavi の実行ファイルと文面ファイル（.ccnavi/messages/）が使えるかを確かめてもらい、返事を待つ（文面 ${ccnavi_tx_id} を引けませんでした）"
	case "$ccnavi_tx_id" in
	CHOICES_*) printf '次の手:\n  1. %s\n' "$ccnavi_tx_gone" ;;
	NOTE_*) printf '案内: 文面 %s を引けませんでした\n' "$ccnavi_tx_id" ;;
	*) printf '次: %s\n' "$ccnavi_tx_gone" ;;
	esac
}

# そのツリーが属するリポジトリの統合先の名前。分からなければ 1。<ツリー>
#
# 決め方（手元の origin/HEAD、無ければ origin/main・origin/master）は実行ファイル（`git integration`。
# integrate.IntegrationName）が持つ。ccnavi-fetch.sh（ワークツリーの起点を進める）・ccnavi-git.sh（統合先への push の
# 拒否）・ccnavi-review.sh（マージリクエストの宛先）が同じ決め方になる。実行ファイルが答えなければ「分からない」
# （実行ファイルを起こさずに動く ccnavi-fetch.sh・ccnavi-user-push-approved.sh は、統合先を使う処理を省く）。
# 実行ファイルの --root はツリーから探したワークスペースルート（無ければツリー）。
ccnavi_integration() {
	ccnavi_ig_ws=$(cd "$1" 2>/dev/null && ccnavi_workspace) || ccnavi_ig_ws="$1"
	ccnavi_ig_name=$(ccnavi_ask "$ccnavi_ig_ws" git integration "$1" | sed -n 's/^integration //p' | head -n 1) || ccnavi_ig_name=""
	[ -n "$ccnavi_ig_name" ] || return 1
	printf '%s\n' "$ccnavi_ig_name"
}

# 統合先が決まらないときの案内の 1 文。<ツリー>
#
# 文は実行ファイル（`git integration` の hint の行。integrate.IntegrationHint）が持つ。origin が無ければ「origin が無い」と言い、
# あれば origin/HEAD を直す方法を示す。呼ぶ側が前置きを付ける。実行ファイルが答えなければ、組むか置くかを言う。
ccnavi_integration_hint() {
	ccnavi_ih_ws=$(cd "$1" 2>/dev/null && ccnavi_workspace) || ccnavi_ih_ws="$1"
	ccnavi_ih_text=$(ccnavi_ask "$ccnavi_ih_ws" git integration "$1" | sed -n 's/^hint //p' | head -n 1) || ccnavi_ih_text=""
	if [ -n "$ccnavi_ih_text" ]; then
		printf '%s' "$ccnavi_ih_text"
	else
		printf '統合先が決まらない（実行ファイルが答えない）。実行ファイルを組む（sh scripts/ccnavi-build.sh）か置いてから打ち直してください。'
	fi
}

# そのツリーが、名前の親子のチケットの親のワークツリーか。<ツリー> <名前>
#
# 置き場（承認済みの doing/・done/、提案の todo/・review/）に `ticket: <名前>` の親チケットか提案が
# あれば 0（SessionStart で早送りする対象の条件の 1 つ）。ready が退避した後は、手元の退避の親チケットも見る。
# 子チケット（`parent:` を持つ）は数えない。
#
# sh はチケットを読まない。読むのは実行ファイル（`tree parent <ツリー> <名前>`）で、sh は答えの 1 行を見る。
# 親でなければ 1。ツリーが無ければ聞かずに 1。決められなければ 2（実行ファイルが無い・答えない・古くて
# サブコマンドを知らない、置き場に名前のファイルが在るのに frontmatter を読めない）。2 をどちらに倒すかは
# 呼ぶ側が決める。止める側の検査は親として扱い、ツリーを動かす側は親でないとして扱う。
# 実行ファイルの --root に渡すワークスペースルートは `.claude/worktrees/<名前>` の 2 つ上。
ccnavi_parent_tree() {
	ccnavi_parent_info "$1" "$2" || return 2
	case "$ccnavi_pi_parent" in
	yes) return 0 ;;
	no) return 1 ;;
	esac
	return 2
}

# `tree parent` の答えを変数に入れる。ツリーが無ければ 1、答えを得られなければ 2。<ツリー> <名前> [<ブランチ>]
#
# 入れる変数は、どれも yes / no / unreadable / unknown のどれか（unknown は、実行ファイルが無い・答えない）。
#   ccnavi_pi_parent     そのツリーが親のワークツリーか
#   ccnavi_pi_approved   親のワークツリーの承認済み（approved/ の doing か done）に親チケットがある（見るのは承認済みのチケットだけ）
#   ccnavi_pi_requested  承認済みの phases/<名前>/ に <N>.requested が 1 つ以上ある
#   ccnavi_pi_archived   手元の退避（ready 済み）に親がある
#   ccnavi_pi_started    承認済みの親チケットの started_at が空でない（着手は push してから完了する）
#   ccnavi_pi_mr         親チケットの mr 欄（無ければ空）
#   ccnavi_pi_trace      <ブランチ> を渡したときだけ。親のブランチを一度は送った形跡があるか（yes / no / unknown。
#                        手元の origin の参照・.requested・退避・着手済み。push のゲートと同じ述語）
ccnavi_parent_info() {
	ccnavi_pi_parent=unknown
	ccnavi_pi_approved=unknown
	ccnavi_pi_requested=unknown
	ccnavi_pi_archived=unknown
	ccnavi_pi_started=unknown
	ccnavi_pi_mr=""
	ccnavi_pi_trace=unknown
	[ -d "$1" ] || return 1
	case "$1" in
	*/.claude/worktrees/*) ccnavi_pi_ws="${1%/.claude/worktrees/*}" ;;
	*) ccnavi_pi_ws=$(ccnavi_workspace) || return 2 ;;
	esac
	ccnavi_pi_out=$(ccnavi_ask "$ccnavi_pi_ws" tree parent "$1" "$2" ${3:+"$3"}) || return 2
	[ "$(printf '%s\n' "$ccnavi_pi_out" | head -n 1)" = "tree 1" ] || return 2
	for ccnavi_pi_key in parent approved requested archived started trace; do
		ccnavi_pi_ans=$(printf '%s\n' "$ccnavi_pi_out" | sed -n "s/^$ccnavi_pi_key //p" | head -n 1)
		case "$ccnavi_pi_ans" in
		yes | no | unreadable) ;;
		*) ccnavi_pi_ans=unknown ;;
		esac
		case "$ccnavi_pi_key" in
		parent) ccnavi_pi_parent="$ccnavi_pi_ans" ;;
		approved) ccnavi_pi_approved="$ccnavi_pi_ans" ;;
		requested) ccnavi_pi_requested="$ccnavi_pi_ans" ;;
		archived) ccnavi_pi_archived="$ccnavi_pi_ans" ;;
		started) ccnavi_pi_started="$ccnavi_pi_ans" ;;
		trace) ccnavi_pi_trace="$ccnavi_pi_ans" ;;
		esac
	done
	ccnavi_pi_mr=$(printf '%s\n' "$ccnavi_pi_out" | sed -n 's/^mr //p' | head -n 1)
	[ "$ccnavi_pi_mr" != "-" ] || ccnavi_pi_mr=""
	return 0
}

# 親のブランチの push のゲート。0 通す、1 拒否、2 確かめられない（拒否側）。
#   ccnavi_push_gate <ツリー> <ブランチ> <識別子> <ワークスペースルート> [<秒>]
# 理由は変数 ccnavi_pg_why に入る（拒否と確かめられないとき）。
#
# 素の push を打つ 3 経路（ccnavi-git.sh の push・write-and-push・ccnavi-user-push-approved.sh）が共有する。
# 掛けるかと、形跡から ls-remote が要るかは実行ファイル（`tree gate`。shask の pushGate）が次の一手の語で返す
# （pass・stop・ls-remote）。ここに残すのは「外との出入り」の ls-remote と、その時間の上限だけ。
#
# 実行ファイルが答えない（入れ替え中など。実行ファイルを起こさずに push する ccnavi-user-push-approved.sh だけが
# ここに来る）ときは、手元の `refs/remotes/origin/<ブランチ>` が在るときだけ ls-remote で確かめる。親のワークツリーか
# は確かめられないので、リモートから消えたブランチへは、親でなくても送らない（送るのはユーザが確かめてから）。
ccnavi_push_gate() {
	ccnavi_pg_why=""
	ccnavi_pg_b="$2"
	ccnavi_pg_secs="${5:-15}"
	ccnavi_pg_out=$(ccnavi_ask "$4" tree gate "$1" "$2" "$3" "$ccnavi_pg_secs") || ccnavi_pg_out=""
	ccnavi_pg_line() { printf '%s\n' "$ccnavi_pg_out" | sed -n "s/^$1 //p" | head -n 1; }
	if [ "$(printf '%s\n' "$ccnavi_pg_out" | head -n 1)" = "tree 1" ]; then
		ccnavi_pg_next=$(ccnavi_pg_line gate)
		ccnavi_pg_missing=$(ccnavi_pg_line missing)
		ccnavi_pg_unreachable=$(ccnavi_pg_line unreachable)
	else
		ccnavi_pg_next=pass
		git -C "$1" show-ref --verify --quiet "refs/remotes/origin/$ccnavi_pg_b" 2>/dev/null && ccnavi_pg_next=ls-remote
		ccnavi_pg_missing="実行ファイルが答えないため、$ccnavi_pg_b が親のブランチかを確かめられない。$ccnavi_pg_b はリモートに無いので送らない。実行ファイルを組む（sh scripts/ccnavi-build.sh）か置いてから打ち直してください"
		ccnavi_pg_unreachable="$ccnavi_pg_b がリモートにあるかを確かめられない（ls-remote が失敗したか、${ccnavi_pg_secs} 秒で終わらなかった）。ネットワークと認証を確かめて打ち直してください"
	fi
	case "$ccnavi_pg_next" in
	pass) return 0 ;;
	ls-remote) ;;
	*)
		ccnavi_pg_why=$(ccnavi_pg_line why)
		return 2
		;;
	esac
	ccnavi_pg_err=$(mktemp 2>/dev/null || mktemp -t ccnavi-pg) || {
		ccnavi_pg_why="親のブランチ $ccnavi_pg_b がリモートにあるかを確かめられない（一時ファイルを作れない）"
		return 2
	}
	ccnavi_pg_lsout="$ccnavi_pg_err.out"
	ccnavi_pg_rc=0
	ccnavi_git_timed "$ccnavi_pg_secs" "$ccnavi_pg_err" "$1" ls-remote --heads origin "refs/heads/$ccnavi_pg_b" >"$ccnavi_pg_lsout" || ccnavi_pg_rc=$?
	# ls-remote の答えの 2 列目が、送る ref と一致する行があるか（段取りの最小の読み）。
	ccnavi_pg_found=""
	if awk -F '\t' -v r="refs/heads/$ccnavi_pg_b" '$2 == r { f = 1 } END { exit f ? 0 : 1 }' "$ccnavi_pg_lsout" 2>/dev/null; then
		ccnavi_pg_found=yes
	fi
	rm -f "$ccnavi_pg_err" "$ccnavi_pg_lsout"
	if [ "$ccnavi_pg_rc" -ne 0 ]; then
		ccnavi_pg_why="$ccnavi_pg_unreachable"
		return 2
	fi
	[ -z "$ccnavi_pg_found" ] || return 0
	ccnavi_pg_why="$ccnavi_pg_missing"
	return 1
}

# ---- タイムアウト監視つきの git（取ってくる操作。ccnavi-fetch.sh と ccnavi-sync.sh が使う）
#
#   ccnavi_git_timed <秒> <標準エラーの書き先> <リポジトリ> <git の引数>...
#
# 認証を尋ねさせず（GIT_TERMINAL_PROMPT=0・GCM_INTERACTIVE=never、ssh は BatchMode）、<秒> で切る。
# ssh の BatchMode は、ユーザが GIT_SSH_COMMAND・GIT_SSH・core.sshCommand を持っていればそちらを尊重する。
# git 用の環境を前置きする薄い層。時間の監視は ccnavi_run_timed（ccnavi-common-host.sh）に任せる。
# 戻り値は git のもの。時間切れで切ったときは 124（ccnavi_run_timed と同じ）。標準出力は呼ぶ側がリダイレクトする。
ccnavi_git_timed() {
	ccnavi_gt_limit="$1"
	ccnavi_gt_err="$2"
	ccnavi_gt_repo="$3"
	shift 3
	ccnavi_git_auth "$ccnavi_gt_repo"
	ccnavi_gt_ssh="${GIT_SSH_COMMAND:-}"
	if [ -z "$ccnavi_gt_ssh" ] && [ -z "${GIT_SSH:-}" ] &&
		! git -C "$ccnavi_gt_repo" config --get core.sshCommand >/dev/null 2>&1; then
		ccnavi_gt_ssh="ssh -o BatchMode=yes"
	fi
	if [ -n "$ccnavi_gt_ssh" ]; then
		ccnavi_run_timed "$ccnavi_gt_limit" /dev/null - "$ccnavi_gt_err" \
			env GIT_SSH_COMMAND="$ccnavi_gt_ssh" GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never LC_ALL=C \
			git -C "$ccnavi_gt_repo" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 "$@"
	else
		ccnavi_run_timed "$ccnavi_gt_limit" /dev/null - "$ccnavi_gt_err" \
			env GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never LC_ALL=C \
			git -C "$ccnavi_gt_repo" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 "$@"
	fi
}

# git が拒んだ理由を 1 行にする。<ワークスペースルート> <標準エラーを書いたファイル>
#
# 読み方（書きかけとの重なり・索引のロック・ユーザの名前・署名・hook・それ以外は git の 1 行目）は実行ファイル
# （`git refusal`。refusal.Of）が持つ。実行ファイルが答えないとき（実行ファイルを起こさずに動く ccnavi-fetch.sh・
# ccnavi-user-push-approved.sh で、入れ替え中など）は、読み分けを省いて git の 1 行目だけを添える。
ccnavi_git_refusal() {
	ccnavi_gr_text=$(ccnavi_ask "$1" git refusal "$2" | sed -n 's/^refusal //p' | head -n 1) || ccnavi_gr_text=""
	if [ -n "$ccnavi_gr_text" ]; then
		printf '%s' "$ccnavi_gr_text"
	else
		printf 'git が拒んだ（%s）' "$(head -n 1 "$2" 2>/dev/null)"
	fi
}

# ccnavi-common-state.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# state の置き場とロック、実行ファイルに聞く口と答えを読む口、親ブランチの push のゲートの ls-remote、タイムアウト監視つきの git。
# 判定と解釈（統合先・親か・送った形跡・ゲートの次の一手・git が断った理由・同じ識別子のブランチ）は実行ファイルが持つ。
# ここに残すのは、sh で実装するもののうち「外との出入り」（ls-remote と時間の上限）と、実行ファイルに聞く口と答えを読む口だけ。

# ---- state の置き場とロック
#
# 1 行 1 項目の `<キー> <値>` のファイルは、sh が ccnavi_val（下の「実行ファイルの答えを読む口」）で読み、jq を使わない
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

# ---- 実行ファイルの答え（1 行 1 項目）を読む口。どの sh もここを使い、行の読み方を写さない。
#
#   ccnavi_val <名前> [<答え>]   <名前> の行の値。同じ名前の行が続けば改行でつなぐ。無ければ空
#   ccnavi_val1 <名前> [<答え>]  <名前> の最初の行の値
#   ccnavi_has <名前> [<答え>]   <名前> の行（値の無い語だけの行も）があれば 0
#   ccnavi_do <答え> [<理由>]    out・err の行を出し、fail ならその文で止め、exit ならその終了コードで終える
#
# <答え> を省くと、直前に ccnavi_do に渡した答え（ccnavi_said）を読む。行の終わりの CR（Windows の実行ファイル）は除く。
# 名前は awk で行の頭と文字どおりに比べる（sed の式に入れると、名前やスレッドの id の字が式として働くため）。
ccnavi_said=""

ccnavi_val() {
	if [ "$#" -ge 2 ]; then ccnavi_vl_in="$2"; else ccnavi_vl_in="$ccnavi_said"; fi
	printf '%s\n' "$ccnavi_vl_in" | awk -v k="$1 " '{ sub(/\r$/, "") } index($0, k) == 1 { print substr($0, length(k) + 1) }'
}

ccnavi_val1() {
	if [ "$#" -ge 2 ]; then ccnavi_vl_in="$2"; else ccnavi_vl_in="$ccnavi_said"; fi
	printf '%s\n' "$ccnavi_vl_in" | awk -v k="$1 " '{ sub(/\r$/, "") } index($0, k) == 1 { print substr($0, length(k) + 1); exit }'
}

ccnavi_has() {
	if [ "$#" -ge 2 ]; then ccnavi_vl_in="$2"; else ccnavi_vl_in="$ccnavi_said"; fi
	printf '%s\n' "$ccnavi_vl_in" | awk -v k="$1" '{ sub(/\r$/, "") } $0 == k || index($0, k " ") == 1 { f = 1; exit } END { exit f ? 0 : 1 }'
}

# 段取りの判定と出力を実行ファイルに任せた答え（`infra/reply` の形）を実行する。<答え> [<理由>]
#
#   out <行> / err <行>      標準出力・標準エラーにそのまま出す
#   help                     呼ぶ側の usage を標準出力に出す
#   fail <識別子> <終了コード> と msg <行>  呼ぶ側の fail <識別子> <文> <終了コード> で止める（usage の行があれば先に usage を標準エラーへ）
#   exit <終了コード>        出し終えてからその終了コードで終える（0 でなければ診断ログに reason の行か <理由> を残す）
#
# 呼ぶ側は fail・usage・log_info を持つこと。答えの値は ccnavi_val で読む。
ccnavi_do() {
	ccnavi_said="$1"
	ccnavi_dd_say=$(ccnavi_val out)
	[ -z "$ccnavi_dd_say" ] || printf '%s\n' "$ccnavi_dd_say"
	ccnavi_dd_say=$(ccnavi_val err)
	[ -z "$ccnavi_dd_say" ] || printf '%s\n' "$ccnavi_dd_say" >&2
	! ccnavi_has help || usage
	ccnavi_dd_fail=$(ccnavi_val1 fail)
	if [ -n "$ccnavi_dd_fail" ]; then
		! ccnavi_has usage || usage >&2
		fail "${ccnavi_dd_fail% *}" "$(ccnavi_val msg)" "${ccnavi_dd_fail##* }"
	fi
	ccnavi_dd_exit=$(ccnavi_val1 exit)
	if [ -n "$ccnavi_dd_exit" ]; then
		ccnavi_dd_reason=$(ccnavi_val1 reason)
		[ "$ccnavi_dd_exit" = 0 ] || log_info 止めた -- "exit=$ccnavi_dd_exit" "reason=${ccnavi_dd_reason:-${2:-}}"
		exit "$ccnavi_dd_exit"
	fi
	return 0
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
	ccnavi_ig_name=$(ccnavi_val1 integration "$(ccnavi_ask "$ccnavi_ig_ws" git integration "$1")") || ccnavi_ig_name=""
	[ -n "$ccnavi_ig_name" ] || return 1
	printf '%s\n' "$ccnavi_ig_name"
}

# 統合先が決まらないときの案内の 1 文。<ツリー>
#
# 文は実行ファイル（`git integration` の hint の行。integrate.IntegrationHint）が持つ。origin が無ければ「origin が無い」と言い、
# あれば origin/HEAD を直す方法を示す。呼ぶ側が前置きを付ける。実行ファイルが答えなければ、組むか置くかを言う。
ccnavi_integration_hint() {
	ccnavi_ih_ws=$(cd "$1" 2>/dev/null && ccnavi_workspace) || ccnavi_ih_ws="$1"
	ccnavi_ih_text=$(ccnavi_val1 hint "$(ccnavi_ask "$ccnavi_ih_ws" git integration "$1")") || ccnavi_ih_text=""
	if [ -n "$ccnavi_ih_text" ]; then
		printf '%s' "$ccnavi_ih_text"
	else
		printf '統合先が決まらない（実行ファイルが答えない）。実行ファイルを組む（sh scripts/ccnavi-build.sh）か置いてから打ち直してください。'
	fi
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
		ccnavi_pi_ans=$(ccnavi_val1 "$ccnavi_pi_key" "$ccnavi_pi_out")
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
	ccnavi_pi_mr=$(ccnavi_val1 mr "$ccnavi_pi_out")
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
	if [ "$(printf '%s\n' "$ccnavi_pg_out" | head -n 1)" = "tree 1" ]; then
		ccnavi_pg_next=$(ccnavi_val1 gate "$ccnavi_pg_out")
		ccnavi_pg_missing=$(ccnavi_val1 missing "$ccnavi_pg_out")
		ccnavi_pg_unreachable=$(ccnavi_val1 unreachable "$ccnavi_pg_out")
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
		ccnavi_pg_why=$(ccnavi_val1 why "$ccnavi_pg_out")
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
	ccnavi_gr_text=$(ccnavi_val1 refusal "$(ccnavi_ask "$1" git refusal "$2")") || ccnavi_gr_text=""
	if [ -n "$ccnavi_gr_text" ]; then
		printf '%s' "$ccnavi_gr_text"
	else
		printf 'git が拒んだ（%s）' "$(head -n 1 "$2" 2>/dev/null)"
	fi
}

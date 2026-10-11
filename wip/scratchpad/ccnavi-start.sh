#!/bin/sh
# ccnavi-start issue・MRを指定して作業を頼まれたとき、既存のものを確かめ、無ければ Draft MR・ワークツリー・
# feature ブランチを先に作る。
#
#   sh .ccnavi/scripts/ccnavi-start.sh --issue <番号> [--slug <語>] [--prefix <語>] [--title <題>] [--dry-run] [--mcp-checked]
#   sh .ccnavi/scripts/ccnavi-start.sh --mr <番号> [--dry-run]
#
# 流れ（docs/claude/worktree.md）:
#   1. ccnavi-branches.sh --json で既存の候補（手元＋ホスト）を探す
#   2. 候補が 1 件ならそのブランチで続ける（ワークツリーが無ければ .claude/worktrees/<識別子> に出す）。
#      複数なら一覧を出して終わる（ユーザが選ぶ）。0 件で --issue なら新しく作る
#   3. 新規作成: <先頭の語>-<番号>-<slug> のブランチを統合先から切り、ワークツリー・空の初期コミット・push・Draft MR
#   途中で落ちても、打ち直せば出来たところを飛ばして続きから進む（ブランチ・push・MRの有無を確かめる）。
#
# git は ccnavi-git.shを通す（読むだけの確認は git を直接呼ぶ。他のスクリプトと同じ）。ホストへの繋ぎ方は
# ccnavi-common-host.sh（ccnavi-review.sh・ccnavi-branches.sh と同じ）。
#
# 終了コード: 0 完了 / 1 前提の未充足 / 2 引数か環境の誤り / 3 候補が複数（何も作らず一覧を出した。ユーザが選ぶ） /
#             4 ホストに届かず、その段を MCPなどで代行してほしい（案内を標準出力に出した。代行したら打ち直す）

set -eu

EXIT_MULTI=3
EXIT_HOST=4

# fail <識別子> <文面> [終了コード]。文面は標準エラーへ出す契約。識別子は診断ログにだけ残す。
fail() {
	printf 'ccnavi-start: %s\n' "$2" >&2
	log_info 止めた -- "exit=${3:-1}" "reason=$1"
	exit "${3:-1}"
}

# 共通部分。ワークスペースルートの探し方と、ホストへの繋ぎ方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-start.sh (--issue <番号> | --mr <番号>) [--slug <語>] [--prefix <語>] [--title <題>] [--dry-run] [--mcp-checked]

  issue・MR に紐づくブランチを探し（ccnavi-branches.sh）、候補に応じて進める。
  候補が 1 件   そのブランチで続ける。ワークツリーがあればパスを出すだけ。無ければ .claude/worktrees/<識別子> に出す
                （名前に / を含むブランチは識別子を / -> - にして切り、switchで移る）
  候補が複数    何も作らず一覧を出して終わる（終了コード 3）。ユーザが選ぶ
  候補が 0 件   --issue なら新しく作る。ブランチ <先頭の語>-<番号>-<slug> を統合先から切り、ワークツリー・空の初期コミット・
                push・Draft MR（本文に Closes #<番号>）まで。--mr は元ブランチが候補なので、無ければ止める
  --mr          フォークから出た MRは元ブランチがこのリポジトリにないので、理由を出して止める
  --slug <語>   ブランチ名の slug（小文字・数字・ハイフン）。省くと issueの題から作る（作れなければ止まる）
  --prefix <語> 先頭の語（既定 feature）
  --title <題>  MRの題。省くと issueの題、読めなければ「#<番号> <slug>」
  --dry-run     何も作らず、作る予定の内容だけ出す（リモートは読むだけ）
  --mcp-checked ホストの APIに届かず、既存の MRがないことを MCPなどで確かめたあとに付ける

  ホストを見られないときは、既存の MRを見落として重複を作らないよう新規作成に進まず止める（手元の候補がちょうど 1 件なら続ける）。
  GitHubのAPIに届かないとき（Claude remote など）は、MCPで代行する手順を標準出力に出して終了コード 4 で止まる。
  代行したあと同じコマンドを打ち直すと、出来たところを飛ばして続きから進む。
  冪等。cwdのリポジトリ（ワークスペース・projects/<名前>）を対象にする。

終了コード: 0 完了 / 1 前提の未充足 / 2 引数か環境の誤り / 3 候補が複数 / 4 ホストに届かず代行が要る
USAGE
}

what=""
number=""
opt_slug=""
opt_prefix="feature"
opt_title=""
dry=""
mcp_checked=""
# 打ち直しの案内に使う。引数ごとに ' で包み（中の ' は '\''）、そのまま打てば同じ引数になるようにする。
# rerun_args は --mcp-checked を除いたもの（MRを作る段の打ち直しに使う。付けたままだと、MCPで作った MRを見ずに止まり続ける）
sh_quote() {
	printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}
orig_args=""
rerun_args=""
for arg in "$@"; do
	q=$(sh_quote "$arg")
	orig_args="$orig_args $q"
	[ "$arg" = --mcp-checked ] || rerun_args="$rerun_args $q"
done
orig_args="${orig_args# }"
rerun_args="${rerun_args# }"
while [ "$#" -gt 0 ]; do
	case "$1" in
	--issue | --mr)
		[ -z "$what" ] || fail two-kinds "--issue と --mr はどちらか 1 つだけ渡してください。" 2
		what="${1#--}"
		[ "$#" -ge 2 ] || fail no-number "$1 の後ろに番号を渡してください。" 2
		number="$2"
		shift 2
		;;
	--slug | --prefix | --title)
		[ "$#" -ge 2 ] || fail no-value "$1 の後ろに値を渡してください。" 2
		case "$1" in
		--slug) opt_slug="$2" ;;
		--prefix) opt_prefix="$2" ;;
		--title) opt_title="$2" ;;
		esac
		shift 2
		;;
	--dry-run)
		dry=1
		shift
		;;
	--mcp-checked)
		mcp_checked=1
		shift
		;;
	-h | --help | help)
		usage
		exit 0
		;;
	*)
		usage >&2
		fail unknown-arg "知らない引数です（$1）。" 2
		;;
	esac
done
[ -n "$what" ] || {
	usage >&2
	fail no-kind "--issue <番号> か --mr <番号> を渡してください。" 2
}
case "$number" in
'' | 0* | *[!0-9]*) fail bad-number "番号は 0 で始まらない正の整数で渡してください（${number}）。" 2 ;;
esac
[ "${#number}" -le 9 ] || fail bad-number "番号が長すぎます（${number}）。" 2

# 語（slug・先頭の語）は小文字・数字・ハイフンだけ。
is_word() {
	printf '%s' "$1" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$'
}
if [ -n "$opt_slug" ]; then
	is_word "$opt_slug" || fail bad-slug "--slug は小文字・数字・ハイフンだけで書いてください（${opt_slug}）。" 2
fi
is_word "$opt_prefix" || fail bad-prefix "--prefix は小文字・数字・ハイフンだけで書いてください（${opt_prefix}）。" 2
if [ "$what" = mr ] && { [ -n "$opt_slug" ] || [ -n "$opt_title" ]; }; then
	fail mr-with-new "--mr には --slug と --title を付けられません（既存の MRの元ブランチで続けるだけです）。" 2
fi
[ "$what" = issue ] || [ -z "$mcp_checked" ] || fail mr-mcp-checked "--mcp-checked は --issueのときだけ使います。--mr は元ブランチを MCPで読んでから、docs/claude/worktree.md の手順で切ってください。" 2

command -v jq >/dev/null 2>&1 || fail no-jq "jq が無い。候補の JSONを読めません。" 2

root=$(ccnavi_workspace) ||
	fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.shを持つ親を cwdから上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
ccnavi_log_root="$root"
here="$(pwd -W 2>/dev/null || pwd)"
top=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -n "$top" ] ||
	fail not-git "cwd（${here}）が gitのリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で打ってください。"
state="$root/logs/state" # 固定
mkdir -p "$state" 2>/dev/null || fail no-state "state の置き場（${state}）を作れません。" 2
scripts="$root/.ccnavi/scripts"
host_err="$state/start-host-err-$$"
cand_json="$state/start-candidates-$$.json"
trap 'rm -f "$host_err" "$host_err.status" "$cand_json"' EXIT
log_info 始めた -- "kind=$what" "number=$number" "dry=${dry:-no}"

# ---- ccnavi-git.sh 経由の git。成功は何も出さず、失敗は出力を標準エラーへ出して 1 を返す（cwd で動く）。
cgit() {
	cgit_out=$(sh "$scripts/ccnavi-git.sh" "$@" 2>&1) || {
		printf '%s\n' "$cgit_out" >&2
		return 1
	}
	log_debug git を通した -- "sub=$1"
	return 0
}

# ---- ホスト。origin を読んで繋ぐ。呼ぶのは親のシェルで（結果を変数に残す）、$( ) の中では呼ばない。
host_tried=""
host_rc=0
host_why=""
host_open() {
	[ -z "$host_tried" ] || return "$host_rc"
	host_tried=1
	host_rc=0
	origin=$(git remote get-url origin 2>/dev/null || :)
	if [ -z "$origin" ]; then
		host_why="origin が無い"
		host_rc=1
		return 1
	fi
	if ! ccnavi_host_parse "$origin"; then
		host_why="originの URLを読めない（$(ccnavi_mask_url "$origin")）"
		host_rc=1
		return 1
	fi
	connect_rc=0
	ccnavi_host_connect || connect_rc=$?
	case "$connect_rc" in
	0) ;;
	3) fail no-jq "jq が無い。ホストの返事を読めません。" 2 ;;
	4)
		host_why="$ccnavi_h_cli_name が $ccnavi_h_host で使えず（未導入か未認証）、curl に付ける $ccnavi_h_token_name も無い"
		host_rc=2
		return 2
		;;
	*)
		host_why="$ccnavi_h_cli_name が $ccnavi_h_host で使えず、curl も無い"
		host_rc=2
		return 2
		;;
	esac
	ccnavi_h_tmp="$state"
	ccnavi_h_on_fail=host_api_failed
	ccnavi_h_max_time="${CCNAVI_START_HOST_TIMEOUT:-30}"
	return 0
}

# APIが落ちたら、どの呼び出しかを覚えておく（`$( )` の中から呼ばれるのでファイルに書く）。ホストの返事は書き出さない。
host_api_failed() {
	printf '%s %s' "$1" "$2" >"$host_err"
	# ホストが HTTP のエラーを返したなら、その番号も残す（届いてはいるのか、届かないのかを見分けるため）。
	# curl は「returned error: 404」、gh は「(HTTP 404)」、glab は「404 Not Found」の形
	printf '%s' "$3" | sed -n -E -e 's/.*returned error: ([0-9]{3}).*/\1/p' -e 's/.*\(HTTP ([0-9]{3})\).*/\1/p' -e 's/^[^0-9]*([0-9]{3}) (Not Found|Forbidden|Unauthorized|Gone).*/\1/p' | head -n 1 >"$host_err.status"
}
api_failed_reason() {
	what_failed=$(cat "$host_err" 2>/dev/null || :)
	printf 'ホストの APIが失敗した（%s）' "${what_failed:-?}"
}

# 親のシェルで呼ぶ。結果は issue_title_read（題。1 行、制御文字は空白）と issue_why（失敗の理由）に入れる。
# 戻り値: 0 読めた / 1 APIが落ちた（ホストに届いていない） / 2 issue が無い・権限が無い（ホストには届いた） / 3 PR の番号だった
issue_title_read=""
issue_why=""
issue_title_get() {
	issue_title_read=""
	issue_why=""
	rm -f "$host_err" "$host_err.status"
	if [ "$ccnavi_h_kind" = github ]; then
		it_path="repos/$ccnavi_h_path/issues/$number"
	else
		it_path="projects/$(ccnavi_host_encoded_path)/issues/$number"
	fi
	it_got=$(ccnavi_host_api GET "$it_path") || {
		it_status=$(cat "$host_err.status" 2>/dev/null || :)
		case "$it_status" in
		401 | 403)
			issue_why="権限が無い（HTTP ${it_status}）"
			return 2
			;;
		404 | 410)
			issue_why="見つからない（HTTP ${it_status}）"
			return 2
			;;
		esac
		return 1
	}
	if [ "$ccnavi_h_kind" = github ]; then
		it_pr=$(printf '%s' "$it_got" | "$ccnavi_h_jq" -r '(.pull_request // null) != null') || return 1
		if [ "$it_pr" = true ]; then
			issue_why="PR の番号だった"
			return 3
		fi
	fi
	it_raw=$(printf '%s' "$it_got" | "$ccnavi_h_jq" -r '.title // empty') || return 1
	issue_title_read=$(printf '%s\n' "$it_raw" | head -n 1 | LC_ALL=C tr '\001-\037\177' ' ' | sed -e 's/^ *//' -e 's/ *$//')
	return 0
}

# 題から ASCIIの小文字・数字・ハイフンの slugを作る（40 字まで）。作れなければ空
slugify() {
	printf '%s' "$1" | head -n 1 | LC_ALL=C tr 'A-Z' 'a-z' |
		LC_ALL=C sed -e 's/[^a-z0-9]\{1,\}/-/g' -e 's/^-//' -e 's/-$//' |
		cut -c1-40 | sed -e 's/-$//'
}

# 開いた MRを探す。あれば {number, url}、無ければ空。APIが落ちたら 1
find_mr() {
	if [ "$ccnavi_h_kind" = github ]; then
		fm_owner="${ccnavi_h_path%%/*}"
		fm_got=$(ccnavi_host_api GET "repos/$ccnavi_h_path/pulls?state=open&head=$fm_owner:$1") || return 1
		printf '%s' "$fm_got" | "$ccnavi_h_jq" '.[0] // empty | {number: .number, url: .html_url}'
	else
		fm_pid=$(ccnavi_host_project_id) || return 1
		fm_rc=0
		fm_got=$(ccnavi_host_pages "projects/$(ccnavi_host_encoded_path)/merge_requests?state=opened&source_branch=$1") || fm_rc=$?
		[ "$fm_rc" -eq 0 ] || return 1
		printf '%s' "$fm_got" | "$ccnavi_h_jq" --argjson pid "$fm_pid" \
			'[.[] | select(.source_project_id == $pid)][0] // empty | {number: .iid, url: .web_url}'
	fi
}

# Draft の MRを作る。{number, url} を出す。<ブランチ> <統合先> <題> <本文>
create_mr() {
	if [ "$ccnavi_h_kind" = github ]; then
		cm_payload=$("$ccnavi_h_jq" -n --arg h "$1" --arg b "$2" --arg t "$3" --arg d "$4" \
			'{head: $h, base: $b, title: $t, body: $d, draft: true}')
		cm_got=$(ccnavi_host_api POST "repos/$ccnavi_h_path/pulls" "$cm_payload") || return 1
		printf '%s' "$cm_got" | "$ccnavi_h_jq" -e '{number: .number, url: .html_url} | select(.number != null)'
	else
		cm_payload=$("$ccnavi_h_jq" -n --arg s "$1" --arg b "$2" --arg t "Draft: $3" --arg d "$4" \
			'{source_branch: $s, target_branch: $b, title: $t, description: $d}')
		cm_got=$(ccnavi_host_api POST "projects/$(ccnavi_host_encoded_path)/merge_requests" "$cm_payload") || return 1
		printf '%s' "$cm_got" | "$ccnavi_h_jq" -e '{number: .iid, url: .web_url} | select(.number != null)'
	fi
}

# ---- ホストに届かないときの案内。標準出力に出して終了コード 4 で止まる。
#
# 段は search（既存の MRを確かめる）・title（issueの題を読む）・create（MRを作る）。
# GitHub は MCP（mcp__github__のツール）で代行する手順を出す。GitLab には MCPの案内が無いので、理由と手で打つ手順を出す。
# 使う変数: stop_branch・stop_target・stop_title・stop_wt（段より前に出来たこと）
stop_branch=""
stop_target=""
stop_title=""
stop_wt=""
stop_pushed=""
stop_local=""
stop_host() {
	sh_stage="$1"
	sh_why="$2"
	log_info ホストに届かず止めた -- "stage=$sh_stage" "kind=${ccnavi_h_kind:-}"
	if [ -z "${ccnavi_h_kind:-}" ]; then
		fail host-unknown "ホストを読めない（${sh_why}）。origin を確かめてください。"
	fi
	sh_owner="${ccnavi_h_path%%/*}"
	sh_repo="${ccnavi_h_path#*/}"
	# MRを作る段では --mcp-checked を除く。付けたままだと、MCPで作った MRを見ずに ensure_mr が止まり続ける
	if [ "$sh_stage" = create ]; then
		sh_cmd="sh .ccnavi/scripts/ccnavi-start.sh $rerun_args"
	else
		sh_cmd="sh .ccnavi/scripts/ccnavi-start.sh $orig_args"
	fi
	if [ "$ccnavi_h_kind" = github ]; then
		case "$sh_stage" in
		search) printf 'ccnavi-start: GitHubのAPIに届かず、%s #%s に紐づく既存の MRを確かめられなかった（%s）。既存の MRを見落として重複を作らないため、何も作らずに止めた。MCP（mcp__github__のツール）で代行してください。\n' "$what" "$number" "$sh_why" ;;
		title) printf 'ccnavi-start: GitHubのAPIに届かず、issue #%s の題を読めなかった（%s）。slugを決められないので、何も作らずに止めた。MCP（mcp__github__のツール）で代行してください。\n' "$number" "$sh_why" ;;
		*) printf 'ccnavi-start: GitHubのAPIに届かず、マージリクエストを作れなかった（%s）。MCP（mcp__github__のツール）で代行してください。\n' "$sh_why" ;;
		esac
		printf 'ここまでに出来たこと:\n'
		if [ "$sh_stage" = create ]; then
			printf '  ブランチ: %s（push 済み）\n  ワークツリー: %s\n' "$stop_branch" "$stop_wt"
		else
			printf '  なし（ブランチもワークツリーも作っていない）\n'
		fi
		[ -z "$stop_local" ] || printf '  手元の候補: %s\n' "$stop_local"
		printf 'MCPで行う手順:\n'
		case "$sh_stage" in
		search)
			if [ "$what" = issue ]; then
				printf '  1. mcp__github__search_pull_requests を query=「repo:%s/%s is:pr is:open %s」で呼ぶ。題・本文に #%s を含むか、元ブランチの名前に %s を含む PRが、この issueに紐づく MR\n' "$sh_owner" "$sh_repo" "$number" "$number" "$number"
				printf '  2. 見つかったら、一覧をユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞く（続けるなら docs/claude/worktree.md の「既存のブランチ」の手順）\n'
				printf '  3. 紐づく PRが無いと確かめたら、同じコマンドに --mcp-checked を付けて打ち直す: %s --mcp-checked\n' "$sh_cmd"
			else
				printf '  1. mcp__github__pull_request_read を method=get owner=%s repo=%s pullNumber=%s で呼び、head.ref（元ブランチ）と head.repo.full_name を読む\n' "$sh_owner" "$sh_repo" "$number"
				printf '  2. head.repo.full_name が %s/%s と違えばフォークの MR。元ブランチがこのリポジトリに無いので作業を始められない。ユーザに伝えて止める\n' "$sh_owner" "$sh_repo"
				printf '  3. 同じなら head.ref のブランチで、docs/claude/worktree.md の「既存のブランチ」の手順どおりにワークツリーを切る\n'
			fi
			;;
		title)
			printf '  1. mcp__github__issue_read を method=get owner=%s repo=%s issue_number=%s で呼び、題を読む\n' "$sh_owner" "$sh_repo" "$number"
			printf '  2. 題から ASCIIの小文字・数字・ハイフンの語を決め、打ち直す: %s --slug <語> --title '"'"'<題>'"'"'\n' "$sh_cmd"
			;;
		*)
			printf '  1. mcp__github__list_pull_requests を owner=%s repo=%s head=%s:%s state=open で呼び、このブランチの開いた PRが無いことを確かめる（あればそれが MR。2 は要らない）\n' "$sh_owner" "$sh_repo" "$sh_owner" "$stop_branch"
			printf '  2. mcp__github__create_pull_request を次の値で呼ぶ\n'
			printf '       owner: %s\n       repo: %s\n       head: %s\n       base: %s\n       title: %s\n       body: Closes #%s\n       draft: true\n' "$sh_owner" "$sh_repo" "$stop_branch" "$stop_target" "$stop_title" "$number"
			printf '  3. 作れたら、同じコマンドを打ち直す: %s（ブランチとワークツリーがあるので、続きから進んでワークツリーのパスを出す）\n' "$sh_cmd"
			;;
		esac
	else
		case "$sh_stage" in
		search) printf 'ccnavi-start: GitLabのAPIに届かず、%s #%s に紐づく既存の MRを確かめられなかった（%s）。既存の MRを見落として重複を作らないため、何も作らずに止めた。\n' "$what" "$number" "$sh_why" ;;
		title) printf 'ccnavi-start: GitLabのAPIに届かず、issue #%s の題を読めなかった（%s）。slugを決められないので、何も作らずに止めた。\n' "$number" "$sh_why" ;;
		*) printf 'ccnavi-start: GitLabのAPIに届かず、マージリクエストを作れなかった（%s）。\n' "$sh_why" ;;
		esac
		printf 'ここまでに出来たこと:\n'
		if [ "$sh_stage" = create ]; then
			printf '  ブランチ: %s（push 済み）\n  ワークツリー: %s\n' "$stop_branch" "$stop_wt"
		else
			printf '  なし（ブランチもワークツリーも作っていない）\n'
		fi
		[ -z "$stop_local" ] || printf '  手元の候補: %s\n' "$stop_local"
		printf '手で行う手順（GitLab には MCPの案内が無い）:\n'
		printf '  1. glab を %s に認証するか、GITLAB_TOKEN を置く（ユーザに頼む）。届くようになったら同じコマンドを打ち直す: %s\n' "${ccnavi_h_host:-?}" "$sh_cmd"
		case "$sh_stage" in
		create)
			printf '  2. 打ち直せないなら、ブラウザで %s の MRを手で作り、打ち直す。ソース: %s、ターゲット: %s、題: Draft: %s、説明: Closes #%s\n' "$ccnavi_h_path" "$stop_branch" "$stop_target" "$stop_title" "$number"
			;;
		search)
			printf '  2. 打ち直せないなら、ブラウザで %s の MRの一覧から、%s #%s に紐づく MRがあるか確かめ、無ければ --mcp-checked を付けて打ち直す\n' "$ccnavi_h_path" "$what" "$number"
			;;
		title)
			printf '  2. 打ち直せないなら、ブラウザで issue #%s の題を読み、%s --slug <語> --title '"'"'<題>'"'"' と打つ\n' "$number" "$sh_cmd"
			;;
		esac
	fi
	exit "$EXIT_HOST"
}

# ---- 統合先。worktree.md の決め方（CCNAVI_INTEGRATION_BRANCH → 取り込み状態 → origin/HEAD → origin/main・master）。
integration=$(ccnavi_integration "$top" "$root") || integration=""
[ -n "$integration" ] ||
	fail no-integration "統合先のブランチを決められません（CCNAVI_INTEGRATION_BRANCH も origin/HEAD も origin/main・master も無い）。CCNAVI_INTEGRATION_BRANCH に統合先の名前を渡してください。"

# ---- 小道具
ref_exists() {
	git rev-parse --verify --quiet "$1^{commit}" >/dev/null 2>&1
}

# ブランチをチェックアウトしているワークツリーのパス。無ければ空
worktree_of() {
	git worktree list --porcelain 2>/dev/null | awk -v want="refs/heads/$1" '
		/^worktree / { p = substr($0, 10) }
		/^branch / && substr($0, 8) == want { print p; exit }'
}

# ブランチの在りか。local ref があれば local、無くて origin にあれば origin、どちらも無ければ空
branch_ref() {
	if ref_exists "refs/heads/$1"; then
		printf '%s' "refs/heads/$1"
	elif ref_exists "refs/remotes/origin/$1"; then
		printf '%s' "refs/remotes/origin/$1"
	fi
}

init_subject="chore: #${number} の作業を開始する"

# ---- 作る・続ける段（どれも、出来ていれば飛ばす）

# ensure_worktree <ブランチ> [<起点> 新規のとき]。出来たワークツリーのパスを wt に入れる。
wt=""
ensure_worktree() {
	ew_branch="$1"
	ew_start="${2:-}"
	wt=$(worktree_of "$ew_branch")
	if [ -n "$wt" ]; then
		log_info ワークツリーがある -- "branch=$ew_branch"
		return 0
	fi
	case "$ew_branch" in
	*/*) ew_id=$(printf '%s' "$ew_branch" | tr '/' '-') ;;
	*) ew_id="$ew_branch" ;;
	esac
	ew_dest="$root/.claude/worktrees/$ew_id"
	if [ -e "$ew_dest" ]; then
		# 識別子のワークツリーが既にある。/ を含むブランチの切りかけなら、switch から続ける。
		ew_cur=$(git -C "$ew_dest" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
		if [ "$ew_cur" = "$ew_id" ] && [ "$ew_id" != "$ew_branch" ]; then
			wt="$ew_dest"
			switch_into "$ew_branch"
			return 0
		fi
		fail worktree-busy "ワークツリーの行き先 $ew_dest が既にあり、ブランチ ${ew_branch}のものではありません（居るブランチ: ${ew_cur:-?}）。中を確かめ、要らなければ片付けてから打ち直してください。"
	fi
	if [ -n "$ew_start" ]; then
		cgit worktree add "$ew_dest" -b "$ew_branch" --no-track "$ew_start" ||
			fail worktree-failed "ワークツリーを切れなかった（上の出力を見てください）。"
		wt="$ew_dest"
		log_info ワークツリーを切った -- "branch=$ew_branch"
		return 0
	fi
	# 既にあるブランチ。手元に無ければ originから取る
	if ! ref_exists "refs/heads/$ew_branch"; then
		cgit fetch origin "$ew_branch" ||
			fail fetch-failed "ブランチ $ew_branch を originから取れなかった（上の出力を見てください）。"
		ref_exists "refs/remotes/origin/$ew_branch" ||
			fail branch-missing "ブランチ $ew_branch が手元にも origin にも無い。ホストにだけある MRの元ブランチ（消された後など）かもしれません。"
	fi
	case "$ew_branch" in
	*/*)
		# 識別子は / を含まない（worktree.md）。識別子のブランチで切り、switchでそのブランチへ移る
		if ref_exists "refs/heads/$ew_id"; then
			fail identifier-exists "識別子のブランチ $ew_id が手元に既にある。要らなければ消してから、使うなら先にそのワークツリーを確かめてから打ち直してください。"
		fi
		if ref_exists "refs/heads/$ew_branch"; then
			ew_from="refs/heads/$ew_branch"
		else
			ew_from="origin/$ew_branch"
		fi
		cgit worktree add "$ew_dest" -b "$ew_id" --no-track "$ew_from" ||
			fail worktree-failed "ワークツリーを切れなかった（上の出力を見てください）。"
		wt="$ew_dest"
		switch_into "$ew_branch"
		;;
	*)
		cgit worktree add "$ew_dest" "$ew_branch" ||
			fail worktree-failed "ワークツリーを切れなかった（上の出力を見てください）。"
		wt="$ew_dest"
		;;
	esac
	log_info ワークツリーを切った -- "branch=$ew_branch"
}

# ワークツリー（wt）で / を含むブランチへ移る。親チケットのワークツリーなら ccnavi-git.sh が拒否するので案内して止める
switch_into() {
	si_branch="$1"
	si_rc=0
	(cd "$wt" && cgit switch "$si_branch") || si_rc=$?
	if [ "$si_rc" -ne 0 ]; then
		fail switch-failed "ワークツリー $wt は作ったが、ブランチ $si_branch へ移れなかった（上の出力を見てください）。同じ名前のチケット（親）が識別子で承認待ち・承認済みだと移れない。docs/claude/worktree.md の「既にある / を含むブランチ」の手順に従ってください。"
	fi
	switched="$si_branch"
}
switched=""

# ensure_initial_commit <ブランチ>。統合先より進んでいなければ、空の初期コミットを積む（wt で）
ensure_initial_commit() {
	ahead=$(git -C "$wt" rev-list --count "origin/$integration..HEAD" 2>/dev/null || echo 0)
	if [ "$ahead" -ge 1 ]; then
		log_info 初期コミットは飛ばす -- "ahead=$ahead"
		return 0
	fi
	(cd "$wt" && cgit commit --allow-empty -m "$init_subject") ||
		fail commit-failed "初期コミットを積めなかった（上の出力を見てください。commit --allow-empty が許可リストで通らないときは、ユーザに許可リストへの追加を頼んでください）。"
	log_info 初期コミットを積んだ
}

# ensure_push <ブランチ>。origin に同じ先頭があれば飛ばす
ensure_push() {
	head_sha=$(git -C "$wt" rev-parse HEAD 2>/dev/null || :)
	remote_sha=$(git -C "$wt" rev-parse --verify --quiet "refs/remotes/origin/$1" 2>/dev/null || :)
	if [ -n "$head_sha" ] && [ "$head_sha" = "$remote_sha" ]; then
		log_info pushは飛ばす -- "branch=$1"
		return 0
	fi
	(cd "$wt" && cgit push -u origin "$1") ||
		fail push-failed "ブランチ $1 を pushできなかった（上の出力を見てください。拒否されたら、そこに出ている案内に従ってください）。"
	log_info push した -- "branch=$1"
}

# ensure_mr <ブランチ> <題>。MRの URLを mr_url に入れる。ホストに届かなければ案内して止まる
mr_url=""
ensure_mr() {
	em_branch="$1"
	em_title="$2"
	stop_branch="$em_branch"
	stop_target="$integration"
	stop_title="$em_title"
	stop_wt="$wt"
	em_rc=0
	host_open || em_rc=$?
	if [ "$em_rc" -eq 1 ]; then
		fail host-unreadable "MRを作れません（${host_why}）。origin を確かめてください。"
	elif [ "$em_rc" -eq 2 ]; then
		stop_host create "$host_why"
	fi
	em_mr=""
	em_found_rc=0
	em_mr=$(find_mr "$em_branch") || em_found_rc=$?
	[ "$em_found_rc" -eq 0 ] || stop_host create "$(api_failed_reason)"
	if [ -n "$em_mr" ]; then
		mr_url=$(printf '%s' "$em_mr" | jq -r '.url')
		log_info MRは飛ばす -- "branch=$em_branch"
		return 0
	fi
	em_made_rc=0
	em_made=$(create_mr "$em_branch" "$integration" "$em_title" "Closes #$number") || em_made_rc=$?
	[ "$em_made_rc" -eq 0 ] || stop_host create "$(api_failed_reason)"
	mr_url=$(printf '%s' "$em_made" | jq -r '.url')
	log_info MRを作った -- "branch=$em_branch"
}

# 結果を 1 項目 1 行で出す。<ブランチ> <MRの URL か空>
report() {
	printf 'ワークツリー: %s\n' "$wt"
	printf 'ブランチ: %s\n' "$1"
	printf 'MR: %s\n' "${2:--}"
	if [ -n "$switched" ]; then
		printf '案内: 名前に / を含むブランチなので、識別子 %s のブランチで切ってから switchで %s へ移した。識別子のブランチは手元に残る（要らなければユーザが消す）\n' "$(printf '%s' "$1" | tr '/' '-')" "$1"
	fi
}

# 新規作成の MRの題。--title、無ければ issueの題、読めなければ「#<番号> <slug>」
make_title() {
	if [ -n "$opt_title" ]; then
		printf '%s' "$opt_title"
	elif [ -n "${issue_title_read:-}" ]; then
		printf '%s' "$issue_title_read"
	else
		printf '#%s %s' "$number" "$1"
	fi
}

# ---- 1. 既存の候補
if ! sh "$scripts/ccnavi-branches.sh" "--$what" "$number" --json >"$cand_json"; then
	fail branches-failed "ccnavi-branches.sh が候補を出せなかった（上の文面を見てください）。"
fi
jq -e . "$cand_json" >/dev/null 2>&1 || fail branches-bad-json "ccnavi-branches.sh の出力を読めなかった。"
checked=$(jq -r '.host.checked' "$cand_json")
host_reason=$(jq -r '.host.reason // ""' "$cand_json")
total=$(jq '.candidates | length' "$cand_json")
log_debug 候補を読んだ -- "checked=$checked" "total=$total"

# 一覧を出す（ccnavi-branches.sh の人が読む形）
show_list() {
	sh "$scripts/ccnavi-branches.sh" "--$what" "$number" || :
}

# ホストを見ていない（--mcp-checked なら MCPで確かめ済みとして、手元の候補だけで進める）
unchecked=""
if [ "$checked" != true ]; then
	if [ -n "$mcp_checked" ]; then
		log_info MCPで確かめ済みとして進める
	else
		unchecked=1
	fi
fi

if [ "$total" -gt 1 ]; then
	show_list
	printf 'ccnavi-start: 候補が %s 件ある。何も作らずに止めた。ユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞いて、返事を待ってください。\n' "$total"
	log_info 候補が複数 -- "total=$total"
	exit "$EXIT_MULTI"
fi

if [ "$total" -eq 0 ]; then
	if [ "$what" = mr ]; then
		if [ -n "$unchecked" ]; then
			host_open || :
			stop_host search "$host_reason"
		fi
		fail mr-not-found "MR #${number} の元ブランチを読めなかった。番号を確かめてください。"
	fi
	if [ -n "$unchecked" ]; then
		# 既存の MRを見落として重複を作らないため、新規作成に進まず止める
		host_rc=0
		host_open || host_rc=$?
		if [ "$host_rc" -eq 1 ]; then
			fail host-unreadable "ホストを見られず（${host_reason}）、origin も読めない（${host_why}）。既存の MRを見落として重複を作らないため、何も作らずに止めた。"
		fi
		stop_host search "${host_reason:-$host_why}"
	fi
	# ---- 2. 新規作成（--issue）
	host_rc=0
	host_open || host_rc=$?
	if [ "$host_rc" -eq 1 ]; then
		fail host-unreadable "MRを作れません（${host_why}）。origin を確かめてください。"
	fi
	issue_rc=0
	if [ "$host_rc" -eq 0 ] && [ -z "$opt_title" ]; then
		issue_title_get || issue_rc=$?
		case "$issue_rc" in
		2) fail issue-missing "issue #${number} が見つからない、または読む権限が無い（${issue_why}）。番号を確かめてください。" 2 ;;
		3) fail issue-is-pr "#${number} は issue ではなく PRです。MRの番号なら --mr ${number} を使ってください。" 2 ;;
		esac
	fi
	slug="$opt_slug"
	[ -n "$slug" ] || slug=$(slugify "${opt_title:-$issue_title_read}")
	if [ -z "$slug" ]; then
		if [ -z "$opt_title" ] && [ -z "$issue_title_read" ]; then
			if [ "$host_rc" -eq 2 ]; then
				stop_host title "$host_why"
			fi
			if [ "$issue_rc" -eq 1 ]; then
				stop_host title "$(api_failed_reason)"
			fi
		fi
		fail no-slug "issueの題から slugを作れない（ASCIIの語が無い）。--slug <語> を付けて打ち直してください。" 2
	fi
	new_branch="${opt_prefix}-${number}-${slug}"
	ccnavi_is_branch "$new_branch" || fail bad-branch "ブランチ名 ${new_branch} は使えない書き方です（--prefix・--slugを見直してください）。" 2
	new_title=$(make_title "$slug")
	new_wt="$root/.claude/worktrees/$new_branch"
	if [ -n "$dry" ]; then
		printf 'dry-run: 何も作らない。作る予定は次のとおり。\n'
		printf 'ブランチ: %s（起点 origin/%s）\n' "$new_branch" "$integration"
		printf 'ワークツリー: %s\n' "$new_wt"
		printf 'MRの題: %s（Draft。本文: Closes #%s）\n' "$new_title" "$number"
		[ "$host_rc" -eq 0 ] || printf '案内: ホストに届かず、題は仮のもの（%s）\n' "$host_why"
		exit 0
	fi
	cgit fetch origin "$integration" || fail fetch-failed "統合先 ${integration} を originから取れなかった（上の出力を見てください）。"
	ref_exists "refs/remotes/origin/$integration" || fail no-origin-integration "origin/${integration} が無い。統合先の名前を確かめてください。"
	ensure_worktree "$new_branch" "origin/$integration"
	ensure_initial_commit
	ensure_push "$new_branch"
	ensure_mr "$new_branch" "$new_title"
	report "$new_branch" "$mr_url"
	log_info 完了 -- "branch=$new_branch"
	exit 0
fi

# ---- 3. 候補がちょうど 1 件
cb=$(jq -r '.candidates[0].branch' "$cand_json")
ccnavi_is_branch "$cb" || fail bad-candidate "候補のブランチ名（${cb}）が使えない書き方です。ユーザに確かめてください。"
c_local=$(jq -r '.candidates[0].local' "$cand_json")
c_origin=$(jq -r '.candidates[0].origin' "$cand_json")
c_mrs=$(jq '.candidates[0].mrs | length' "$cand_json")
c_fork=$(jq '[.candidates[0].mrs[] | select(.fork == true)] | length' "$cand_json")
c_url=$(jq -r '[.candidates[0].mrs[]?.url][0] // ""' "$cand_json")
c_mrnum=$(jq -r '[.candidates[0].mrs[]?.number][0] // ""' "$cand_json")
c_state=$(jq -r '[.candidates[0].mrs[]?.state][0] // ""' "$cand_json")

if [ "$c_local" != true ] && [ "$c_origin" != true ] && [ "$c_mrs" -eq 0 ]; then
	# どこにも実体が無い（チケットの提案だけ）。ユーザが決める
	show_list
	printf 'ccnavi-start: 候補 %s は、手元にも origin にも MR にも実体が無い（チケットの提案だけ）。何も作らずに止めた。ユーザに確かめてください。\n' "$cb"
	exit "$EXIT_MULTI"
fi
if [ "$c_local" != true ] && [ "$c_origin" != true ] && [ "$c_fork" -gt 0 ] && [ "$c_fork" -eq "$c_mrs" ]; then
	fail fork-mr "MR !${c_mrnum} はフォークから出ている。元ブランチ ${cb} はこのリポジトリに無いので、ここでは作業を始められません。フォーク側のブランチで続けるか、ユーザに相談してください。"
fi
[ "$c_mrs" -eq 0 ] || [ "$c_state" = open ] || [ "$c_state" = opened ] ||
	printf 'ccnavi-start: 案内: MR !%s は %s。元ブランチ %s で続ける\n' "$c_mrnum" "$c_state" "$cb"
[ -z "$unchecked" ] || printf 'ccnavi-start: 案内: ホストは見ていない（%s）。手元の候補 %s だけで続ける。既存の MRがあるかは確かめていない\n' "$host_reason" "$cb"

# 作りかけ（ccnavi-start.sh が途中で落ちた跡）か。ホストを見ていて MRが無く、初期コミット（init_subject）だけ
# 積んであり、名前に / を含まないなら、残りの段（push・MR）を続ける。
# 統合先より進んでいない（c_ahead=0）ブランチは作りかけと見なさない。取り込み済みで消し忘れた古いブランチと区別できず、
# 続けると空コミット・push・新しい Draft MRを作ってしまう。一覧を出してユーザに聞く
resume=""
if [ "$what" = issue ] && [ -z "$unchecked" ] && [ "$c_mrs" -eq 0 ]; then
	case "$cb" in
	*/*) ;;
	*)
		cref=$(branch_ref "$cb")
		if [ -n "$cref" ] && ref_exists "refs/remotes/origin/$integration"; then
			c_ahead=$(git rev-list --count "origin/$integration..$cref" 2>/dev/null || echo 9)
			c_subject=$(git log -1 --format=%s "$cref" 2>/dev/null || :)
			if [ "$c_ahead" -eq 0 ]; then
				show_list
				printf 'ccnavi-start: 候補 %s は、統合先 %s より進んでおらず、開いた MR も無い（取り込み済みで消し忘れた古いブランチかもしれない）。何も作らずに止めた。ユーザに見せ、このブランチを使うか・新しく切るか・やめるかを聞いて、返事を待ってください。\n' "$cb" "$integration"
				log_info 古いブランチらしい -- "branch=$cb"
				exit "$EXIT_MULTI"
			fi
			if [ "$c_ahead" -eq 1 ] && [ "$c_subject" = "$init_subject" ]; then
				resume=1
			fi
		fi
		;;
	esac
fi

if [ -n "$dry" ]; then
	printf 'dry-run: 何も作らない。既存のブランチ %s で続ける予定。\n' "$cb"
	existing=$(worktree_of "$cb")
	if [ -n "$existing" ]; then
		printf 'ワークツリー: %s\n' "$existing"
	else
		printf 'ワークツリー: %s（無いので作る）\n' "$root/.claude/worktrees/$(printf '%s' "$cb" | tr '/' '-')"
	fi
	printf 'ブランチ: %s\n' "$cb"
	if [ -n "$resume" ]; then
		printf '作りかけなので、残りの段（初期コミット・push・Draft MR）も続ける予定。MRの題: %s\n' "$(make_title "$cb")"
	else
		printf 'MR: %s\n' "${c_url:--}"
	fi
	exit 0
fi

if [ -n "$resume" ]; then
	# 作りかけ。ワークツリーを出して、残りの段を続ける
	ensure_worktree "$cb"
	host_rc=0
	host_open || host_rc=$?
	if [ "$host_rc" -eq 0 ] && [ -z "$opt_title" ]; then
		issue_title_get || :
	fi
	ensure_initial_commit
	ensure_push "$cb"
	ensure_mr "$cb" "$(make_title "$cb")"
	report "$cb" "$mr_url"
	log_info 作りかけを完了 -- "branch=$cb"
	exit 0
fi

stop_branch="$cb"
ensure_worktree "$cb"
report "$cb" "$c_url"
log_info 続ける -- "branch=$cb"
exit 0

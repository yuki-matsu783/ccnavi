#!/bin/sh
# ccnavi-start issue・MR を指定して作業を頼まれたとき、既存のものを確かめ、無ければ Draft MR・ワークツリー・
# feature ブランチを先に作る。
#
#   sh .ccnavi/scripts/ccnavi-start.sh --issue <番号> [--slug <語>] [--prefix <語>] [--title <題>] [--dry-run] [--mcp-checked]
#   sh .ccnavi/scripts/ccnavi-start.sh --mr <番号> [--issue <値>] [--dry-run]
#   sh .ccnavi/scripts/ccnavi-start.sh --issue <値> --mr <番号> --branch <元ブランチ> --mcp-checked
#   sh .ccnavi/scripts/ccnavi-start.sh --check
#
# チケット作業は issue と MR のセットで進める。開始操作（--check・--help・--dry-run を除く）が終了コード 0 で
# 終わったら、採用した MR と、その MR が閉じる issue と、元ブランチが確定している。
#
# 流れ（.ccnavi/docs/worktree-start.md）:
#   1. ccnavi-branches.sh --json で既存の候補（手元＋ホスト）を探す
#   2. 候補が 1 件ならそのブランチで続ける（ワークツリーが無ければ .claude/worktrees/<識別子> に出す。識別子は
#      ブランチ名の / を - に置き換えたもの。識別子のブランチは作らず、ブランチを直接チェックアウトする）。
#      既存の MR を採用するときは、MR が open・フォークでない・マージ先が統合先・ブランチ名が保護されていない・
#      識別子が使える・同じ識別子になる別のブランチが無い・同じブランチ名のマージ済み MR が無い、を確かめ、
#      引っかかったら止める。続けるときは毎回 origin から取って早送りし、最後に ccnavi-sync.sh <識別子> を打つ。
#      複数なら一覧を出して終わる（ユーザが選ぶ）。0 件で --issue なら新しく作る
#   3. 新規作成: <先頭の語>-<番号>-<slug> のブランチを統合先から切り、ワークツリー・空の初期コミット・push・Draft MR。
#      slug は Chrome 拡張の「始める」と同じ作り方（実行ファイルの `worktree issue-id`。`ticket_ids.issue_identifier`）で、
#      作る前に既存の MR を採用するときと同じ識別子の確認（使える・同じ識別子の別ブランチが無い・同名のマージ済み MR が
#      無い）をする。作ってから後段で詰まると、打ち直しても採用の確認で止まり、手で消すまで戻れないため
#   途中で失敗しても、打ち直せば完了済みの処理をスキップし、未完了の処理から再開する（ブランチ・push・MR の有無を確かめる）。
#
# git は ccnavi-git.sh を通す（読むだけの確認は git を直接呼ぶ。他のスクリプトと同じ）。ホストへの繋ぎ方は
# ccnavi-common-host.sh（ccnavi-review.sh・ccnavi-branches.sh と同じ）。
#
# --check はチケット作業の入口の事前確認（ccnavi_preflight。ホストの API と git のリモートが使える状態か）。
#
# 終了コード: 0 完了 / 1 前提の未充足 / 2 引数か環境の誤り / 3 候補が複数か、MR の無い候補か、MR が閉じる issue が複数
#             （何も作らず一覧を出した。ユーザが選ぶ） /
#             4 ホストに届かず、その段を MCP などで代行してほしい（案内を標準出力に出した。代行したら打ち直す）。
#             --check では「確かめられなかった項目がある」

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
sh .ccnavi/scripts/ccnavi-start.sh (--issue <値> | --mr <番号> [--issue <値>]) [--slug <語>] [--prefix <語>] [--title <題>] [--dry-run] [--mcp-checked] [--branch <元ブランチ>]
sh .ccnavi/scripts/ccnavi-start.sh --check

  チケット作業は issue と MR のセット。issue・MR に紐づくブランチを探し（ccnavi-branches.sh）、候補に応じて進める。
  終了コード 0 で終わったら、採用した MR と、その MR が閉じる issue（リポジトリと番号）と、元ブランチが確定している
  （出力の「必須」の行の issue: と mr: を提案の frontmatter に書く）。--dry-run は予定を示すだけで、開始の完了ではない。
  --check       チケット作業の入口の事前確認。ホストの API（認証・リポジトリの有無・書き込みの権限）と git のリモートが
                使える状態かを確かめる。何も作らない。単独で使う（ほかの引数とは一緒に渡せない）。
                終了コード 0 使える / 1 不備がある / 4 確かめられなかった項目がある
  候補が 1 件で、開いた MR がある
                そのブランチで続ける。ワークツリーが無ければ .claude/worktrees/<識別子> に、ブランチを直接
                チェックアウトして出す（識別子はブランチ名の / を - に置き換えたもの）。
                毎回 origin から取って早送りし、最後に ccnavi-sync.sh <識別子> を打つ。既存の MR を採用するときは
                MR が open・フォークでない・マージ先が統合先・ブランチ名が保護されていない・識別子が使える
                （58 文字以内で、末尾が -2 桁でない）・同じ識別子になる別のブランチが無い（origin の全ブランチで確認。
                英字の大文字小文字は区別しない）・
                同じブランチ名のマージ済み MR が無い、を確かめ、引っかかったら止める。その MR が閉じる issue も確かめる
                （--issue があればその issue が含まれること。無ければ 1 件に決まること。0 件なら止め、2 件以上なら一覧を出して
                終了コード 3）
  候補が 1 件で、MR が無い
                作りかけ（初期コミットだけ）なら、MR を作って続ける。それ以外は成功させず、一覧を出して終了コード 3
                （ユーザに MR（説明欄に Closes #<番号>）を作ってもらって打ち直すか、新しく切るか、やめる）
  候補が複数    何も作成せずに一覧を出して終わる（終了コード 3）。ユーザが選ぶ
  候補が 0 件   --issue なら新しく作る。ブランチ <先頭の語>-<番号>-<slug> を統合先から切り、ワークツリー・空の初期コミット・
                push・Draft MR（本文に Closes #<番号>）まで。作る前に、識別子が使えること（58 文字以内・末尾が -2 桁でない）・
                同じ識別子になる別のブランチが無いこと・同じ名前のマージ済み MR が無いことを確かめる。
                --mr は元ブランチが候補なので、無ければ止める
  --mr          フォークから出た MR は元ブランチがこのリポジトリにないので、理由を出して止める。--issue と一緒に渡せる
                （順番は問わない）。<値> は <番号> か <owner/repo>#<番号>
  --slug <語>   ブランチ名の slug（小文字・数字・ハイフン）。省くと issue の題から作る（Chrome 拡張の「始める」と同じ
                作り方。ccnavi の実行ファイルに聞く）
  --prefix <語> 先頭の語（既定 feature）
  --title <題>  MR の題。省くと issue の題、読めなければ「#<番号> <slug>」
  --dry-run     何も作らず、作る予定の内容だけ出す（リモートは読むだけ）
  --mcp-checked ホストの API に届かず、エージェントが別の手段（MCP など）で確かめたあとに付ける
  --branch <元ブランチ>
                --mcp-checked と一緒のときだけ使える。ホストに届かないとき、候補の探索の代わりにこのブランチを使う
                （--issue・--mr・--branch の 3 つがそろえば、ホストを見ずに続ける）。ホストに届くときは普通に確かめ、
                MR の元ブランチが違えば終了コード 1

  ホストを見られないときは、既存の MR を見落として重複を作らないよう新規作成に進まず止める（終了コード 4）。
  GitHub の API に届かないとき（Claude remote など）は、MCP で代行する手順を標準出力に出して終了コード 4 で止まる。
  代行したあと、案内のコマンドを再実行すると、完了済みの処理をスキップし、未完了の処理から再開します。
  冪等。cwd のリポジトリ（ワークスペース・projects/<名前>）を対象にする。

終了コード: 0 完了 / 1 前提の未充足 / 2 引数か環境の誤り / 3 候補が複数・MR の無い候補・閉じる issue が複数 / 4 ホストに届かず代行が必要です
USAGE
}

what=""
number=""
issue_arg=""
mr_arg=""
branch_arg=""
check=""
opt_slug=""
opt_prefix="feature"
opt_title=""
dry=""
mcp_checked=""
orig_count="$#"
# 打ち直しの案内に使う。引数ごとに ' で包み（中の ' は '\''）、そのまま打てば同じ引数になるようにする。
sh_quote() {
	printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}
orig_args=""
# --dry-run を外した同じ引数。dry-run の「次:」で、実際に作るときのコマンドとして出す
real_args=""
for arg in "$@"; do
	q=$(sh_quote "$arg")
	orig_args="$orig_args $q"
	[ "$arg" = --dry-run ] || real_args="$real_args $q"
done
orig_args="${orig_args# }"
real_args="${real_args# }"
while [ "$#" -gt 0 ]; do
	case "$1" in
	--issue | --mr | --branch)
		[ "$#" -ge 2 ] || fail no-number "$1 の後ろに値を渡してください。" 2
		case "$1" in
		--issue)
			[ -z "$issue_arg" ] || fail two-issue "--issue は 1 つだけ渡してください。" 2
			issue_arg="$2"
			;;
		--mr)
			[ -z "$mr_arg" ] || fail two-mr "--mr は 1 つだけ渡してください。" 2
			mr_arg="$2"
			;;
		--branch)
			[ -z "$branch_arg" ] || fail two-branch "--branch は 1 つだけ渡してください。" 2
			branch_arg="$2"
			;;
		esac
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
	--check)
		check=1
		shift
		;;
	-h | --help | help)
		usage
		exit 0
		;;
	*)
		usage >&2
		fail unknown-arg "未対応の引数です（$1）。" 2
		;;
	esac
done
if [ -n "$check" ]; then
	[ "$orig_count" -eq 1 ] || fail check-alone "--check は単独で使います（ほかの引数とは一緒に渡せません）。" 2
else
	[ -n "$issue_arg" ] || [ -n "$mr_arg" ] || {
		usage >&2
		fail no-kind "--issue <番号> か --mr <番号> を渡してください（チケット作業は issue と MR のセットです）。" 2
	}
	[ -z "$branch_arg" ] || [ -n "$mcp_checked" ] || fail branch-alone "--branch は --mcp-checked と一緒のときだけ渡せます。" 2
fi

# issue の値は <番号> か <owner/repo>#<番号>。issue_n に番号、issue_r にリポジトリ（番号だけなら空）を入れる
issue_n=""
issue_r=""
if [ -n "$issue_arg" ]; then
	case "$issue_arg" in
	*#*)
		issue_r="${issue_arg%#*}"
		issue_n="${issue_arg##*#}"
		printf '%s' "$issue_r" | grep -Eq '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$' ||
			fail bad-issue "--issue の値が読めません（${issue_arg}）。<番号> か <owner/repo>#<番号> で渡してください。" 2
		;;
	*) issue_n="$issue_arg" ;;
	esac
	case "$issue_n" in
	'' | 0* | *[!0-9]*) fail bad-number "issue の番号は 0 で始まらない正の整数で渡してください（${issue_arg}）。" 2 ;;
	esac
	[ "${#issue_n}" -le 9 ] || fail bad-number "issue の番号が長すぎます（${issue_arg}）。" 2
fi
if [ -n "$mr_arg" ]; then
	case "$mr_arg" in
	'' | 0* | *[!0-9]*) fail bad-number "MR の番号は 0 で始まらない正の整数で渡してください（${mr_arg}）。" 2 ;;
	esac
	[ "${#mr_arg}" -le 9 ] || fail bad-number "MR の番号が長すぎます（${mr_arg}）。" 2
	what=mr
	number="$mr_arg"
elif [ -n "$issue_arg" ]; then
	what=issue
	number="$issue_n"
fi

# 語（slug・先頭の語）は小文字・数字・ハイフンだけ。
is_word() {
	printf '%s' "$1" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$'
}
if [ -n "$opt_slug" ]; then
	is_word "$opt_slug" || fail bad-slug "--slug は小文字・数字・ハイフンだけで書いてください（${opt_slug}）。" 2
fi
is_word "$opt_prefix" || fail bad-prefix "--prefix は小文字・数字・ハイフンだけで書いてください（${opt_prefix}）。" 2
if [ "$what" = mr ] && { [ -n "$opt_slug" ] || [ -n "$opt_title" ]; }; then
	fail mr-with-new "--mr には --slug と --title を付けられません（既存の MR の元ブランチで続けるだけです）。" 2
fi

[ -n "$check" ] || command -v jq >/dev/null 2>&1 || fail no-jq "jqが見つかりません。候補の JSON を解析できません。" 2

root=$(ccnavi_workspace) ||
	fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
ccnavi_log_root="$root"
here="$(pwd -W 2>/dev/null || pwd)"
top=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -n "$top" ] ||
	fail not-git "cwd（${here}）が git のリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で実行してください。"
state="$root/logs/state" # 固定
mkdir -p "$state" 2>/dev/null || fail no-state "state の置き場（${state}）を作れません。" 2
scripts="$root/.ccnavi/scripts"
host_err="$state/start-host-err-$$"
cand_json="$state/start-candidates-$$.json"
heads_file="$state/start-heads-$$"
trap 'rm -f "$host_err" "$host_err.status" "$cand_json" "$heads_file" "$heads_file.raw" "$heads_file.err"' EXIT
log_info 始めた -- "kind=${what:-check}" "number=$number" "dry=${dry:-no}"

# origin のパス（owner/repo）。issue の値の正規化に使う。読めなければ空（host_open が理由を言う）
own_path=""
host_unseen=""
own_url=$(git remote get-url origin 2>/dev/null || :)
if [ -n "$own_url" ] && ccnavi_host_parse "$own_url" >/dev/null 2>&1; then
	own_path="$ccnavi_h_path"
fi
lower() {
	printf '%s' "$1" | tr 'A-Z' 'a-z'
}
# issue の正規化した形 <owner/repo>#<番号>（小文字。比べる用）。<リポジトリか空> <番号>
issue_key() {
	printf '%s#%s' "$(lower "${1:-$own_path}")" "$2"
}
# frontmatter と案内に書く形。MR と同じリポジトリなら <番号>、違えば <owner/repo>#<番号>。<リポジトリか空> <番号>
issue_show() {
	if [ -z "${1:-}" ] || [ "$(lower "$1")" = "$(lower "$own_path")" ]; then
		printf '%s' "$2"
	else
		printf '%s#%s' "$1" "$2"
	fi
}
if [ "$what" = issue ] && [ -n "$issue_r" ] && [ -n "$own_path" ] && [ "$(lower "$issue_r")" != "$(lower "$own_path")" ]; then
	fail issue-other-repo "別のリポジトリの issue（${issue_arg}）から MR を新しく作る道はありません。その issue を閉じる MR（説明欄に Closes ${issue_arg}）をユーザに作ってもらい、--mr <番号> と一緒に渡してください。" 2
fi

# ---- --check（手で打つ事前確認。標準出力。何も作らない）
if [ -n "$check" ]; then
	ccnavi_preflight "$top"
	log_info 事前確認 -- "result=$ccnavi_pf_result" "fix=${ccnavi_pf_fix:-none}" "auth=${ccnavi_pf_auth_source:-none}"
	pc_name=$(ccnavi_project "$top" "$root")
	printf '対象リポジトリ: %s（%s）\n' "${ccnavi_h_path:-$top}" "${pc_name:-ワークスペース自身}"
	for pc_item in tools origin integration api access git; do
		ccnavi_pf_line "$pc_item"
		printf '確認: %s\n' "$ccnavi_pf_line_text"
	done
	case "$ccnavi_pf_result" in
	ok) printf '結論: チケット作業を始められる状態です。\n' ;;
	ng) printf '結論: 不備があり、チケット作業を始められません。\n' ;;
	*) printf '結論: 確かめられなかった項目があります（省略は前提の項目が OK でないため確かめていません）。\n' ;;
	esac
	ccnavi_pf_next
	[ -z "$ccnavi_pf_next_text" ] || printf '次: %s\n' "$ccnavi_pf_next_text"
	case "$ccnavi_pf_result" in
	ok) exit 0 ;;
	ng) exit 1 ;;
	*) exit "$EXIT_HOST" ;;
	esac
fi

# ---- ccnavi-git.sh 経由の git。成功は何も出さず、失敗は出力を標準エラーへ出して 1 を返す（cwd で動く）。
cgit() {
	cgit_out=$(sh "$scripts/ccnavi-git.sh" "$@" 2>&1) || {
		printf '%s\n' "$cgit_out" >&2
		return 1
	}
	log_debug git を通した -- "sub=$1"
	return 0
}

# cgit と同じく ccnavi-git.sh 経由。失敗を出さず、出力は cgit_out に残す（呼ぶ側が使い道を決める）。
cgit_quiet() {
	cgit_out=$(sh "$scripts/ccnavi-git.sh" "$@" 2>&1) || return 1
	return 0
}

# origin の全ブランチ名を 1 行 1 本で $heads_file に書く（ls-remote）。読めなければ 1。
# 識別子の重なりは、MR の有無によらず origin の全ブランチで見る。
remote_heads() {
	ccnavi_git_timed "${CCNAVI_START_GIT_TIMEOUT:-30}" "$heads_file.err" "$top" ls-remote --heads origin >"$heads_file.raw" || return 1
	sed -n 's#^[0-9a-fA-F]\{1,\}[[:space:]]\{1,\}refs/heads/##p' "$heads_file.raw" | tr -d '\r' >"$heads_file"
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
		host_why="origin がありません。"
		host_rc=1
		return 1
	fi
	if ! ccnavi_host_parse "$origin"; then
		host_why="origin の URL を読めない（$(ccnavi_mask_url "$origin")）${ccnavi_h_parse_why:+。$ccnavi_h_parse_why}"
		host_rc=1
		return 1
	fi
	ccnavi_h_tmp="$state"
	ccnavi_h_on_fail=host_api_failed
	ccnavi_h_max_time="${CCNAVI_START_HOST_TIMEOUT:-30}"
	connect_rc=0
	ccnavi_host_connect || connect_rc=$?
	case "$connect_rc" in
	0) ;;
	3) fail no-jq "jq が見つかりません。ホストからの応答を解析できません。" 2 ;;
	4)
		host_why="$ccnavi_h_cli_name が $ccnavi_h_host で使えず（未導入か未認証）、curl に付ける $ccnavi_h_token_name もありません。"
		host_rc=2
		return 2
		;;
	*)
		host_why="$ccnavi_h_cli_name が $ccnavi_h_host で使えず、curl もありません。"
		host_rc=2
		return 2
		;;
	esac
	# プロジェクトを 1 回読んで、認証・リポジトリの有無・書き込みの権限を確かめる（何かを作る前に）
	ccnavi_host_probe
	case "$ccnavi_h_probe" in
	ok) ;;
	auth | notfound)
		ccnavi_pf_top="$top"
		if [ "$ccnavi_h_probe" = auth ]; then
			ccnavi_pf_fix=auth
			host_fail_why="ホストの API が認証を受け付けない（HTTP 401 か 403）"
		else
			ccnavi_pf_fix=notfound
			host_fail_why="ホストにリポジトリ ${ccnavi_h_path} が見つからない（HTTP 404）"
		fi
		ccnavi_pf_next
		fail host-"$ccnavi_h_probe" "${host_fail_why}。何も作成せずに処理を中断しました。
次: ${ccnavi_pf_next_text}" 1
		;;
	*)
		host_why="ホストの API に届かない、または応答を読めない（probe=${ccnavi_h_probe}）。"
		host_rc=2
		return 2
		;;
	esac
	acc_rc=0
	ccnavi_host_access || acc_rc=$?
	if [ "$acc_rc" -eq 1 ]; then
		ccnavi_pf_top="$top"
		ccnavi_pf_fix=access
		ccnavi_pf_next
		fail access-denied "${ccnavi_h_path} への書き込みの権限が足りない（${ccnavi_h_access_why}）。ワークツリー・ブランチ・push・MR のどれも作らずに中断しました。
次: ${ccnavi_pf_next_text}" 1
	fi
	return 0
}

# API が失敗したら、どの呼び出しかを覚えておく（`$( )` の中から呼ばれるのでファイルに書く）。ホストの返事は書き出さない。
host_api_failed() {
	printf '%s %s' "$1" "$2" >"$host_err"
	# ホストが HTTP のエラーを返したなら、その番号も残す（届いてはいるのか、届かないのかを見分けるため）。
	# curl は「returned error: 404」、gh は「(HTTP 404)」、glab は「404 Not Found」の形
	ccnavi_host_status "$3" >"$host_err.status"
}
ml_why=""
api_failed_reason() {
	what_failed=$(cat "$host_err" 2>/dev/null || :)
	if [ -z "$what_failed" ] && [ -n "$ml_why" ]; then
		printf '%s' "$ml_why"
		return 0
	fi
	printf 'ホストの API が失敗した（%s）' "${what_failed:-?}"
}

# 親のシェルで呼ぶ。結果は issue_title_read（題。1 行、制御文字は空白）と issue_why（失敗の理由）に入れる。
# 戻り値: 0 読めた / 1 API が失敗した（ホストに届いていない） / 2 issue が無い・権限が無い（ホストには届いた） / 3 PR の番号だった
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
			issue_why="権限がありません（HTTP ${it_status}）"
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

# issue の番号と題から、新しい親のブランチ名（= 識別子）を実行ファイルに作らせる（`worktree issue-id`）。
# Chrome 拡張の「始める」と同じ関数（`ticket_ids.issue_identifier`）なので、同じ issue なら同じ名前になる。
# プロジェクト（projects/<名前>）のリポジトリなら、その名前を渡す（slug の頭に付く）。
# 名前を issue_branch に入れる。実行ファイルが無い・答えないなら 1、作れないと言われたら 2（理由は issue_branch_why）
issue_branch=""
issue_branch_why=""
issue_branch_make() {
	issue_branch=""
	issue_branch_why=""
	ib_project=$(ccnavi_project "$top" "$root")
	if ib_bin=$(ccnavi_bin "$root"); then
		ib_rc=0
		ib_out=$("$ib_bin" --root "$root" worktree issue-id "$number" "$opt_prefix" $ib_project --title "$1" 2>"$state/start-issue-id-$$" </dev/null) || ib_rc=$?
	elif [ -f "$root/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
		ib_rc=0
		ib_out=$(cd "$root" && uv run --quiet python -m ccnavi --root "$root" worktree issue-id "$number" "$opt_prefix" $ib_project --title "$1" 2>"$state/start-issue-id-$$" </dev/null) || ib_rc=$?
	else
		return 1
	fi
	issue_branch_why=$(tr -d '\r' <"$state/start-issue-id-$$" 2>/dev/null | head -n 1 | sed 's/^ccnavi: //')
	rm -f "$state/start-issue-id-$$"
	issue_branch=$(printf '%s\n' "$ib_out" | tr -d '\r' | sed -n 's/^id //p' | head -n 1)
	[ -n "$issue_branch" ] && return 0
	[ "$ib_rc" -ne 0 ] && return 2
	return 1
}

# 開いた MR を探す。あれば {number, url}、無ければ空。API が失敗したら 1
find_mr() {
	if [ "$ccnavi_h_kind" = github ]; then
		fm_owner="${ccnavi_h_path%%/*}"
		fm_got=$(ccnavi_host_api GET "repos/$ccnavi_h_path/pulls?state=open&head=$(ccnavi_host_encode "$fm_owner:$1")") || return 1
		printf '%s' "$fm_got" | "$ccnavi_h_jq" '.[0] // empty | {number: .number, url: .html_url}'
	else
		fm_pid=$(ccnavi_host_project_id) || return 1
		fm_rc=0
		fm_got=$(ccnavi_host_pages "projects/$(ccnavi_host_encoded_path)/merge_requests?state=opened&source_branch=$(ccnavi_host_encode "$1")") || fm_rc=$?
		[ "$fm_rc" -eq 0 ] || return 1
		printf '%s' "$fm_got" | "$ccnavi_h_jq" --argjson pid "$fm_pid" \
			'[.[] | select(.source_project_id == $pid)][0] // empty | {number: .iid, url: .web_url}'
	fi
}

# 同じブランチ名のマージ済みの MR を探す。あれば {number, url}、無ければ空。API が失敗したら 1
# 手元の退避（logs/archive/）には頼らず、ホストに聞く。
find_merged_mr() {
	if [ "$ccnavi_h_kind" = github ]; then
		fx_owner="${ccnavi_h_path%%/*}"
		fx_got=$(ccnavi_host_pages "repos/$ccnavi_h_path/pulls?state=closed&head=$(ccnavi_host_encode "$fx_owner:$1")") || return 1
		printf '%s' "$fx_got" | "$ccnavi_h_jq" '[.[] | select(.merged_at != null)][0] // empty | {number: .number, url: .html_url}'
	else
		fx_got=$(ccnavi_host_pages "projects/$(ccnavi_host_encoded_path)/merge_requests?state=merged&source_branch=$(ccnavi_host_encode "$1")") || return 1
		printf '%s' "$fx_got" | "$ccnavi_h_jq" '.[0] // empty | {number: .iid, url: .web_url}'
	fi
}

# Draft の MR を作る。{number, url} を出す。<ブランチ> <統合先> <題> <本文>
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

# その MR が閉じる issue を、正規化した形 <owner/repo>#<番号> で 1 行ずつ標準出力へ。<MR の番号>
# 戻り値: 0 読めた / 1 API の失敗（応答の errors・null を含む） / 2 読み切れない（件数の上限を超えた）。理由は ml_why
mr_linked_issues() {
	ml_mr="$1"
	ml_why=""
	if [ "$ccnavi_h_kind" = github ]; then
		ml_owner="${ccnavi_h_path%%/*}"
		ml_name="${ccnavi_h_path#*/}"
		ml_cursor=null
		ml_page=0
		while :; do
			ml_page=$((ml_page + 1))
			ml_payload=$("$ccnavi_h_jq" -n --arg o "$ml_owner" --arg n "$ml_name" --argjson num "$ml_mr" --argjson c "$ml_cursor" \
				'{query: "query($o:String!,$n:String!,$num:Int!,$c:String){repository(owner:$o,name:$n){pullRequest(number:$num){closingIssuesReferences(first:100,after:$c){nodes{number repository{nameWithOwner}} pageInfo{hasNextPage endCursor}}}}}", variables: {o: $o, n: $n, num: $num, c: $c}}')
			ml_got=$(ccnavi_host_api POST graphql "$ml_payload") || return 1
			if ! printf '%s' "$ml_got" | "$ccnavi_h_jq" -e '(.errors // null) == null and (.data.repository.pullRequest.closingIssuesReferences.nodes | type) == "array"' >/dev/null 2>&1; then
				ml_why="紐づく issue を読めなかった（GraphQL の応答に errors があるか、MR が見つからない）"
				return 1
			fi
			printf '%s' "$ml_got" | "$ccnavi_h_jq" -r '.data.repository.pullRequest.closingIssuesReferences.nodes[] | "\(.repository.nameWithOwner)#\(.number)"'
			if [ "$(printf '%s' "$ml_got" | "$ccnavi_h_jq" -r '.data.repository.pullRequest.closingIssuesReferences.pageInfo.hasNextPage')" != true ]; then
				return 0
			fi
			if [ "$ml_page" -ge 5 ]; then
				ml_why="紐づく issue が多すぎて読み切れない（5 ページを超えた）"
				return 2
			fi
			ml_cursor=$(printf '%s' "$ml_got" | "$ccnavi_h_jq" '.data.repository.pullRequest.closingIssuesReferences.pageInfo.endCursor')
		done
	else
		ml_rc=0
		ml_got=$(ccnavi_host_pages "projects/$(ccnavi_host_encoded_path)/merge_requests/$ml_mr/closes_issues") || ml_rc=$?
		if [ "$ml_rc" -eq 2 ]; then
			ml_why="紐づく issue が多すぎて読み切れない"
			return 2
		fi
		[ "$ml_rc" -eq 0 ] || return 1
		printf '%s' "$ml_got" | "$ccnavi_h_jq" -r --arg p "$ccnavi_h_path" '.[] | (.references.full // "\($p)#\(.iid)")'
	fi
}

# MR が閉じる issue を確かめて、final_issue（frontmatter に書く形）を決める。<MR の番号>
# --issue があれば、その issue が閉じる一覧に含まれること。無ければ 1 件に決まること（0 件は 1、2 件以上は 3）。
final_issue=""
final_mr=""
resolve_issue() {
	ri_mr="$1"
	stop_mr="$ri_mr"
	ri_rc=0
	ri_raw=$(mr_linked_issues "$ri_mr") || ri_rc=$?
	if [ "$ri_rc" -ne 0 ]; then
		stop_host issue-link "$([ "$ri_rc" -eq 2 ] && printf '%s' "${ml_why:-読み切れない}" || api_failed_reason)"
	fi
	# 大文字小文字を区別せず重複を除く（先に出た表記を残す）
	ri_list=$(printf '%s\n' "$ri_raw" | awk 'NF && !seen[tolower($0)]++')
	ri_count=$(printf '%s\n' "$ri_list" | awk 'NF' | wc -l | tr -d ' ')
	if [ -n "$issue_arg" ]; then
		ri_want=$(issue_key "$issue_r" "$issue_n")
		ri_hit=$(printf '%s\n' "$ri_list" | awk -v w="$ri_want" 'tolower($0) == w { print; exit }')
		if [ -z "$ri_hit" ]; then
			ri_shown=$(printf '%s\n' "$ri_list" | awk 'NF' | tr '\n' ' ')
			fail issue-not-linked "MR !${ri_mr} は issue #${issue_n} を閉じるリンクを持たない（参照しているだけ）。この MR が閉じる issue: ${ri_shown:-なし}。説明欄に Closes #${issue_n} を書くようユーザに頼み、打ち直してください。" 1
		fi
		final_issue=$(issue_show "${ri_hit%#*}" "${ri_hit##*#}")
	elif [ "$ri_count" -eq 0 ]; then
		fail issue-none "MR !${ri_mr} が閉じる issue が無い。説明欄に Closes #<番号> を書くようユーザに頼み、打ち直してください。" 1
	elif [ "$ri_count" -gt 1 ]; then
		printf 'ccnavi-start: MR !%s が閉じる issue が %s 件ある。どれで進めるかをユーザに聞いてください。\n' "$ri_mr" "$ri_count"
		printf '%s\n' "$ri_list" | sed 's/^/  - /'
		printf '次: 選んだ issue で --issue <値> を付けて打ち直す: sh .ccnavi/scripts/ccnavi-start.sh %s --issue <値>\n' "$orig_args"
		log_info 閉じる issue が複数 -- "mr=$ri_mr" "count=$ri_count"
		exit "$EXIT_MULTI"
	else
		final_issue=$(issue_show "${ri_list%#*}" "${ri_list##*#}")
	fi
	final_mr="$ri_mr"
	log_info 閉じる issue を決めた -- "mr=$ri_mr"
}

# ---- ホストに届かないときの案内。標準出力に出して終了コード 4 で止まる。
#
# 段は search（既存の MR を確かめる）・title（issue の題を読む）・create（MR を作る）。
# GitHub は MCP（mcp__github__のツール）で代行する手順を出す。GitLab には MCP の案内が無いので、理由と手で打つ手順を出す。
# 使う変数: stop_branch・stop_target・stop_title・stop_wt（段より前に出来たこと）
stop_branch=""
stop_target=""
stop_title=""
stop_wt=""
stop_pushed=""
stop_local=""
stop_mr=""
stop_host() {
	sh_stage="$1"
	sh_why="$2"
	log_info ホストに届かず止めた -- "stage=$sh_stage" "kind=${ccnavi_h_kind:-}"
	if [ -z "${ccnavi_h_kind:-}" ]; then
		fail host-unknown "ホストを読めない（${sh_why}）。origin を確かめてください。"
	fi
	sh_owner="${ccnavi_h_path%%/*}"
	sh_repo="${ccnavi_h_path#*/}"
	# MR を作る段と issue を確かめる段は、MCP で確かめたあとの打ち直しを issue・MR・元ブランチの 3 つそろえた形で案内する
	sh_issue_v="<issue の番号>"
	[ -z "$issue_arg" ] || sh_issue_v="$issue_arg"
	sh_branch_v="<元ブランチ>"
	[ -z "$stop_branch" ] || sh_branch_v=$(sh_quote "$stop_branch")
	case "$sh_stage" in
	create) sh_cmd="sh .ccnavi/scripts/ccnavi-start.sh --issue $(issue_show "" "$number") --mr <作った番号> --branch $sh_branch_v --mcp-checked" ;;
	issue-link) sh_cmd="sh .ccnavi/scripts/ccnavi-start.sh --mr $stop_mr --issue $sh_issue_v --branch $sh_branch_v --mcp-checked" ;;
	mcp-args) sh_cmd="sh .ccnavi/scripts/ccnavi-start.sh --issue ${issue_arg:-<issue の番号>} --mr ${mr_arg:-<MR の番号>} --branch ${branch_arg:-<元ブランチ>} --mcp-checked" ;;
	*) sh_cmd="sh .ccnavi/scripts/ccnavi-start.sh $orig_args" ;;
	esac
	if [ "$sh_stage" = mcp-args ]; then
		sh_missing=""
		[ -n "$issue_arg" ] || sh_missing="$sh_missing --issue"
		[ -n "$mr_arg" ] || sh_missing="$sh_missing --mr"
		[ -n "$branch_arg" ] || sh_missing="$sh_missing --branch"
		printf 'ccnavi-start: ホストを見られない間は、--mcp-checked は --issue・--mr・--branch の 3 つがそろうときだけ使えます（足りない:%s）。何も作成せずに処理を中断しました。\n' "$sh_missing"
		printf '次: MCP などで MR の元ブランチと、その MR が閉じる issue を確かめ、3 つそろえて打ち直す: %s\n' "$sh_cmd"
		exit "$EXIT_HOST"
	fi
	if [ "$ccnavi_h_kind" = github ]; then
		case "$sh_stage" in
		search) printf 'ccnavi-start: GitHub APIに接続できず、%s #%s に紐づく既存の MR を確かめられなかった（%s）。既存の MR を見落として重複を作らないため、何も作成せずに処理を中断しました。MCP（mcp__github__のツール）で代行してください。\n' "$what" "$number" "$sh_why" ;;
		title) printf 'ccnavi-start: GitHub APIに接続できず、issue #%s を読めなかった（%s）。実在を確かめられないので、何も作成せずに処理を中断しました。MCP（mcp__github__のツール）で代行してください。\n' "$number" "$sh_why" ;;
		merged) printf 'ccnavi-start: GitHub APIに接続できず、ブランチ %s と同じ名前のマージ済みの MR があるかを確かめられなかった（%s）。マージ済みのブランチを再利用しないため、何も作成せずに処理を中断しました。MCP（mcp__github__のツール）で代行してください。\n' "$stop_branch" "$sh_why" ;;
	issue-link) printf 'ccnavi-start: MR !%s が閉じる issue を確かめられなかった（%s）。issue と MR のセットが確定しないため、続けずに処理を中断しました。MCP（mcp__github__のツール）で代行してください。\n' "$stop_mr" "$sh_why" ;;
		*) printf 'ccnavi-start: GitHub APIに接続できず、マージリクエストを作れなかった（%s）。MCP（mcp__github__のツール）で代行してください。\n' "$sh_why" ;;
		esac
		printf '完了済みの処理:\n'
		if [ "$sh_stage" = create ]; then
			printf '  ブランチ: %s（push 済み）\n  ワークツリー: %s\n' "$stop_branch" "$stop_wt"
		else
			printf '  なし（ブランチもワークツリーも作っていない）\n'
		fi
		[ -z "$stop_local" ] || printf '  手元の候補: %s\n' "$stop_local"
		printf 'MCP で行う手順:\n'
		case "$sh_stage" in
		issue-link)
			printf '  1. mcp__github__pull_request_read を method=get owner=%s repo=%s pullNumber=%s で呼び、head.ref（元ブランチ）と本文を読む。本文の Closes / Fixes / Resolves #<番号> が、この MR が閉じる issue\n' "$sh_owner" "$sh_repo" "$stop_mr"
			printf '  2. mcp__github__issue_read を method=get owner=%s repo=%s issue_number=<その番号> で呼び、issue が実在することを確かめる（Closes が無い・複数あるときは、ユーザに伝えて止める）\n' "$sh_owner" "$sh_repo"
			printf '  3. 元ブランチと issue が決まったら、打ち直す: %s\n' "$sh_cmd"
			;;
		search)
			if [ "$what" = issue ]; then
				printf '  1. mcp__github__search_pull_requests を query=「repo:%s/%s is:pr is:open %s」で呼ぶ。題・本文に #%s を含むか、元ブランチの名前に %s を含む PR が、この issue に紐づく MR\n' "$sh_owner" "$sh_repo" "$number" "$number" "$number"
				printf '  2. 見つかったら、一覧をユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞く（続けるなら .ccnavi/docs/worktree-start.md の「既にある / を含むブランチ」の手順）\n'
				printf '  3. 紐づく PR が無いと確かめたら、mcp__github__issue_read を method=get owner=%s repo=%s issue_number=%s で呼び、issue が実在することも確かめる（無ければ番号を作らず、ユーザに伝えて止める）\n' "$sh_owner" "$sh_repo" "$number"
				printf '  4. 実在を確かめたら、同じコマンドに --mcp-checked を付けて再実行する: %s --mcp-checked\n' "$sh_cmd"
			else
				printf '  1. mcp__github__pull_request_read を method=get owner=%s repo=%s pullNumber=%s で呼び、head.ref（元ブランチ）と head.repo.full_name を読む\n' "$sh_owner" "$sh_repo" "$number"
				printf '  2. head.repo.full_name が %s/%s と違えばフォークの MR。元ブランチがこのリポジトリに無いので作業を始められない。ユーザに伝えて止める\n' "$sh_owner" "$sh_repo"
				printf '  3. 同じなら head.ref のブランチで、.ccnavi/docs/worktree-start.md の「既にある / を含むブランチ」の手順どおりにワークツリーを切る\n'
			fi
			;;
		merged)
			printf '  1. mcp__github__list_pull_requests を owner=%s repo=%s head=%s:%s state=closed で呼び、マージ済み（merged_at がある）の PR が無いことを確かめる\n' "$sh_owner" "$sh_repo" "$sh_owner" "$stop_branch"
			printf '     mcp__github__* がセッションに無いときは gh で代行する: gh api -X GET repos/%s/%s/pulls -f state=closed -f per_page=100 -f head=%s:%s（-X GET を付ける。-f だけだと POST になる。-f なら gh が値をエンコードする）\n' "$sh_owner" "$sh_repo" "$sh_owner" "$stop_branch"
			printf '  2. あれば、マージ済みのブランチは再利用できない。ユーザに伝えて止める\n'
			printf '  3. 無いと確かめたら、同じコマンドに --mcp-checked を付けて再実行する: %s --mcp-checked\n' "$sh_cmd"
			;;
		title)
				printf '  1. mcp__github__issue_read を method=get owner=%s repo=%s issue_number=%s で呼び、issue が実在することを確かめて題を読む（無ければ番号を作らず、ユーザに伝えて止める）\n' "$sh_owner" "$sh_repo" "$number"
			printf '  2. 題から ASCII の小文字・数字・ハイフンの語を決め、再実行する: %s --slug <語> --title '"'"'<題>'"'"' --mcp-checked\n' "$sh_cmd"
			;;
		*)
			printf '  1. mcp__github__list_pull_requests を owner=%s repo=%s head=%s:%s state=open で呼び、このブランチの開いた PR が無いことを確かめる（あればその番号を <作った番号> にして 3 へ）\n' "$sh_owner" "$sh_repo" "$sh_owner" "$stop_branch"
			printf '  2. mcp__github__create_pull_request を次の値で呼ぶ\n'
			printf '       owner: %s\n       repo: %s\n       head: %s\n       base: %s\n       title: %s\n       body: Closes #%s\n       draft: true\n' "$sh_owner" "$sh_repo" "$stop_branch" "$stop_target" "$stop_title" "$number"
			printf '  3. 作れた PR の番号を <作った番号> に入れて、打ち直す: %s（ブランチとワークツリーがあるので、続きから進んでワークツリーのパスを出す）\n' "$sh_cmd"
			;;
		esac
	else
		case "$sh_stage" in
		search) printf 'ccnavi-start: GitLab APIに接続できず、%s #%s に紐づく既存の MR を確かめられなかった（%s）。既存の MR を見落として重複を作らないため、何も作成せずに処理を中断しました。\n' "$what" "$number" "$sh_why" ;;
		title) printf 'ccnavi-start: GitLab APIに接続できず、issue #%s を読めなかった（%s）。実在を確かめられないので、何も作成せずに処理を中断しました。\n' "$number" "$sh_why" ;;
		merged) printf 'ccnavi-start: GitLab APIに接続できず、ブランチ %s と同じ名前のマージ済みの MR があるかを確かめられなかった（%s）。マージ済みのブランチを再利用しないため、何も作成せずに処理を中断しました。\n' "$stop_branch" "$sh_why" ;;
		issue-link) printf 'ccnavi-start: MR !%s が閉じる issue を確かめられなかった（%s）。issue と MR のセットが確定しないため、続けずに処理を中断しました。\n' "$stop_mr" "$sh_why" ;;
		*) printf 'ccnavi-start: GitLab APIに接続できず、マージリクエストを作れなかった（%s）。\n' "$sh_why" ;;
		esac
		printf '完了済みの処理:\n'
		if [ "$sh_stage" = create ]; then
			printf '  ブランチ: %s（push 済み）\n  ワークツリー: %s\n' "$stop_branch" "$stop_wt"
		else
			printf '  なし（ブランチもワークツリーも作っていない）\n'
		fi
		[ -z "$stop_local" ] || printf '  手元の候補: %s\n' "$stop_local"
		printf '手で行う手順（GitLab には MCP の案内がありません）:\n'
		printf '  1. glab を %s に認証するか、GITLAB_TOKEN を置く（ユーザに頼む）。届くようになったら同じコマンドを再実行する: %s\n' "${ccnavi_h_host:-?}" "$sh_cmd"
		case "$sh_stage" in
		create)
			printf '  2. 再実行できないなら、ブラウザで %s の MR を手で作る。ソース: %s、ターゲット: %s、題: Draft: %s、説明: Closes #%s。作れたら、その番号を <作った番号> に入れて打ち直す: %s\n' "$ccnavi_h_path" "$stop_branch" "$stop_target" "$stop_title" "$number" "$sh_cmd"
			;;
		issue-link)
			printf '  2. 再実行できないなら、ブラウザで MR !%s の説明欄と元ブランチを読み、閉じる issue（Closes #<番号>）を確かめ、打ち直す: %s\n' "$stop_mr" "$sh_cmd"
			;;
		search)
			printf '  2. 再実行できないなら、ブラウザで %s の MR の一覧から、%s #%s に紐づく MR があるか確かめ、無ければ --mcp-checked を付けて再実行します。\n' "$ccnavi_h_path" "$what" "$number"
			;;
		merged)
			printf '  2. 再実行できないなら、ブラウザで %s の MR の一覧から、ブランチ %s のマージ済みの MR が無いか確かめ、無ければ --mcp-checked を付けて再実行します。\n' "$ccnavi_h_path" "$stop_branch"
				;;
		title)
			printf '  2. 再実行できないなら、ブラウザで issue #%s の題を読み、%s --slug <語> --title '"'"'<題>'"'"' と実行します（issue が実在すると確かめたあとに限り、--mcp-checked を付ける）。\n' "$number" "$sh_cmd"
			;;
		esac
	fi
	exit "$EXIT_HOST"
}

# ---- 統合先。worktree.md の決め方（そのリポジトリの origin/HEAD → origin/main・master）。
integration=$(ccnavi_integration "$top") || integration=""
[ -n "$integration" ] ||
	fail no-integration "$(ccnavi_integration_hint "$top")"

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
#
# 行き先は .claude/worktrees/<識別子>（識別子はブランチ名の / を - に置き換えたもの）。既にあるブランチは
# `worktree add <行き先> <ブランチ>` で直接チェックアウトする。
wt=""
ensure_worktree() {
	ew_branch="$1"
	ew_start="${2:-}"
	wt=$(worktree_of "$ew_branch")
	if [ -n "$wt" ]; then
		log_info ワークツリーがある -- "branch=$ew_branch"
		return 0
	fi
	ew_id=$(ccnavi_branch_to_id "$ew_branch")
	ew_dest="$root/.claude/worktrees/$ew_id"
	if [ -e "$ew_dest" ] || [ -L "$ew_dest" ]; then
		ew_cur=$(git -C "$ew_dest" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
		fail worktree-busy "ワークツリーの作成先 $ew_dest が既にあり、ブランチ $ew_branch のものではありません（現在のブランチ：${ew_cur:-?}）。中を確かめ、不要なら片付けてから再実行してください。"
	fi
	if [ -n "$ew_start" ]; then
		cgit worktree add "$ew_dest" -b "$ew_branch" --no-track "$ew_start" ||
			fail worktree-failed "ワークツリーを作成できませんでした（上の出力を確認してください）。"
		wt="$ew_dest"
		log_info ワークツリーを切った -- "branch=$ew_branch"
		return 0
	fi
	# 既にあるブランチ。手元に無ければ origin/<ブランチ> から（git が追跡ブランチを作る）
	ref_exists "refs/heads/$ew_branch" || ref_exists "refs/remotes/origin/$ew_branch" ||
		fail branch-missing "ブランチ $ew_branch が手元にも origin にもありません。ホストにだけある MR の元ブランチ（消された後など）かもしれません。"
	cgit worktree add "$ew_dest" "$ew_branch" ||
		fail worktree-failed "ワークツリーを作成できませんでした（上の出力を確認してください）。"
	wt="$ew_dest"
	log_info ワークツリーを切った -- "branch=$ew_branch"
}

# 続けるブランチを毎回 origin から取る（手元にあっても取る）。取れなくても、手元にあれば続ける（リモート未確認と言う）。
# 結果は remote_state（checked / gone / unchecked）に入れる。gone は origin にそのブランチが無い。
remote_state=""
fetch_branch() {
	fb_branch="$1"
	if cgit_quiet fetch origin "$fb_branch"; then
		remote_state=checked
		return 0
	fi
	case "$cgit_out" in
	*"couldn't find remote ref"*)
		remote_state=gone
		;;
	*)
		remote_state=unchecked
		log_info リモートを確かめられず -- "branch=$fb_branch"
		ref_exists "refs/heads/$fb_branch" ||
			fail fetch-failed "ブランチ $fb_branch を origin から取れなかった（$(printf '%s' "$cgit_out" | head -n 1)）。手元にも無いので続けられません。"
		;;
	esac
}

# ワークツリー（wt）の先頭を origin/<ブランチ> に早送りする。手元が先・分かれているときは動かさず言う
# （merge は ccnavi-sync.sh の仕事）。
ff_note=""
fast_forward() {
	ff_branch="$1"
	ff_note=""
	ref_exists "refs/remotes/origin/$ff_branch" || return 0
	ff_local=$(git -C "$wt" rev-parse HEAD 2>/dev/null || :)
	ff_remote=$(git -C "$wt" rev-parse "refs/remotes/origin/$ff_branch" 2>/dev/null || :)
	[ -n "$ff_local" ] && [ -n "$ff_remote" ] && [ "$ff_local" != "$ff_remote" ] || return 0
	if git -C "$wt" merge-base --is-ancestor "$ff_local" "$ff_remote" 2>/dev/null; then
		if (cd "$wt" && cgit merge --ff-only --no-overwrite-ignore "origin/$ff_branch"); then
			ff_note="origin/$ff_branch に早送りした"
			log_info 早送りした -- "branch=$ff_branch"
		else
			ff_note="origin/$ff_branch に早送りできなかった（上の出力を見てください）"
		fi
	elif git -C "$wt" merge-base --is-ancestor "$ff_remote" "$ff_local" 2>/dev/null; then
		ff_note="手元が origin/$ff_branch より先に進んでいる（未送信）"
	else
		ff_note="手元と origin/$ff_branch が分かれている（取り込みは ccnavi-sync.sh が行う）"
	fi
}

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
		log_info push は飛ばす -- "branch=$1"
		return 0
	fi
	(cd "$wt" && cgit push -u origin "$1") ||
		fail push-failed "ブランチ $1 を push できなかった（上の出力を見てください。拒否されたら、そこに出ている案内に従ってください）。"
	log_info push した -- "branch=$1"
}

# ensure_mr <ブランチ> <題>。MR の URL を mr_url に入れる。ホストに届かなければ案内して止まる
mr_url=""
mr_num=""
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
		fail host-unreadable "MR を作れません（${host_why}）。origin を確かめてください。"
	elif [ "$em_rc" -eq 2 ]; then
		stop_host create "$host_why"
	fi
	em_mr=""
	em_found_rc=0
	em_mr=$(find_mr "$em_branch") || em_found_rc=$?
	[ "$em_found_rc" -eq 0 ] || stop_host create "$(api_failed_reason)"
	if [ -n "$em_mr" ]; then
		mr_url=$(printf '%s' "$em_mr" | jq -r '.url')
		mr_num=$(printf '%s' "$em_mr" | jq -r '.number')
		log_info MR は飛ばす -- "branch=$em_branch"
		return 0
	fi
	em_made_rc=0
	em_made=$(create_mr "$em_branch" "$integration" "$em_title" "Closes #$number") || em_made_rc=$?
	[ "$em_made_rc" -eq 0 ] || stop_host create "$(api_failed_reason)"
	mr_url=$(printf '%s' "$em_made" | jq -r '.url')
	mr_num=$(printf '%s' "$em_made" | jq -r '.number')
	log_info MR を作った -- "branch=$em_branch"
}

# 最後に ccnavi-sync.sh <識別子> を打つ。親チケットも提案もまだ無いと取り込まれないが、止めない。<ブランチ>
sync_note=""
# ccnavi-sync.sh が「親チケットも提案もありません」で取り込まなかったとき 1 にする。report が提案を作る次の一手を出す
sync_no_ticket=""
# ccnavi-sync.sh が取り込んだとき 1 にする（親チケットか提案が既にある）。report が状態を確かめる次の一手を出す
sync_ok=""
# 親チケットも提案も無い以外の理由で取り込めなかったとき、次の一手の文を入れる
sync_next=""
# 提案を作る次の一手に書く題。新しく作る MR の題か、採用する MR の題。分からなければ空
next_title=""
run_sync() {
	rs_id=$(ccnavi_branch_to_id "$1")
	rs_rc=0
	rs_out=$(sh "$scripts/ccnavi-sync.sh" "$rs_id" 2>&1) || rs_rc=$?
	log_info ccnavi-sync を打った -- "id=$rs_id" "exit=$rs_rc"
	if [ "$rs_rc" -eq 0 ]; then
		printf 'ccnavi-sync.sh %s:\n' "$rs_id"
		printf '%s\n' "$rs_out" | sed 's/^/  /'
		sync_ok=1
	else
		printf 'ccnavi-sync.sh %s は取り込まなかった（終了コード %s）:\n' "$rs_id" "$rs_rc"
		printf '%s\n' "$rs_out" | sed 's/^/  /'
		# 「親チケットも提案もありません」は ccnavi-sync.sh の文言。変えるときはここと test_start_sh.py も直す
		case "$rs_out" in
		*親チケットも提案もありません*)
			sync_no_ticket=1
			sync_note="親チケットか提案が出来たら、sh $scripts/ccnavi-sync.sh $rs_id を打ち直す"
			;;
		*)
			sync_next="取り込めなかった理由は上の出力のとおり。直したら、sh $scripts/ccnavi-sync.sh $rs_id を打ち直す（直せないときはユーザに伝える）"
			;;
		esac
	fi
}

# ccnavi-sync.sh を打つ。MR の番号は親チケットの mr 欄が持つので、ここでは保存しない。<ブランチ> <MR の番号か空>
finish_up() {
	run_sync "$1"
}

# 結果を 1 項目 1 行で出す。<ブランチ> <MR の URL か空> <MR の番号か空>
report() {
	# 着手の対象。意図したリポジトリかを、出力を読んだ側が確かめられるようにする
	rp_name=$(ccnavi_project "$top" "$root")
	printf '対象リポジトリ: %s（%s）\n' "${ccnavi_h_path:-$top}" "${rp_name:-ワークスペース自身}"
	printf 'ワークツリー: %s\n' "$wt"
	printf 'ブランチ: %s\n' "$1"
	printf '識別子: %s\n' "$(ccnavi_branch_to_id "$1")"
	printf 'MR: %s\n' "${2:--}"
	[ -z "$ff_note" ] || printf '最新化: %s\n' "$ff_note"
	[ -z "$host_unseen" ] || printf '案内: ホストは見ていない（MCP などで確認済みとして扱った）。既存の MR と閉じる issue は、渡された --issue・--mr・--branch のとおりに扱った\n'
	case "$remote_state" in
	unchecked) printf '案内: リモート未確認。origin から取れなかったので、手元のブランチのまま続けた。つながったら sh %s/ccnavi-sync.sh を打つ\n' "$scripts" ;;
	gone) printf '案内: origin にブランチ %s が無い（手元だけのブランチ）\n' "$1" ;;
	esac
	[ -z "$sync_note" ] || printf '案内: %s\n' "$sync_note"
	case "${3:-}" in
	'' | *[!0-9]*) ;;
	*)
		printf '必須: 提案の frontmatter に issue: %s と mr: %s を書く（この issue とこの MR の組。書かないと、この MR の番号を含む依頼が、すでにあるチケットに結び付かない。ccnavi ticket new の --issue と --mr が書く）\n' "$final_issue" "$3"
		;;
	esac
	[ -z "$sync_no_ticket" ] || next_ticket_lines "$1" "${3:-}"
	[ -z "$sync_next" ] || printf '次: %s\n' "$sync_next"
	[ -z "$sync_ok" ] || printf '次: sh %s/ccnavi-ticket.sh status %s で、このチケットの状態と次の一手を確かめる\n' "$scripts" "$(ccnavi_branch_to_id "$1")"
}

# 親チケットも提案も無いワークツリーの次の一手。提案を作る ccnavi ticket new を、埋められる値を埋めて出す。<ブランチ> <MR の番号か空>
# 触るファイルは依頼から読み取って --scope に書くしかないので、<触るファイルの glob> は残す。
# 次の一手を提案づくりに絞るのは、承認が通らないなどで詰まったエージェントが、提案を諦めて直接実装に進んだことがあるため
next_ticket_lines() {
	nt_id=$(ccnavi_branch_to_id "$1")
	nt_title=$(sh_quote "${next_title:-<題>}")
	nt_issue=" --issue $final_issue"
	nt_mr=""
	case "${2:-}" in
	'' | *[!0-9]*) ;;
	*) nt_mr=" --mr $2" ;;
	esac
	nt_scope=$(sh_quote "<触るファイルの glob>")
	printf '次: ワークツリーの中で sh %s/ccnavi-launcher.sh ticket new %s --title %s --plan quick-work --scope %s%s%s で提案を作る。作れたら、承認をユーザに頼んで返事を待つ\n' \
		"$scripts" "$nt_id" "$nt_title" "$nt_scope" "$nt_issue" "$nt_mr"
	printf '案内: 設計判断が要らない作業は、範囲が複数のディレクトリや文書・設定にまたがっても quick-work。大きい作業は --plan を design,implement のように変える（型と範囲は .ccnavi/config/common/phases.yml）。提案は手で書かず、承認が通らないなど詰まったら、直接実装に切り替えず、ユーザに報告して止まる\n'
}

# 既存の MR を採用してよいか確かめる。引っかかったら止める。<ブランチ> <MR の JSON>
#
# 確かめるのは 7 つ。MR が open・フォークでない・マージ先が統合先・ブランチ名が保護されていない・
# 変換した識別子が使える（58 文字以内、末尾が -2 桁でない。実行ファイルに聞く）・同じ識別子になる別のブランチが無い
# （origin の全ブランチ。ls-remote。英字の大文字小文字は区別しない）・同じブランチ名のマージ済み MR が無い
# （ホストに聞く。手元の退避には頼らない）。後ろの 3 つは新しく作るとき（id_check・clash_check・merged_check）も同じ。
adopt_check() {
	ad_branch="$1"
	ad_mr="$2"
	ad_num=$(printf '%s' "$ad_mr" | jq -r '.number // ""')
	ad_state=$(printf '%s' "$ad_mr" | jq -r '.state // ""')
	ad_base=$(printf '%s' "$ad_mr" | jq -r '.base // ""')
	ad_fork=$(printf '%s' "$ad_mr" | jq -r '.fork // false')
	case "$ad_state" in
	open | opened) ;;
	*) fail adopt-not-open "MR !${ad_num} は ${ad_state:-状態が不明}。open の MR だけ採用できます（閉じた・マージ済みの MR のブランチでは続けません）。ユーザに確かめてください。" ;;
	esac
	[ "$ad_fork" != true ] ||
		fail fork-mr "MR !${ad_num} はフォークから出ている。元ブランチ ${ad_branch} はこのリポジトリに無いので、ここでは作業を始められません。フォーク側のブランチで続けるか、ユーザに相談してください。"
	[ "$ad_base" = "$integration" ] ||
		fail adopt-target "MR !${ad_num} のマージ先は ${ad_base:-不明} で、統合先 ${integration} ではありません。マージ先を直すか、別の作業としてユーザに相談してください。"
	if ! ccnavi_is_branch "$ad_branch" || [ "$ad_branch" = "$integration" ]; then
		fail adopt-protected "ブランチ ${ad_branch} は保護されたブランチ（統合先・main・master・develop・release など）か、使えない書き方です。MR の元ブランチとして採用できません。"
	fi
	id_check "$ad_branch"
	clash_check "$ad_branch" strict
	merged_check "$ad_branch"
	log_info 既存の MR を採用してよい -- "branch=$ad_branch" "mr=$ad_num"
}

# ブランチ <ブランチ> から作る識別子が親に使えるかを実行ファイルに聞き（58 文字以内・末尾が -2 桁でない・
# 字の種類・NFC）、使えなければ止める。答えが無ければ「確かめられない」と止める（sh で数えると、
# dash の `${#x}` はバイト数を数えるので、日本語の識別子を誤って断る）。[<案内>]
id_check() {
	ic_branch="$1"
	ic_hint="${2:-ブランチ名を直すか、ユーザに相談してください。}"
	ccnavi_id_ask "$root" "$ic_branch" ||
		fail id-unchecked "ブランチ ${ic_branch} から作る識別子が親に使えるか（58 文字以内・末尾が -2 桁でない・使える字）を確かめられない（ccnavi の実行ファイルが答えない）。何も作成せずに処理を中断しました。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してから再実行してください。"
	[ -z "$ccnavi_id_problem" ] ||
		fail adopt-id "ブランチ ${ic_branch} から作る識別子 ${ccnavi_id_ident} は使えない（${ccnavi_id_problem}）。何も作成せずに処理を中断しました。${ic_hint}" "${3:-1}"
}

# 同じ識別子になる別のブランチ（origin の全ブランチと手元のブランチ。英字の大文字小文字は区別しない）があれば止める。
# <ブランチ> strict|lenient。origin の一覧（ls-remote）を取れないとき、strict は止め、lenient は手元に残る
# origin の追跡ブランチで代わりに確かめて「リモート未確認」と言う。
clash_note=""
clash_check() {
	cc_branch="$1"
	cc_mode="$2"
	cc_remote=yes
	if ! remote_heads; then
		[ "$cc_mode" = lenient ] ||
			fail adopt-heads "origin のブランチ一覧を取れなかった（$(head -n 1 "$heads_file.err" 2>/dev/null)）。同じ識別子になる別のブランチが無いか確かめられないので止めた。"
		cc_remote=no
		git for-each-ref --format='%(refname:lstrip=3)' refs/remotes/origin 2>/dev/null | grep -v -x 'HEAD' >"$heads_file" || :
	fi
	{
		cat "$heads_file"
		git for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null || :
	} | sort -u >"$heads_file.all"
	cc_clash=$(ccnavi_id_clash "$cc_branch" "$heads_file.all") || cc_clash=""
	rm -f "$heads_file.all"
	[ -z "$cc_clash" ] ||
		fail adopt-id-clash "ブランチ ${cc_branch} と ${cc_clash} は、どちらも識別子 $(ccnavi_branch_to_id "$cc_branch")（英字の大文字小文字は区別しない）になる。ワークツリーの置き場が重なるので使えません。何も作成せずに処理を中断しました。ユーザに相談してください。"
	if [ "$cc_remote" = no ]; then
		clash_note="リモート未確認。origin のブランチ一覧を取れなかったので、同じ識別子になる別のブランチは手元に残る origin の追跡ブランチでだけ確かめた"
		log_info 同じ識別子の確認でリモート未確認 -- "branch=$cc_branch"
	fi
}

# 同じブランチ名のマージ済み MR があれば止める（ホストに聞く。手元の退避には頼らない）。<ブランチ>
merged_check() {
	stop_branch="$1"
	mc_rc=0
	host_open || mc_rc=$?
	if [ "$mc_rc" -eq 1 ]; then
		fail host-unreadable "同じブランチ名のマージ済み MR を確かめられません（${host_why}）。origin を確かめてください。"
	elif [ "$mc_rc" -eq 2 ]; then
		[ -z "$mcp_checked" ] || return 0
		stop_host merged "$host_why"
	fi
	mc_merged=""
	mc_merged_rc=0
	mc_merged=$(find_merged_mr "$1") || mc_merged_rc=$?
	if [ "$mc_merged_rc" -ne 0 ]; then
		[ -z "$mcp_checked" ] || return 0
		stop_host merged "$(api_failed_reason)"
	fi
	if [ -n "$mc_merged" ]; then
		fail adopt-merged "同じブランチ名 ${1} のマージ済み MR（!$(printf '%s' "$mc_merged" | jq -r '.number')）がある。マージ済みのブランチは再利用できません。新しいブランチで始めるか、ユーザに相談してください。"
	fi
}

# 新規作成の MR の題。--title、無ければ issue の題、読めなければ「#<番号> <slug>」
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

# ホストを見られていない
unchecked=""
[ "$checked" = true ] || unchecked=1

# ホストに届かず、エージェントが別の手段（MCP など）で確かめたうえで issue・MR・元ブランチがそろっているとき:
# 候補の探索の代わりに --branch のブランチで、ホストを見ずに続ける。ホストにしか無い情報は補わない
if [ -n "$unchecked" ] && [ -n "$mcp_checked" ] && [ -n "$issue_arg" ] && [ -n "$mr_arg" ] && [ -n "$branch_arg" ]; then
	cb="$branch_arg"
	{ ccnavi_is_branch "$cb" && [ "$cb" != "$integration" ]; } ||
		fail adopt-protected "ブランチ ${cb} は保護されたブランチ（統合先・main・master・develop・release など）か、使えない書き方です。MR の元ブランチとして採用できません。"
	[ -n "$(branch_ref "$cb")" ] ||
		fail branch-missing "ブランチ $cb が手元にも origin にもありません。--branch の綴りを確かめてください。"
	id_check "$cb"
	mode=strict
	remote_heads || mode=lenient
	clash_check "$cb" "$mode"
	[ -z "$clash_note" ] || printf 'ccnavi-start: 案内: %s\n' "$clash_note"
	if [ -n "$dry" ]; then
		printf 'dry-run: 何も作らない。ブランチ %s で続ける予定（ホストは見ていない）。\n' "$cb"
		printf '次: 問題なければ、--dry-run を外して実行する: sh %s/ccnavi-start.sh %s\n' "$scripts" "$real_args"
		exit 0
	fi
	stop_branch="$cb"
	host_unseen=1
	fetch_branch "$cb"
	ensure_worktree "$cb"
	fast_forward "$cb"
	final_issue=$(issue_show "$issue_r" "$issue_n")
	final_mr="$mr_arg"
	next_title=""
	finish_up "$cb" "$mr_arg"
	report "$cb" "$mr_arg" "$mr_arg"
	log_info MCP で確かめ済みとして続ける -- "branch=$cb"
	exit 0
fi

if [ "$total" -gt 1 ]; then
	show_list
	printf 'ccnavi-start: 候補が %s 件見つかったため、何も作成せずに処理を中断しました。ユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞いて、返事を待ってください。\n' "$total"
	log_info 候補が複数 -- "total=$total"
	exit "$EXIT_MULTI"
fi

# ホストを見られないまま続けない（手元の候補だけでは、既存の MR も閉じる issue も確かめられない）。
# --mcp-checked でも、新しく作る道（候補が 0 件で --issue だけ）以外は、3 つの引数がそろわなければ止める
if [ -n "$unchecked" ] && { [ "$total" -ge 1 ] || [ "$what" = mr ]; }; then
	host_rc=0
	host_open || host_rc=$?
	if [ "$host_rc" -eq 1 ]; then
		fail host-unreadable "ホストを見られず（${host_reason}）、origin も読めない（${host_why}）。何も作成せずに処理を中断しました。"
	fi
	stop_branch=$(jq -r '.candidates[0].branch // ""' "$cand_json")
	if [ -n "$mcp_checked" ]; then
		stop_host mcp-args "${host_reason:-$host_why}"
	fi
	stop_host search "${host_reason:-$host_why}"
fi

if [ "$total" -eq 0 ]; then
	if [ "$what" = mr ]; then
		fail mr-not-found "MR #${number} の元ブランチを読めなかった。番号を確かめてください。"
	fi
	if [ -n "$unchecked" ] && [ -z "$mcp_checked" ]; then
		# 既存の MR を見落として重複を作らないため、新規作成に進まず止める
		host_rc=0
		host_open || host_rc=$?
		if [ "$host_rc" -eq 1 ]; then
			fail host-unreadable "ホストを見られず（${host_reason}）、origin も読めない（${host_why}）。既存の MR を見落として重複を作らないため、何も作成せずに処理を中断しました。"
		fi
		stop_host search "${host_reason:-$host_why}"
	fi
	# ---- 2. 新規作成（--issue だけ。MR の本文に Closes #<番号> を入れるので、issue と MR の組は作った時点で確定する）
	host_rc=0
	host_open || host_rc=$?
	if [ "$host_rc" -eq 1 ]; then
		fail host-unreadable "MR を作れません（${host_why}）。origin を確かめてください。"
	fi
	issue_rc=0
	# --title を付けても issue の存在は確かめる（実在しない番号の Closes で別の課題を閉じないため）。
	# 確かめを省けるのは、ホストに届かないとき（MCP で読んだ後の再実行）だけ
	if [ "$host_rc" -eq 0 ]; then
		issue_title_get || issue_rc=$?
		case "$issue_rc" in
		2) fail issue-missing "issue #${number} が見つからない、または読む権限がありません（${issue_why}）。番号を作らず、ユーザに確かめてください。issue が無ければ、ユーザに作ってもらい、その番号で打ち直します（チケット作業は issue と MR のセットです）。" 2 ;;
		1)
			# API が失敗して実在を確かめられなかった。--slug・--title があっても無確認で進まない
			# （確かめた後の再実行は --mcp-checked を付ける）
			[ -n "$mcp_checked" ] || stop_host title "$(api_failed_reason)"
			;;
		3) fail issue-is-pr "#${number} は issue ではなく PR です。MR の番号なら --mr ${number} を使ってください。" 2 ;;
		esac
	fi
	if [ -n "$opt_slug" ]; then
		slug="$opt_slug"
		new_branch="${opt_prefix}-${number}-${slug}"
	else
		# 題が読めなければ slug を決められない（ホストに届かないなら代行を案内する）
		if [ -z "$opt_title" ] && [ -z "$issue_title_read" ]; then
			if [ "$host_rc" -eq 2 ]; then
				stop_host title "$host_why"
			fi
			if [ "$issue_rc" -eq 1 ]; then
				stop_host title "$(api_failed_reason)"
			fi
		fi
		ib_rc=0
		issue_branch_make "${opt_title:-$issue_title_read}" || ib_rc=$?
		case "$ib_rc" in
		0) ;;
		2) fail no-slug "issue #${number} からブランチ名を作れませんでした（${issue_branch_why}）。--slug <語> を付けて再実行してください。" 2 ;;
		*) fail id-unchecked "issue #${number} からブランチ名を作れませんでした（ccnavi の実行ファイルが答えない）。何も作成せずに処理を中断しました。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してから再実行するか、--slug <語> を付けて再実行してください。" ;;
		esac
		new_branch="$issue_branch"
		slug="${new_branch#"${opt_prefix}-${number}-"}"
	fi
	ccnavi_is_branch "$new_branch" || fail bad-branch "ブランチ名 ${new_branch} は使えない書き方です（--prefix・--slug を見直してください）。" 2
	# 作る前に、既存の MR を採用するときと同じ識別子の確認をする。作ってから後段で詰まると、打ち直しても
	# 採用の確認で止まり、手で消すまで戻れない
	id_check "$new_branch" "--prefix・--slug を見直して再実行してください。" 2
	clash_check "$new_branch" strict
	merged_check "$new_branch"
	new_title=$(make_title "$slug")
	new_wt="$root/.claude/worktrees/$new_branch"
	final_issue=$(issue_show "" "$number")
	stop_branch="$new_branch"
	if [ -n "$dry" ]; then
		printf 'dry-run: 何も作らない。作る予定は次のとおり。\n'
		printf 'ブランチ: %s（起点 origin/%s）\n' "$new_branch" "$integration"
		printf 'ワークツリー: %s\n' "$new_wt"
		printf 'MR の題: %s（Draft。本文: Closes #%s）\n' "$new_title" "$number"
		printf 'issue: %s（MR を作ると確定する）\n' "$final_issue"
		[ "$host_rc" -eq 0 ] || printf '案内: ホストに届かず、題は仮のもの（%s）\n' "$host_why"
		printf '次: 問題なければ、--dry-run を外して実行する: sh %s/ccnavi-start.sh %s\n' "$scripts" "$real_args"
		exit 0
	fi
	cgit fetch origin "$integration" || fail fetch-failed "統合先 ${integration} を origin から取れなかった（上の出力を見てください）。"
	ref_exists "refs/remotes/origin/$integration" || fail no-origin-integration "origin/${integration} がありません。統合先の名前を確かめてください。"
	ensure_worktree "$new_branch" "origin/$integration"
	ensure_initial_commit
	ensure_push "$new_branch"
	ensure_mr "$new_branch" "$new_title"
	final_mr="$mr_num"
	next_title="$new_title"
	finish_up "$new_branch" "$mr_num"
	report "$new_branch" "$mr_url" "$mr_num"
	log_info 完了 -- "branch=$new_branch"
	exit 0
fi

# ---- 3. 候補がちょうど 1 件（ホストを見られている）
cb=$(jq -r '.candidates[0].branch' "$cand_json")
ccnavi_is_branch "$cb" || fail bad-candidate "候補のブランチ名（${cb}）が使えない書き方か、保護されたブランチ（統合先・main・master・develop・release など）です。ユーザに確かめてください。"
c_local=$(jq -r '.candidates[0].local' "$cand_json")
c_origin=$(jq -r '.candidates[0].origin' "$cand_json")
c_mrs=$(jq '.candidates[0].mrs | length' "$cand_json")
c_url=$(jq -r '[.candidates[0].mrs[]?.url][0] // ""' "$cand_json")
c_mrnum=$(jq -r '[.candidates[0].mrs[]?.number][0] // ""' "$cand_json")

if [ "$c_local" != true ] && [ "$c_origin" != true ] && [ "$c_mrs" -eq 0 ]; then
	# どこにも実体が無い（チケットの提案だけ）。ユーザが決める
	show_list
	printf 'ccnavi-start: 候補 %s は、手元にも origin にも MR にも実体がありません（チケットの提案だけ）。何も作成せずに処理を中断しました。ユーザに確かめてください。\n' "$cb"
	exit "$EXIT_MULTI"
fi
# --branch はホストに届くとき、普通に確かめる側の指定（MR の元ブランチと違えば止める）
if [ -n "$branch_arg" ] && [ "$cb" != "$branch_arg" ]; then
	fail branch-mismatch "--branch ${branch_arg} は、ホストで見つかった元ブランチ ${cb} と違います。ブランチ名を確かめて打ち直してください。" 1
fi
# 既存の MR を採用するときの確認。引っかかったら止める
if [ "$c_mrs" -gt 0 ]; then
	c_mr=$(jq -c '.candidates[0].mrs[0]' "$cand_json")
	adopt_check "$cb" "$c_mr"
	# adopt_check の後で、その MR が閉じる issue を確かめる
	stop_branch="$cb"
	resolve_issue "$c_mrnum"
else
	# MR の無い候補（名前に番号を含む手元・origin のブランチ）も、親のワークツリーに出すので識別子の確認はする
	# （MR が open かなど、MR に依る確認は除く）。origin の一覧を取れなければ、手元の追跡ブランチで確かめて言う
	id_check "$cb"
	clash_check "$cb" lenient
	[ -z "$clash_note" ] || printf 'ccnavi-start: 案内: %s\n' "$clash_note"
fi

# 作りかけ（ccnavi-start.sh が途中で失敗した跡）か。MR が無く、初期コミット（init_subject）だけ
# 積んであり、名前に / を含まないなら、残りの段（push・MR）を続ける。
# 統合先より進んでいない（c_ahead=0）ブランチは作りかけと見なさない。取り込み済みで消し忘れた古いブランチと区別できず、
# 続けると空コミット・push・新しい Draft MR を作ってしまう。一覧を出してユーザに聞く
resume=""
if [ "$c_mrs" -eq 0 ]; then
	case "$cb" in
	*/*) ;;
	*)
		cref=$(branch_ref "$cb")
		if [ -n "$cref" ] && ref_exists "refs/remotes/origin/$integration"; then
			c_ahead=$(git rev-list --count "origin/$integration..$cref" 2>/dev/null || echo 9)
			c_subject=$(git log -1 --format=%s "$cref" 2>/dev/null || :)
			if [ "$c_ahead" -eq 0 ]; then
				show_list
				printf 'ccnavi-start: 候補 %s は、統合先 %s より進んでおらず、開いた MR もありません（取り込み済みで消し忘れた古いブランチかもしれない）。何も作成せずに処理を中断しました。ユーザに見せ、このブランチを使うか・新しく切るか・やめるかを聞いて、返事を待ってください。\n' "$cb" "$integration"
				log_info 古いブランチらしい -- "branch=$cb"
				exit "$EXIT_MULTI"
			fi
			if [ "$c_ahead" -eq 1 ] && [ "$c_subject" = "$init_subject" ] && [ "$what" = issue ]; then
				resume=1
			fi
		fi
		;;
	esac
	if [ -z "$resume" ]; then
		# MR の無い候補は成功させない（issue と MR のセットが確定しない）
		show_list
		printf 'ccnavi-start: 候補 %s には開いた MR がありません。issue と MR のセットが確定しないため、何も作成せずに処理を中断しました。このブランチで続けるなら、ユーザに MR（説明欄に Closes #%s）を作ってもらい、--mr <番号> を付けて打ち直します。新しく切るか・やめるかも含め、ユーザに聞いて返事を待ってください。\n' "$cb" "$number"
		log_info MR の無い候補 -- "branch=$cb"
		exit "$EXIT_MULTI"
	fi
	final_issue=$(issue_show "" "$number")
	# 作りかけの再開は MR を作る。ワークツリーを切る前に、ホストと権限を確かめる
	host_rc=0
	host_open || host_rc=$?
	if [ "$host_rc" -eq 1 ]; then
		fail host-unreadable "MR を作れません（${host_why}）。origin を確かめてください。"
	elif [ "$host_rc" -eq 2 ]; then
		stop_host search "$host_why"
	fi
else
	final_mr="$c_mrnum"
fi

if [ -n "$dry" ]; then
	printf 'dry-run: 何も作らない。既存のブランチ %s で続ける予定。\n' "$cb"
	existing=$(worktree_of "$cb")
	if [ -n "$existing" ]; then
		printf 'ワークツリー: %s\n' "$existing"
	else
		printf 'ワークツリー: %s（無いので作る）\n' "$root/.claude/worktrees/$(ccnavi_branch_to_id "$cb")"
	fi
	printf 'ブランチ: %s\n' "$cb"
	if [ -n "$resume" ]; then
		printf '作りかけなので、残りの段（初期コミット・push・Draft MR）も続ける予定。MR の題: %s\n' "$(make_title "$cb")"
	else
		printf 'MR: %s\n' "${c_url:--}"
	fi
	printf 'issue: %s\n' "$final_issue"
	printf '次: 問題なければ、--dry-run を外して実行する: sh %s/ccnavi-start.sh %s\n' "$scripts" "$real_args"
	exit 0
fi

if [ -n "$resume" ]; then
	# 作りかけ。ワークツリーを出して、残りの段を続ける
	fetch_branch "$cb"
	ensure_worktree "$cb"
	fast_forward "$cb"
	if [ -z "$opt_title" ]; then
		issue_title_get || :
	fi
	ensure_initial_commit
	ensure_push "$cb"
	next_title=$(make_title "$cb")
	ensure_mr "$cb" "$next_title"
	final_mr="$mr_num"
	finish_up "$cb" "$mr_num"
	report "$cb" "$mr_url" "$mr_num"
	log_info 作りかけを完了 -- "branch=$cb"
	exit 0
fi

stop_branch="$cb"
fetch_branch "$cb"
ensure_worktree "$cb"
fast_forward "$cb"
next_title=$(jq -r '.candidates[0].mrs[0].title // ""' "$cand_json")
finish_up "$cb" "$c_mrnum"
report "$cb" "$c_url" "$c_mrnum"
log_info 続ける -- "branch=$cb"
exit 0

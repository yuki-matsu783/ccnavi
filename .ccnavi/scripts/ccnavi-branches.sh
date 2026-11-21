#!/bin/sh
# ccnavi-branches issue・MR に紐づくブランチを探す。読むだけで、ワークツリーにも git にも書かない。
#
#   sh .ccnavi/scripts/ccnavi-branches.sh --issue <番号> [--json]
#   sh .ccnavi/scripts/ccnavi-branches.sh --mr <番号> [--json]
#
# issue や MR を指定して作業を頼まれたとき、着手の前にエージェントが打つ（UserPromptSubmit の hook が
# 依頼文の `#152`・`!5` などを見つけて、打つように指示を足す）。候補があれば、エージェントは一覧を
# ユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞いてから進める。
#
# 見るのは cwd のリポジトリ（ワークスペース・projects/<名前>・そのワークツリー）。プロジェクトの issue・MR は
# projects/<名前>/ に cd してから打つ。
#
# 役割の分け方:
#   - ホスト（GitHub / GitLab）はこの sh が読む。MR 指定なら、その MR の元ブランチ。issue 指定なら、
#     その issue を参照している開いた MR の元ブランチ。繋ぎ方は ccnavi-common-host.sh の「ホスト（GitHub /
#     GitLab）への接続」（ccnavi-review.sh と同じ。gh / glab か、curl と GITHUB_TOKEN / GITLAB_TOKEN）
#   - 取ってきた応答は整えずにファイルへ書き、実行ファイルの
#     `ccnavi branches <issue|mr> <番号> checked <github|gitlab> <ホスト> <リポジトリ> <プロジェクトの id> --result <応答>` に渡す。
#     どの MR が関係するか・フォークか・マージ済みかを読むのも、手元の候補（名前に番号を含むブランチ・ワークツリー・
#     `issue:` を持つチケット）を集めるのも実行ファイルで、合わせて 1 候補 1 行（--json なら JSON）で出す
#   - ホストに繋げないときは止めない。`unchecked <理由>`（API が失敗したら `failed <呼び出し> --result <ホストの返事>`）を
#     渡し、実行ファイルが「ホストは見ていない」と言って手元の候補だけを出す
#
# 終了コード: 0 出した / 1 前提の未充足（git の外・実行ファイルが異常終了した）/ 2 引数か環境の誤り

set -eu

# fail <識別子> <文面> [終了コード]。文面は標準エラーへ出す契約。識別子は診断ログにだけ残す。
fail() {
	printf 'ccnavi-branches: %s\n' "$2" >&2
	log_info 止めた -- "exit=${3:-1}" "reason=$1"
	exit "${3:-1}"
}

# 共通部分。ワークスペースルートの探し方と、ホストへの繋ぎ方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-branches.sh (--issue <番号> | --mr <番号>) [--json]

  issue・MR に紐づくブランチを探して、1 候補 1 行で出す（--json なら JSON）。読むだけ。
  --issue <番号>  名前に番号を含むブランチ（手元と origin。名前が `<先頭の語>-<番号>-<slug>` の形なら、番号の位置だけで見る）、その issue を参照している開いた MR の元ブランチ、
                  issue: <番号> を持つチケットと、その親のブランチ
  --mr <番号>     その MR の元ブランチ
  各候補には、チェックアウトしているワークツリーと結び付くチケットを添える。
  ホストに繋げないときは手元の候補だけを出し、「ホストは見ていない」と書く。
  cwd のリポジトリを見る。プロジェクトの issue・MR は projects/<名前>/ に cd してから実行します。
USAGE
}

kind=""
number=""
json=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	--issue | --mr)
		[ -z "$kind" ] || fail two-kinds "--issue と --mr はどちらか 1 つだけ渡してください。" 2
		kind="${1#--}"
		[ "$#" -ge 2 ] || fail no-number "$1 の後ろに番号を渡してください。" 2
		number="$2"
		shift 2
		;;
	--json)
		json=1
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
[ -n "$kind" ] || {
	usage >&2
	fail no-kind "--issue <番号> か --mr <番号> を渡してください。" 2
}
case "$number" in
'' | 0* | *[!0-9]*) fail bad-number "番号は 0 で始まらない正の整数で渡してください（${number}）。" 2 ;;
esac
[ "${#number}" -le 9 ] || fail bad-number "番号が長すぎます（${number}）。" 2

root=$(ccnavi_workspace) ||
	fail no-workspace "$(ccnavi_no_workspace_text)" 2
ccnavi_log_root="$root"
here="$(pwd -W 2>/dev/null || pwd)"
git rev-parse --show-toplevel >/dev/null 2>&1 ||
	fail not-git "cwd（${here}）が git のリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で実行してください。"
state="$root/logs/state" # 固定
mkdir -p "$state" 2>/dev/null || fail no-state "state の置き場（${state}）を作れません。" 2
result="$state/branches-host-$$.json"
host_err="$state/branches-host-err-$$"
trap 'rm -f "$result" "$host_err" "$host_err.text"' EXIT

# ---- 実行ファイル。
if bin=$(ccnavi_bin "$root"); then
	ccnavi() { "$bin" --root "$root" --cwd "$here" "$@"; }
else
	fail no-bin "ccnavi の実行ファイルが見つかりません（CCNAVI_ENTRY_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/・PATH のどれにもありません）。scripts/ccnavi-build.sh で組み立てるか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。" 2
fi

# ---- ホスト。sh は応答を取ってきてファイルに書くだけ（外との出入り）。読めなければ理由を渡して、手元の候補だけにする。
# 実行ファイルに渡すホストの結果の渡し方（host_how が checked・unchecked・failed。unchecked の理由は host_why）。

# unchecked <理由> <診断ログの識別子> ホストを見ていないことを渡す。
unchecked() {
	host_how=unchecked
	host_why="$1"
	log_info ホストを見ていない -- "kind=$kind" "reason=$2"
}

# API が失敗したら、どの呼び出しかと、ホストの返事を覚えておく（`$( )` の中から呼ばれることがあるのでファイルに書く）。
# 返事から HTTP の番号と本文を読むのは実行ファイル（`branches ... failed`）。
host_api_failed() {
	printf '%s %s' "$1" "$2" >"$host_err"
	printf '%s' "$3" >"$host_err.text"
}

# 応答を取ってきて $result に書く。<github|gitlab>_<issue|mr>。開いた MR の一覧が多すぎれば 2、API が失敗したら 1。
github_mr() {
	ccnavi_host_api GET "repos/$ccnavi_h_path/pulls/$number" >"$result"
}

# GitHub: 開いた PR の一覧。その issue を参照しているか（タイトル・本文の `#<番号>` か `issues/<番号>`、元ブランチ名に含む番号）は
# 実行ファイルが選ぶ。
github_issue() {
	ccnavi_host_pages "repos/$ccnavi_h_path/pulls?state=open" >"$result"
}

# GitLab: MR 1 本。フォークから出た MR（元ブランチがこのプロジェクトに無い）かは、プロジェクトの id と比べて実行ファイルが決める。
gitlab_mr() {
	ccnavi_host_api GET "projects/$(ccnavi_host_encoded_path)/merge_requests/$number" >"$result"
}

# GitLab: その issue に関係する MR（related_merge_requests。本文で参照した MR を含む）。開いたものだけを選ぶのは実行ファイル。
gitlab_issue() {
	ccnavi_host_pages "projects/$(ccnavi_host_encoded_path)/issues/$number/related_merge_requests" >"$result"
}

look_at_host() {
	origin=$(git remote get-url origin 2>/dev/null || :)
	if [ -z "$origin" ]; then
		unchecked "origin がありません" no-origin
		return 0
	fi
	origin_shown=$(ccnavi_mask_url "$origin")
	if ! ccnavi_host_parse "$origin"; then
		unchecked "origin の URL を読めない（${origin_shown}）${ccnavi_h_parse_why:+。$ccnavi_h_parse_why}" origin-unreadable
		return 0
	fi
	connect_rc=0
	ccnavi_host_connect || connect_rc=$?
	case "$connect_rc" in
	0) ;;
	3)
		unchecked "jq がありません" no-jq
		return 0
		;;
	4)
		unchecked "$ccnavi_h_cli_name が $ccnavi_h_host で利用できず（未導入か未認証）、curl に付ける $ccnavi_h_token_name もありません" no-transport-token
		return 0
		;;
	*)
		unchecked "$ccnavi_h_cli_name が $ccnavi_h_host で利用できず、curl もありません" no-transport
		return 0
		;;
	esac
	ccnavi_h_tmp="$state"
	ccnavi_h_on_fail=host_api_failed
	# 待ちすぎない。候補を出すための読み取りで、作業の前に打つものなので。
	ccnavi_h_max_time="${CCNAVI_BRANCHES_HOST_TIMEOUT:-30}"
	pid=-
	look_rc=0
	if [ "$ccnavi_h_kind" = gitlab ]; then
		pid=$(ccnavi_host_project_id) || look_rc=1
	fi
	[ "$look_rc" -ne 0 ] || "${ccnavi_h_kind}_$kind" || look_rc=$?
	case "$look_rc" in
	0) host_how=checked ;;
	2) unchecked "開いた MR が多すぎて読み切れない（2000 本まで）" too-many-pages ;;
	*)
		host_how=failed
		log_info ホストを見ていない -- "kind=$kind" "reason=api-failed"
		;;
	esac
	return 0
}

host_how=""
host_why=""
look_at_host

# ---- 手元の候補と合わせて出す（実行ファイル）。
case "$host_how" in
checked) set -- branches "$kind" "$number" checked "$ccnavi_h_kind" "$ccnavi_h_host" "$ccnavi_h_path" "$pid" --result "$result" ;;
failed) set -- branches "$kind" "$number" failed "$(cat "$host_err" 2>/dev/null || :)" --result "$host_err.text" ;;
*) set -- branches "$kind" "$number" unchecked "$host_why" ;;
esac
[ -z "$json" ] || set -- "$@" --json
ccnavi "$@" || fail exe-failed "実行ファイルが候補を出せなかった（上の文面を見てください）。" 1
log_info 候補を出した -- "kind=$kind"

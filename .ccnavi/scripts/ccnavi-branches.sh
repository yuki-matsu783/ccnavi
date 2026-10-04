#!/bin/sh
# ccnavi-branches issue・MR に紐づくブランチを探す。読むだけで、作業ツリーにも git にも書かない。
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
# 役割の分け方（docs/claude/exe-boundary.md）:
#   - ホスト（GitHub / GitLab）はこの sh が読む。MR 指定なら、その MR の元ブランチ。issue 指定なら、
#     その issue を参照している開いた MR の元ブランチ。繋ぎ方は ccnavi-common.sh の「ホスト（GitHub /
#     GitLab）への接続」（ccnavi-review.sh と同じ。gh / glab か、curl と GITHUB_TOKEN / GITLAB_TOKEN）
#   - 読んだ結果を JSON（形は ccnavi.md の 9.13）に書き、実行ファイルの
#     `ccnavi branches <issue|mr> <番号> --result <json>` に渡す。手元の候補（名前に番号を含むブランチ・
#     ワークツリー・`issue:` を持つチケット）は実行ファイルが集め、合わせて 1 候補 1 行（--json なら JSON）で出す
#   - ホストに繋げないときは止めない。理由を JSON に書き、実行ファイルが「ホストは見ていない」と言って
#     手元の候補だけを出す
#
# 終了コード: 0 出した / 1 前提の未充足（git の外・実行ファイルが落ちた）/ 2 引数か環境の誤り

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
  --issue <番号>  名前に番号を含むブランチ（手元と origin）、その issue を参照している開いた MR の元ブランチ、
                  issue: <番号> を持つチケットと、その親のブランチ
  --mr <番号>     その MR の元ブランチ
  各候補には、チェックアウトしているワークツリーと結び付くチケットを添える。
  ホストに繋げないときは手元の候補だけを出し、「ホストは見ていない」と書く。
  cwd のリポジトリを見る。プロジェクトの issue・MR は projects/<名前>/ に cd してから打つ。
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
		fail unknown-arg "知らない引数です（$1）。" 2
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
	fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
ccnavi_log_root="$root"
here="$(pwd -W 2>/dev/null || pwd)"
git rev-parse --show-toplevel >/dev/null 2>&1 ||
	fail not-git "cwd（${here}）が git のリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で打ってください。"
state="$root/${CCNAVI_STATE:-logs/state}"
mkdir -p "$state" 2>/dev/null || fail no-state "state の置き場（${state}）を作れません。" 2
result="$state/branches-host-$$.json"
host_err="$state/branches-host-err-$$"
trap 'rm -f "$result" "$host_err"' EXIT

# ---- 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。
if bin=$(ccnavi_bin "$root"); then
	skew=$(ccnavi_compat_skew "$root" "$bin") || printf 'ccnavi-branches: %s\n' "$skew" >&2
	ccnavi() { "$bin" --root "$root" --cwd "$here" "$@"; }
elif [ -f "$root/src/ccnavi/__main__.py" ]; then
	ccnavi() { (cd "$root" && uv run python -m ccnavi --root "$root" --cwd "$here" "$@"); }
else
	fail no-bin "ccnavi の実行ファイルが無い（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ のどれにも無い）。build.py で組み立てるか、scripts/ccnavi-setup.sh で配ってください。" 2
fi

# ---- ホスト。読めなければ理由を書いて、手元の候補だけにする。

# JSON の文字列に入れる表記（`\` と `"` を逃がす）。理由の文面は改行を含まない。
json_text() {
	printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# unchecked <理由> ホストを見ていないことを書く。jq が無くても書けるように printf で書く。
unchecked() {
	printf '{"checked": false, "reason": "%s"}\n' "$(json_text "$1")" >"$result"
	log_info ホストを見ていない -- "kind=$kind" "reason=$2"
}

# API が落ちたら、どの呼び出しかを覚えておく（`$( )` の中から呼ばれるのでファイルに書く）。ホストの返事は書き出さない。
host_api_failed() {
	printf '%s %s' "$1" "$2" >"$host_err"
}

api_failed_reason() {
	what=$(cat "$host_err" 2>/dev/null || :)
	printf 'ホストの API が失敗した（%s）。番号が無いか、権限が足りないか、ホストに届かない' "${what:-?}"
}

# write_checked <mrs の JSON 配列> 見た結果を書く。
write_checked() {
	printf '%s' "$1" | "$ccnavi_h_jq" --arg h "$ccnavi_h_host" --arg p "$ccnavi_h_path" \
		'{checked: true, host: $h, repo: $p, mrs: .}' >"$result"
}

# GitHub: MR（PR）1 本の元ブランチ。
github_mr() {
	got=$(ccnavi_host_api GET "repos/$ccnavi_h_path/pulls/$number") || return 1
	printf '%s' "$got" | "$ccnavi_h_jq" --arg p "$ccnavi_h_path" '
		[{number: .number, branch: .head.ref,
		  state: (if .merged_at then "merged" else .state end),
		  url: .html_url, title: .title,
		  fork: (((.head.repo.full_name // "") | ascii_downcase) != ($p | ascii_downcase))}]'
}

# GitHub: その issue を参照している開いた PR。題・本文の `#<番号>` か `issues/<番号>`、元ブランチの名前の番号で見る。
github_issue() {
	got=$(ccnavi_host_pages "repos/$ccnavi_h_path/pulls?state=open") || return "$?"
	printf '%s' "$got" | "$ccnavi_h_jq" --arg p "$ccnavi_h_path" --arg n "$number" '
		[.[] | select(
			(((.title // "") + " " + (.body // "")) as $t
			 | ($t | test("(^|[^0-9A-Za-z_&/])#" + $n + "([^0-9]|$)"))
			   or ($t | test("issues/" + $n + "([^0-9]|$)")))
			or ((.head.ref // "") | test("(^|[^0-9])" + $n + "([^0-9]|$)")))
		 | {number: .number, branch: .head.ref, state: .state, url: .html_url, title: .title,
		    fork: (((.head.repo.full_name // "") | ascii_downcase) != ($p | ascii_downcase))}]'
}

# GitLab: MR 1 本の元ブランチ。フォークから出た MR は元ブランチがこのプロジェクトに無い。
gitlab_mr() {
	pid=$(ccnavi_host_project_id) || return 1
	got=$(ccnavi_host_api GET "projects/$(ccnavi_host_encoded_path)/merge_requests/$number") || return 1
	printf '%s' "$got" | "$ccnavi_h_jq" --argjson pid "$pid" '
		[{number: .iid, branch: .source_branch, state: .state, url: .web_url, title: .title,
		  fork: (.source_project_id != $pid)}]'
}

# GitLab: その issue に関係する開いた MR（related_merge_requests。本文で参照した MR を含む）。
gitlab_issue() {
	pid=$(ccnavi_host_project_id) || return 1
	got=$(ccnavi_host_pages "projects/$(ccnavi_host_encoded_path)/issues/$number/related_merge_requests") || return "$?"
	printf '%s' "$got" | "$ccnavi_h_jq" --argjson pid "$pid" '
		[.[] | select(.state == "opened")
		 | {number: .iid, branch: .source_branch, state: .state, url: .web_url, title: .title,
		    fork: (.source_project_id != $pid)}]'
}

look_at_host() {
	origin=$(git remote get-url origin 2>/dev/null || :)
	if [ -z "$origin" ]; then
		unchecked "origin が無い" no-origin
		return 0
	fi
	origin_shown=$(ccnavi_mask_url "$origin")
	if ! ccnavi_host_parse "$origin"; then
		unchecked "origin の URL を読めない（${origin_shown}）" origin-unreadable
		return 0
	fi
	connect_rc=0
	ccnavi_host_connect || connect_rc=$?
	case "$connect_rc" in
	0) ;;
	3)
		unchecked "jq が無い" no-jq
		return 0
		;;
	4)
		unchecked "$ccnavi_h_cli_name が $ccnavi_h_host で使えず（未導入か未認証）、curl に付ける $ccnavi_h_token_name も無い" no-transport-token
		return 0
		;;
	*)
		unchecked "$ccnavi_h_cli_name が $ccnavi_h_host で使えず、curl も無い" no-transport
		return 0
		;;
	esac
	ccnavi_h_tmp="$state"
	ccnavi_h_on_fail=host_api_failed
	# 待ちすぎない。候補を出すための読み取りで、作業の前に打つものなので。
	ccnavi_h_max_time="${CCNAVI_BRANCHES_HOST_TIMEOUT:-30}"
	look_rc=0
	mrs=$("${ccnavi_h_kind}_$kind") || look_rc=$?
	case "$look_rc" in
	0) write_checked "$mrs" ;;
	2) unchecked "開いた MR が多すぎて読み切れない（2000 本まで）" too-many-pages ;;
	*) unchecked "$(api_failed_reason)" api-failed ;;
	esac
	return 0
}

look_at_host

# ---- 手元の候補と合わせて出す（実行ファイル）。
set -- branches "$kind" "$number" --result "$result"
[ -z "$json" ] || set -- "$@" --json
ccnavi "$@" || fail exe-failed "実行ファイルが候補を出せなかった（上の文面を見てください。古い実行ファイルは branches を知らないので、組み立て直すか配り直してください）。" 1
log_info 候補を出した -- "kind=$kind"

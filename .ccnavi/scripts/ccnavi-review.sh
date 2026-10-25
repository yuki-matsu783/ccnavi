#!/bin/sh
# ccnavi-review レビューの依頼と確認。親（メインエージェント）だけが呼ぶ。
#
#   sh .ccnavi/scripts/ccnavi-review.sh request --phase <N> --body-file <依頼文> --eli5 <HTML>
#   sh .ccnavi/scripts/ccnavi-review.sh confirm --phase <N>
#   sh .ccnavi/scripts/ccnavi-review.sh comment --body-file <本文>
#   sh .ccnavi/scripts/ccnavi-review.sh decide  <N> --preview （残った指摘の一覧を JSON で見るだけ。ボードも読む）
#   sh .ccnavi/scripts/ccnavi-review.sh fetch                 （取ってきた時点の状態を JSON で見る）
#   sh .ccnavi/scripts/ccnavi-review.sh merged [<番号>]       （MR がマージ済みか。番号で聞くこともできる。ccnavi-sync.sh が使う）
#   sh .ccnavi/scripts/ccnavi-review.sh mr-state <番号>       （MR の今の状態を読むだけ。ボードの「マージ状況を確認」が使う）
#   sh .ccnavi/scripts/ccnavi-review.sh ready [--parent <親>]                  （閉じたチケットを logs/archive/ へ退避し、MR の Draft を外し、子と親のワークツリーと合流済みの子のブランチを片付ける）
#   sh .ccnavi/scripts/ccnavi-review.sh origin                                 （origin をどう読んだか。ホスト・scheme・API の URL）
#
# ユーザだけが打つ操作は、ユーザ専用の sh（ccnavi-user-*.sh）に分けた。エージェントからは hook が名前で止める。
#
#   ccnavi-user-decide.sh <N> [--choices <JSON> --digest <ダイジェスト>]   残った指摘の対応方針を決めて置く
#   ccnavi-user-close-early.sh --reason <理由> [--no-issue]               まだ残っているが早めに閉じる
#
# リモート（GitHub / GitLab）を読み書きするのはこのスクリプトで、ccnavi の実行ファイルは
# ネットワークに出ない。実行ファイルが見るのはワークツリーの中（フェーズ・ブランチ・マーカー）
# だけで、マージリクエストの中身はここが取ってきて JSON で渡す（--result）。ホストを読む関数と write-and-push の支度は
# ccnavi-common-review.sh にあり、ユーザ専用のレビューの sh と共有する。
#
#   request: `ccnavi review prepare` が前提を確かめ、依頼の本文とマージリクエストの
#            下書きを書き出す → ここが（無ければ）マージリクエストを下書きで作り、依頼を投稿する
#            → `ccnavi review requested` がマーカーを置く
#            ユーザはレビューをマージリクエストで行うので、マージリクエストが無いことで止めない。題から Draft を
#            外してマージするのはユーザに任せる。
#            --eli5 <HTML> は必須（付け忘れを後回しにさせない）。変更をやさしく説明した 1 枚の HTML で、
#            このワークツリーの wip/eli5/ の下（既定の名前は wip/eli5/phase-<N>.html）にコミットしておく。
#            HEAD に入って push されていれば、マージリクエストの差分に載る。ここは拡張子・在ること・
#            中身があること・wip/eli5/ の下にあること・名前の字・HEAD で普通のファイル（100644）として
#            HEAD と同じ中身でコミット済みであることを確かめ、
#            実行ファイルには渡さない。投稿が済んだら、ユーザが打つ
#            `crit review <パス>` と `crit push <番号>` を標準出力に出す。crit push で送られた指摘は
#            マージリクエストの行のスレッドになり、confirm が未解決として数え、ccnavi-user-decide.sh で選べる。
#   confirm: ここがスレッドとレビューを取ってくる → `ccnavi review confirm` が判定してマーカーを置き、
#            レビュー待ち（wip/proposals/review/）の子を .ccnavi/approved/done/ へ動かす。
#            トークンの持ち主（GitHub は login、GitLab は username）を引けたら --actor で渡し、マーカーに残す。
#            actor は記録で、判定には使わない。引けなければ渡さず、マーカーは前と同じ。
#            ユーザが子を手で done/ に動かして commit・push 済みなら、移動の前に付いた未解決の指摘は
#            ホストで解決せず、版つきで phases/<親>/accepted.json（受け入れ台帳）に足して確定する
#            （マーカーに via: manual と moved）。台帳に受け入れた指摘は、版と最後のコメントの id が同じ間は数えない。
#            移動の後に付いた指摘が残れば確定しない。変更要求は保留
#   decide <N> --preview:
#            ここが取ってくる → `ccnavi --reviewed N --accept-unresolved --preview` が見せる一覧を JSON で返す。
#            何も置かない（見るだけ）。選んだものを置くのは ccnavi-user-decide.sh
#
# 取り込み済みの親子のチケット（origin があり、write-and-push の対象に入るもの。マージリクエストで見るフェーズが無いものを除く）では、状態を書く
# request（マーカー）・confirm・ready を write-and-push で回す
# （Chrome 拡張から見える親子のチケットに未 push の状態を溜めないため）。
# ロック → 途中の操作の確認 → hook のマーカーを先にコミット → 取り込み → 未送信の確かめを済ませてから
# ホストに触り、実行ファイルが書いたパスだけを commit --only して push する。送れなければ戻す。
# それ以外は今のまま。
#
# 親のワークツリーの中で実行すること。どの親かは cwd から引く。ready だけは `--parent <親>` で
# 外（ワークスペースルートなど）からも打てる（ほかのサブコマンドに --parent を渡すと拒否する）。
# request・confirm を統合先のブランチの上（ワークスペースルートなど）で打つと、親を決められないので
# 「親のワークツリーの中で打ってください」と止める。外から打てば、Draft を外したあとに親のワークツリーも消える。
# 終了コード: 0 成功 / 1 前提の未充足 / 2 引数か環境の誤り /
#             3 merged だけ: MR がマージ済みかを確かめられなかった（"unknown" を出す。推定せずに止める）
#
# mr-state <番号>: 読むだけ。何も書かず、実行ファイルも起こさない。標準出力は 2 行で、機械が読む。
# 区切りは空白 1 つ。1 行目は版 `mr-state 1`、2 行目は次のどれか。
#   open <番号> <yes|no> <URL>        開いている（3 つめは Draft か）
#   merged <番号> <URL>               マージ済み
#   closed <番号> <URL>               マージせずに閉じた
#   unknown <種類> <理由>             確かめられなかった。種類は no-tool（jq・gh/glab・curl が無い）/
#                                     auth（トークンが無い、認証に失敗した）/ network（ホストに届かない）/
#                                     api（API の失敗、答えを読めなかった）/ not-found（MR・リポジトリ・origin が無い）。
#                                     理由は 1 行の日本語で、資格情報は含まない
# 終了コードは、状態が分かれば 0、unknown なら 1、引数の誤りは 2（標準出力は空で、標準エラーに理由）。
# 番号は 1 から始まる数字だけ。merged 副命令の出力と終了コード（3）は変えない。

set -eu

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-review.sh <request|confirm|comment|decide|ready|fetch|origin|merged|mr-state> [--phase <N>] [--body-file <path>] [--eli5 <html>]

  request      --phase <N> --body-file <依頼文> --eli5 <HTML>
                                                  前提を確かめ、無ければマージリクエストを作り、依頼を投稿してマーカーを置く。
                                                  --eli5 は必須。変更の目的・何が変わるか・リスクを専門用語なしで書いた
                                                  1 枚の HTML（.html / .htm）。このワークツリーの wip/eli5/ の下（既定の名前は
                                                  wip/eli5/phase-<N>.html）に置いてコミットし、push しておく（マージリクエストの
                                                  差分に載せる）。相対パスは実行した場所から。投稿が済むと、ユーザが実行する
                                                  crit review <パス> と crit push <番号> を出す。--body-file が
                                                  ワークスペースルート直下の scratchpad/ の中のファイルなら、成功のあとに
                                                  request 自身が消す（待ち中は rm を打てないため。それ以外の場所のファイルは
                                                  消さない。消せなければ警告だけで、終了コードは変えない）
  confirm      --phase <N>                        未解決（Unresolved）のスレッドが 1 つも無ければマーカーを置く（未解決のスレッドは依頼の時刻に関わらずすべて数える。手で done/ に動かして共有した子は、移動の前の指摘を台帳 accepted.json に版つきで受け入れて確定する）
  comment      --body-file <本文>                 判断の記録をマージリクエストのコメントに書き出す
  decide       <N> --preview                      未解決（Unresolved）の指摘と、選べる対応方針の一覧を JSON で見るだけ。何も置かない
  ready        [--parent <親>]                    閉じられて push 済みなら、クローズ済みのチケットを logs/archive/ へ退避し wip/ の追跡済みのファイルを削除して削除を push し、Draft を解除する（「マージに進んでよい」の合図。マージはユーザが squash で）。最後に親のワークツリーを削除します（中から実行したときは消さず、外から実行する 1 本を出す）。--parent なら親のワークツリーの外から実行できる
  fetch                                           リモートから取ってきた時点の状態を JSON で標準出力へ
  origin                                          origin をどう読んだか（ホスト・scheme・API の URL）
  merged [<番号>]                                 マージリクエスト（番号で指定、省けばいまのブランチのもの）がマージ済みなら "merged <番号> <先頭の sha> <元ブランチ>"、無ければ "none"、確かめられなければ "unknown"（終了コード 3。ccnavi-sync.sh が閉じたかを確かめる）
  mr-state <番号>                                 マージリクエストの今の状態を読むだけ。1 行目 "mr-state 1"、2 行目は "open <番号> <yes|no> <URL>"（yes は Draft）・"merged <番号> <URL>"・"closed <番号> <URL>"・"unknown <種類> <理由>"（終了コード 1。種類は no-tool / auth / network / api / not-found）。ボードの「マージ状況を確認」が使う

ユーザがターミナルで実行する操作は別の sh:
  ccnavi-user-decide.sh <N>          指摘の対応方針を選んで置く（対応しない・このフェーズで直す・issue に回す）
  ccnavi-user-close-early.sh --reason <理由> [--no-issue]
                                     まだ残っているが早めに閉じる。残りを issue に書き出す。Draft は親が ready で外す

gh / glab があればそれを使う。無ければ curl と GITLAB_TOKEN / GITHUB_TOKEN。jq が必要です。
USAGE
}

# 共通部分。ワークスペースルートの探し方と、URL の資格情報を伏せる処理はここにある。
. "$(dirname "$0")/ccnavi-common.sh"
# レビューの sh が共有する、ホストを読む側の部品。
review_label=ccnavi-review
ccnavi_load ccnavi-common-review.sh

[ "$#" -ge 1 ] || {
	usage
	exit 2
}
sub="$1"
shift
# 診断ログに残す引数の数（サブコマンドを除いた、読む前の数）。
args_n=$#
case "$sub" in
request | confirm | comment | decide | ready | fetch | origin | merged | mr-state) ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	fail unknown-sub "$sub は指定できません。指定できるのは request / confirm / comment / decide / ready / fetch / origin / merged / mr-state です。" 2
	;;
esac

# ---- --parent は ready だけ。ほかのサブコマンドでは親を cwd から引くので、渡されても効かない。黙って読み飛ばさず断る。
if [ "$sub" != ready ]; then
	for parent_arg in "$@"; do
		case "$parent_arg" in
		--parent | --parent=*)
			fail parent-only-ready "--parent は ready だけです。$sub は親のワークツリーの中で打ってください（どの親かは cwd から引きます）。" 2
			;;
		esac
	done
fi

# ---- decide は、見るだけの --preview だけを受ける。選んだものを置くのはユーザ専用の ccnavi-user-decide.sh。
if [ "$sub" = decide ]; then
	n="${1:-}"
	case "$n" in
	'' | *[!0-9]*) fail decide-no-phase "decide には <N>（フェーズ番号）が必要です。" 2 ;;
	esac
	# 下書きの名前は実行ファイルが整数で組む（`01` でも `1`）。揃えないと、書いた下書きを拾えない
	n=$(printf '%s' "$n" | sed 's/^0*\([0-9]\)/\1/')
	shift
	preview=0
	for decide_arg in "$@"; do
		case "$decide_arg" in
		--preview) preview=1 ;;
		--json) ;;
		*) fail decide-bad-option "decide は $decide_arg を受けない。" 2 ;;
		esac
	done
	[ "$preview" -eq 1 ] || fail decide-no-preview "decide は <N> --preview だけを受ける。選んだものを置くのは ccnavi-user-decide.sh です。" 2
fi

# ---- mr-state <番号>。MR の今の状態を、機械が読む 2 行で答える（読むだけ）。
#
# 番号は 1 から始まる数字だけ（9 桁まで）。誤りは標準出力に何も出さず、標準エラーに理由を書いて 2 で終わる。
mr_state_number=""
if [ "$sub" = mr-state ]; then
	[ "$#" -eq 1 ] || fail mr-state-args "mr-state には MR の番号を 1 つだけ渡してください。" 2
	case "$1" in
	'' | 0* | *[!0-9]*) fail mr-state-number "mr-state の MR の番号 (${1}) は 1 から始まる数字で渡してください。" 2 ;;
	esac
	[ "${#1}" -le 9 ] || fail mr-state-number "mr-state の MR の番号 (${1}) が大きすぎます。" 2
	mr_state_number="$1"
fi

# 答えの 2 行目を出して終わる。理由は 1 行にそろえる（制御文字を空白にし、空白の連続を 1 つにする）。
# 理由には資格情報を入れない（ホストの返事の本文もコピーせず、決まった言葉と状態コードだけで組む）。
mr_state_emit() {
	printf 'mr-state 1\n%s\n' "$1"
}

mr_state_unknown() {
	mrs_reason=$(printf '%s' "$2" | tr '\001-\037\177' ' ' | sed -e 's/  */ /g' -e 's/^ //' -e 's/ $//')
	[ -n "$mrs_reason" ] || mrs_reason="理由は取れなかった"
	log_info mr-stateを答えた -- "state=unknown" "kind=$1" "exit=1"
	mr_state_emit "unknown $1 $mrs_reason"
	exit 1
}

mr_state_known() {
	log_info mr-stateを答えた -- "state=$1" "exit=0"
	mr_state_emit "$2"
	exit 0
}

# API の失敗の本文（gh / glab / curl の出力）から、種類（mrs_kind）と理由（mrs_why）を決める。
# 本文は理由にコピーしない。ネットワークの失敗を先に見て（curl は 404 などを "error: 404" と書く）、次に状態コード。
mr_state_classify() {
	mrs_low=$(printf '%s' "$1" | tr 'A-Z' 'a-z')
	mrs_code=$(printf '%s' "$mrs_low" | grep -o -E '(http |error: |: )[0-9]{3}( |$)' | head -n 1 | grep -o -E '[0-9]{3}' | head -n 1 || :)
	mrs_with=""
	[ -z "$mrs_code" ] || mrs_with="（HTTP ${mrs_code}）"
	case "$mrs_low" in
	*"could not resolve"* | *"couldn't resolve"* | *"no such host"* | *"failed to connect"* | *"couldn't connect"* | *"connection refused"* | *"connection reset"* | *"timed out"* | *"timeout"* | *"network is unreachable"* | *"dial tcp"* | *"i/o timeout"* | *"curl: (6)"* | *"curl: (7)"* | *"curl: (28)"* | *"curl: (35)"* | *"curl: (52)"* | *"curl: (56)"* | *"curl: (60)"*)
		mrs_kind=network
		mrs_why="ホストに届かない（ネットワークか、ホスト名を引けない）"
		return 0
		;;
	*"rate limit"*)
		mrs_kind=api
		mrs_why="ホストの API の利用上限に達した${mrs_with}"
		return 0
		;;
	esac
	case "$mrs_code" in
	404)
		mrs_kind=not-found
		mrs_why="MR #${mrs_n} が見つからない（番号か、リポジトリへの権限を確かめる）${mrs_with}"
		return 0
		;;
	401 | 403)
		mrs_kind=auth
		mrs_why="認証に失敗した（トークンか gh / glab の認証を確かめる）${mrs_with}"
		return 0
		;;
	esac
	case "$mrs_low" in
	*"not found"*)
		mrs_kind=not-found
		mrs_why="MR #${mrs_n} が見つからない（番号か、リポジトリへの権限を確かめる）"
		;;
	*"bad credentials"* | *"unauthorized"* | *"forbidden"* | *"authentication"*)
		mrs_kind=auth
		mrs_why="認証に失敗した（トークンか gh / glab の認証を確かめる）"
		;;
	*)
		mrs_kind=api
		mrs_why="ホストの API が失敗した${mrs_with}"
		;;
	esac
}

# 失敗の本文を受ける置き場。ccnavi_host_api は部分シェルの中で失敗を知らせるので、ファイルで受け渡す。
mr_state_note() {
	printf '%s' "$3" >"$mrs_err" 2>/dev/null || :
}

# URL を 1 語にそろえる。空・空白や制御文字を含む・読めないときは、ホスト名とパスから組む。
mr_state_url() {
	mrs_url=$(ccnavi_mask_url "$1")
	mrs_clean=$(printf '%s' "$mrs_url" | tr -d '\001-\040\177')
	if [ -z "$mrs_clean" ] || [ "$mrs_clean" != "$mrs_url" ]; then
		if [ "$ccnavi_h_kind" = github ]; then
			mrs_url="$ccnavi_h_scheme://$ccnavi_h_host/$ccnavi_h_path/pull/$mrs_n"
		else
			mrs_url="$ccnavi_h_scheme://$ccnavi_h_host/$ccnavi_h_path/-/merge_requests/$mrs_n"
		fi
	fi
}

mr_state() {
	mrs_n="$1"
	# ホストの返事の読み方は ccnavi-common-host.sh と同じ。ここは失敗を、決まった種類に分けて答えるだけ。
	origin=$(git remote get-url origin 2>/dev/null || :)
	[ -n "$origin" ] || mr_state_unknown not-found "origin がありません"
	mrs_rc=0
	ccnavi_host_parse "$origin" || mrs_rc=$?
	case "$mrs_rc" in
	0) ;;
	4) mr_state_unknown not-found "${ccnavi_h_parse_why}" ;;
	*) mr_state_unknown not-found "origin からホストかプロジェクトのパスを読めない" ;;
	esac
	mrs_rc=0
	ccnavi_host_connect || mrs_rc=$?
	case "$mrs_rc" in
	0) ;;
	3) mr_state_unknown no-tool "jq が見つからない" ;;
	4)
		if [ -n "${ccnavi_h_cli:-}" ]; then
			mr_state_unknown auth "${ccnavi_h_cli_name} が ${ccnavi_h_host} で利用できず（未認証か、ホストに届かない）、${ccnavi_h_token_name} もない"
		fi
		mr_state_unknown auth "${ccnavi_h_cli_name} が無く、${ccnavi_h_token_name} もない"
		;;
	*) mr_state_unknown no-tool "${ccnavi_h_cli_name} も curl も利用できない" ;;
	esac

	# 失敗の本文の置き場。state が書けなければ TMPDIR。終わるときに消す。
	mkdir -p "$state" 2>/dev/null || :
	if [ -d "$state" ] && [ -w "$state" ]; then
		mrs_tmp="$state"
	else
		mrs_tmp="${TMPDIR:-/tmp}"
	fi
	mrs_err="$mrs_tmp/ccnavi-mr-state-err-$$"
	trap 'rm -f "$mrs_err" 2>/dev/null || :' EXIT
	ccnavi_h_tmp="$mrs_tmp"
	ccnavi_h_on_fail=mr_state_note
	ccnavi_h_max_time=30

	if [ "$ccnavi_h_kind" = github ]; then
		mrs_rel="repos/$ccnavi_h_path/pulls/$mrs_n"
		mrs_jq='if type == "object" then "\(.number // "-") \(.state // "-") \(.merged == true or (.merged_at // null) != null) \(.draft == true) \(.html_url // "")" else empty end'
	else
		mrs_rel="projects/$(ccnavi_host_encoded_path)/merge_requests/$mrs_n"
		mrs_jq='if type == "object" then "\(.iid // "-") \(.state // "-") \(.state == "merged") \((.draft // .work_in_progress) == true) \(.web_url // "")" else empty end'
	fi
	mrs_rc=0
	mrs_answer=$(ccnavi_host_api GET "$mrs_rel") || mrs_rc=$?
	if [ "$mrs_rc" -ne 0 ]; then
		mrs_body=$(cat "$mrs_err" 2>/dev/null || :)
		mr_state_classify "$mrs_body"
		mr_state_unknown "$mrs_kind" "$mrs_why"
	fi
	mrs_line=$(printf '%s' "$mrs_answer" | "$ccnavi_h_jq" -r "$mrs_jq" 2>/dev/null) || mrs_line=""
	[ -n "$mrs_line" ] || mr_state_unknown api "ホストの答えを読めなかった"

	# 空白で 5 つに分ける（URL は最後。展開を避けるため、パラメータ展開だけで切る）。
	mrs_number="${mrs_line%% *}"
	mrs_line="${mrs_line#* }"
	mrs_st="${mrs_line%% *}"
	mrs_line="${mrs_line#* }"
	mrs_merged="${mrs_line%% *}"
	mrs_line="${mrs_line#* }"
	mrs_draft="${mrs_line%% *}"
	mrs_raw_url="${mrs_line#* }"
	[ "$mrs_raw_url" != "$mrs_line" ] || mrs_raw_url=""
	[ "$mrs_number" = "$mrs_n" ] || mr_state_unknown api "ホストが別の MR（${mrs_number}）を返した"
	mr_state_url "$mrs_raw_url"
	if [ "$mrs_merged" = true ]; then
		mr_state_known merged "merged $mrs_n $mrs_url"
	fi
	case "$mrs_st" in
	open | opened)
		if [ "$mrs_draft" = true ]; then
			mrs_yes=yes
		else
			mrs_yes=no
		fi
		mr_state_known open "open $mrs_n $mrs_yes $mrs_url"
		;;
	closed) mr_state_known closed "closed $mrs_n $mrs_url" ;;
	locked) mr_state_unknown api "MR #${mrs_n} はマージの途中（locked）" ;;
	*) mr_state_unknown api "ホストが想定外の state（${mrs_st}）を返した" ;;
	esac
}

review_place

if [ "$sub" = mr-state ]; then
	mr_state "$mr_state_number"
fi

# ---- ready --parent <親>。親のワークツリーの外（ワークスペースルートなど）から打つ形。
#
# 親は cwd から引く決まりなので、ここで親のワークツリーへ移ってから先へ進む。git（origin・ブランチ・
# 親の識別子）と実行ファイルの cwd は親のワークツリーになり、中から打ったときと同じに動く。
# 打った場所（here）は覚えておき、最後に親のワークツリーを消すときの cwd に使う（外から打てば消せる）。
# 消す前に打った場所へ戻る（自分の cwd が消す対象の中にあると、Windows では消せない）。
ready_parent=""
ready_from=""
if [ "$sub" = ready ]; then
	# `--parent` は 1 度だけ、値は空でないこと。`--parent=`（空）や `--parent X --parent` を黙って
	# 読み飛ばすと、外から打ったつもりが中から打つ形で進む。
	rp_prev=""
	rp_count=0
	for rp_arg in "$@"; do
		if [ "$rp_prev" = --parent ]; then
			ready_parent="$rp_arg"
			[ -n "$rp_arg" ] || fail ready-parent-missing "ready の --parent には親の識別子が必要です。" 2
			rp_prev=""
			continue
		fi
		case "$rp_arg" in
		--parent=*)
			rp_count=$((rp_count + 1))
			ready_parent="${rp_arg#--parent=}"
			[ -n "$ready_parent" ] || fail ready-parent-missing "ready の --parent には親の識別子が必要です。" 2
			;;
		--parent) rp_count=$((rp_count + 1)) ;;
		esac
		rp_prev="$rp_arg"
	done
	if [ "$rp_prev" = --parent ]; then
		fail ready-parent-missing "ready の --parent には親の識別子が必要です。" 2
	fi
	[ "$rp_count" -le 1 ] || fail ready-parent-twice "ready の --parent は 1 度だけ渡せる。" 2
	if [ -n "$ready_parent" ]; then
		ccnavi_is_ident "$ready_parent" ||
			fail ready-parent-ident "ready の --parent の値（${ready_parent}）は有効な識別子の形式ではありません。" 2
		ready_dir="$root/.claude/worktrees/$ready_parent"
		[ -d "$ready_dir" ] && [ ! -L "$ready_dir" ] && [ -e "$ready_dir/.git" ] ||
			fail ready-parent-tree "親 ${ready_parent} のワークツリー（${ready_dir}）がありません。ready は親のワークツリーで行う。" 2
		ready_from="$(pwd)"
		cd "$ready_dir" || fail ready-parent-cd "親 ${ready_parent} のワークツリー（${ready_dir}）に移れない。" 2
		exe_cwd="$(pwd -W 2>/dev/null || pwd)"
	fi
fi

# ---- 実行ファイル。merged は実行ファイルを起こさない（ホストに聞くだけ）ので、無くても進める。
review_exe || [ "$sub" = merged ] || review_no_bin

# ---- リモート。
review_host

# ---- 統合先。MR のマージ先と比べる。MR は作らない（作るのは ccnavi-start.sh）。

default_branch() {
	# 統合先。ccnavi-fetch.sh がワークツリーの起点に使うのと同じ決め方（origin/HEAD → origin/main・master。
	# ccnavi-common-state.sh の ccnavi_integration）で、cwd のツリーが属するリポジトリについて決める。
	# 決まらなければ当てずっぽうの main にせず、案内を出して止まる。
	db_tree=$(git rev-parse --show-toplevel 2>/dev/null || :)
	db_name=$(ccnavi_integration "${db_tree:-.}") || db_name=""
	[ -n "$db_name" ] || fail no-integration "$(ccnavi_integration_hint "${db_tree:-.}")"
	printf '%s' "$db_name"
}

# 親のワークツリーの外で、親を cwd から引くサブコマンド（request・confirm）を打ったとき、止めて案内する。
#
# 親のワークツリーの外で居るブランチを親の識別子と読むと、統合先（main など）を親と読んでしまい、
# 「親のワークツリーを切り直せ」という見当違いの案内になる。原因は、親のワークツリーの中で打っていないこと。
# 居るブランチが統合先のときだけ止める。統合先が決まらないときは、前どおり進める。
need_parent_tree() {
	[ "$review_in_tree" = yes ] && return 0
	np_tree=$(git rev-parse --show-toplevel 2>/dev/null || :)
	np_name=$(ccnavi_integration "${np_tree:-.}" 2>/dev/null) || np_name=""
	[ -n "$np_name" ] && [ "$np_name" = "$branch" ] || return 0
	np_hint=""
	[ "$sub" != ready ] || np_hint="外から打つなら --parent <親の識別子> を付けます。"
	fail not-in-parent-tree "$sub は親のワークツリー（$root/.claude/worktrees/<親の識別子>）の中で打ってください。いまは統合先のブランチ（${branch}）の上で、親を決められません。親のワークツリーは 'sh $script_dir/ccnavi-git.sh worktree list' で探せます。${np_hint}" 2
}

# ---- 目印で始まる投稿を探す。{url, created_at} を返す（無ければ空）。<番号> <URL> <目印>

find_posted() {
	case "$3" in
	'<!-- ccnavi:'*) ;;
	*) return 0 ;;
	esac
	if [ "$kind" = github ]; then
		found_all=$(pages "repos/$path/issues/$1/comments") || return 1
		printf '%s' "$found_all" | "$JQ" -c --arg m "$3" \
			'[.[] | select((.body // "") | startswith($m))][0] // empty | {url: (.html_url // ""), created_at: (.created_at // ""), author: (.user.login // "")}'
	else
		found_all=$(pages "projects/$(encoded_path)/merge_requests/$1/notes") || return 1
		printf '%s' "$found_all" | "$JQ" -c --arg m "$3" --arg u "$2" \
			'[.[] | select((.body // "") | startswith($m))][0] // empty | {url: ($u + "#note_" + (.id | tostring)), created_at: (.created_at // ""), author: ((.author.id // "") | tostring)}'
	fi
}

# ---- Draft を外す。GitHub は GraphQL で外す（GraphQL が 403 の環境では REST の ccr/ready_for_review を使う）。GitLab は題の "Draft: " を除く。

# 題の頭の Draft の印（`Draft:`・`[Draft]`・`WIP:` など。大文字小文字は問わない）を除く。標準入力の 1 行。
# sed の大文字小文字を区別しない指定（`I`）は GNU だけで、BSD の sed（macOS）では失敗するので、字ごとに書く。
strip_draft() {
	sed -E 's/^[[:space:]]*(\[?([Dd][Rr][Aa][Ff][Tt]|[Ww][Ii][Pp])\]?:?[[:space:]]*)+//'
}

undraft() {
	mr_number="$1"
	if [ "$kind" = github ]; then
		pr=$(api GET "repos/$path/pulls/$mr_number")
		node=$(printf '%s' "$pr" | "$JQ" -r '.node_id // empty')
		[ -n "$node" ] || fail no-node-id "マージリクエスト #$mr_number の node_id を読めない。"
		# 題の "Draft: " は GitLab の流儀で付けたもの。GitHub は Draft をフラグで持つので、フラグを外すときに題からも除く。
		title=$(printf '%s' "$pr" | "$JQ" -r '.title // empty')
		stripped=$(printf '%s' "$title" | strip_draft) || stripped=""
		if [ -n "$stripped" ] && [ "$stripped" != "$title" ]; then
			api PATCH "repos/$path/pulls/$mr_number" "$("$JQ" -n --arg t "$stripped" '{title: $t}')" >/dev/null || return 1
		fi
		query=$("$JQ" -n --arg id "$node" '{
			query: "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{number isDraft}}}",
			variables: {id: $id}}')
		gql_rc=0
		res=$(gql "$query") || gql_rc=$?
		case "$gql_rc" in
		0) printf '%s' "$res" | "$JQ" -r '.data.markPullRequestReadyForReview.pullRequest.isDraft' ;;
		3)
			# GraphQL を使えない環境。Draft を外したあと、実状態（draft）を読んで返す（戻り値の形は GraphQL と同じ true / false）
			rest_rc=0
			api POST "repos/$path/pulls/$mr_number/ccr/ready_for_review" '{}' >/dev/null || rest_rc=$?
			if [ "$rest_rc" -eq 0 ]; then
				pr=$(api GET "repos/$path/pulls/$mr_number") || rest_rc=$?
			fi
			if [ "$rest_rc" -ne 0 ]; then
				rest_fallback_failed "Draft の解除"
				return 1
			fi
			rm -f "${ccnavi_h_tmp:-${TMPDIR:-/tmp}}/ccnavi-gql-why-$$" || :
			printf '%s' "$pr" | "$JQ" -r '.draft // false'
			;;
		*) return 1 ;;
		esac
	else
		title=$(api GET "projects/$(encoded_path)/merge_requests/$mr_number" | "$JQ" -r '.title // empty')
		[ -n "$title" ] || fail no-mr-title "マージリクエスト !$mr_number の題を読めない。"
		stripped=$(printf '%s' "$title" | strip_draft) ||
			fail undraft-title "マージリクエスト !$mr_number の題から Draft を除けなかった。題は変えていない。"
		if [ "$stripped" = "$title" ]; then
			printf 'false\n'
			return 0
		fi
		# 除いた結果が空なら、空の題で更新しない。
		[ -n "$stripped" ] ||
			fail undraft-empty-title "マージリクエスト !$mr_number の題から Draft を除くと空になる。題は変えていない。題を付けてから再実行してください。"
		# squash も有効にする。途中のコミットを既定のブランチに残さない。GitHub は MR ごとに持てない
		# （マージのときにユーザが選ぶ）ので、コメントに書くだけ。
		payload=$("$JQ" -n --arg t "$stripped" '{title: $t, squash: true}')
		api PUT "projects/$(encoded_path)/merge_requests/$mr_number" "$payload" | "$JQ" -r '.draft // .work_in_progress // false'
	fi
}

review_begin "$args_n"
# 抜けるときに exe に渡した JSON を消し、終わりの 1 行を診断ログに残す。終了コードは変えない。
# rm が失敗しても（JSON の置き場の名前がディレクトリ・権限など）、`set -e` がその失敗の値で抜けて
# 元の終了コードを上書きしないよう、失敗を無視してから元の値で抜け直す。
eli5_posted=""
trap 'review_exit=$?; ccnavi_wap_end; rm -f "$result" "$result.re" ${eli5_posted:+"$eli5_posted"} 2>/dev/null || :; log_info 終わった -- "sub=$sub" "exit=$review_exit"; exit "$review_exit"' EXIT
trap 'ccnavi_wap_end; exit 130' INT TERM HUP

review_wap

case "$sub" in
origin)
	# origin をどう読んだか。ヒットしないときに、どこで読み違えたかを見るための出力。
	# URL に埋まった資格情報は伏せる（`ccnavi_mask_url` が作る `origin_shown`）。
	# ここの出力はエージェントの文脈と記録に残る。
	printf 'origin=%s\nkind=%s\nscheme=%s\nhost=%s\npath=%s\napi_base=%s\nbranch=%s\ntransport=%s\n' \
		"$origin_shown" "$kind" "$scheme" "$host" "$path" "$api_base" "$branch" "$transport"
	;;
fetch)
	fetch_all
	printf '\n'
	;;
merged)
	# MR がマージ済みか。ccnavi-sync.sh が、親のブランチがリモートから消えたときに、閉じたか（第一の根拠）を
	# 確かめ、マージ済みなら手元の片付けの条件に使う先頭の sha を取るために聞く。読むだけで、何も書かない。
	# 引数は MR の番号（省けば、いまのブランチを元にした MR を探す）。番号があればその MR だけを聞く。
	# 答えは 3 つで、呼ぶ側は none のときだけ「マージされていない」と読む。
	#   merged <番号> <先頭の sha> <元ブランチ>
	#                               終了コード 0（sha はマージされた MR の元ブランチの先頭、元ブランチはその名前。
	#                               分からなければそれぞれ空）
	#   none                        終了コード 0（マージ済みの MR が無いと分かった）
	#   unknown                     終了コード 3（API が失敗した・答えを読めなかった）。道具やトークンが無ければ、
	#                               上の道具の選び方が 2 で止める
	merged_number="${1:-}"
	case "$merged_number" in
	*[!0-9]*) fail merged-number "merged の MR の番号 (${merged_number}) は数字で渡してください。" 2 ;;
	esac
	found=""
	if [ "$kind" = github ]; then
		owner="${path%%/*}"
		if [ -n "$merged_number" ]; then
			answer=$(api GET "repos/$path/pulls/$merged_number") || {
				printf 'unknown\n'
				exit 3
			}
			found=$(printf '%s' "$answer" | "$JQ" -r 'select(.merged_at != null) | "\(.number) \(.head.sha // "") \(.head.ref // "")"') || {
				printf 'unknown\n'
				exit 3
			}
		else
			answer=$(api GET "repos/$path/pulls?state=closed&head=$(ccnavi_host_encode "$owner:$branch")") || {
				printf 'unknown\n'
				exit 3
			}
			found=$(printf '%s' "$answer" | "$JQ" -r '[.[] | select(.merged_at != null)][0] | select(. != null) | "\(.number) \(.head.sha // "") \(.head.ref // "")"') || {
				printf 'unknown\n'
				exit 3
			}
		fi
	else
		pid=$(project_id) || {
			printf 'unknown\n'
			exit 3
		}
		if [ -n "$merged_number" ]; then
			answer=$(api GET "projects/$(encoded_path)/merge_requests/$merged_number") || {
				printf 'unknown\n'
				exit 3
			}
			found=$(printf '%s' "$answer" | "$JQ" -r --argjson pid "$pid" 'select(.state == "merged" and .source_project_id == $pid) | "\(.iid) \(.sha // "") \(.source_branch // "")"') || {
				printf 'unknown\n'
				exit 3
			}
		else
			# 全ページを読んでから絞る（フォークの MR で 1 ページ目が埋まっても見落とさない）
			answer=$(pages "projects/$(encoded_path)/merge_requests?state=merged&source_branch=$(ccnavi_host_encode "$branch")") || {
				printf 'unknown\n'
				exit 3
			}
			found=$(printf '%s' "$answer" | "$JQ" -r --argjson pid "$pid" '[.[] | select(.source_project_id == $pid)][0] | select(. != null) | "\(.iid) \(.sha // "") \(.source_branch // "")"') || {
				printf 'unknown\n'
				exit 3
			}
		fi
	fi
	if [ -n "$found" ]; then
		printf 'merged %s\n' "$found"
	else
		printf 'none\n'
	fi
	;;
confirm)
	# マーカーに残すアカウントはここがホストに聞く。呼び手が渡したもの（--actor）は受けない
	for a in "$@"; do
		case "$a" in
		--actor | --actor=*) fail confirm-actor "confirm は --actor を受けない。マーカーに記すアカウントは、トークンの持ち主をホストに問い合わせて入れる。" 2 ;;
		esac
	done
	# マーカーに残すアカウント。ロックを取る前に引く（引けなくても止めず、マーカーにアカウントを入れない）
	actor=$(account || :)
	log_debug マーカーのアカウント -- "actor=${actor:+set}"
	[ -z "$actor" ] || set -- "$@" "--actor=$actor"
	confirm_phase=""
	confirm_prev=""
	for a in "$@"; do
		case "$a" in
		--phase=*) confirm_phase="${a#--phase=}" ;;
		*) [ "$confirm_prev" = --phase ] && confirm_phase="$a" ;;
		esac
		confirm_prev="$a"
	done
	need_parent_tree
	wap_start "$family"
	fetch_all >"$result"
	wap_ccnavi "$branch のレビュー済みを置いた" -- review confirm "$@" --result "$result"
	# done/ へ動いた子のワークツリーを消す（write-and-push なら送り終えた後。push の失敗で戻ったときに、レビュー待ちの子の
	# ワークツリーを残すため）。消せなかったもの（cwd が中・未コミット）は名指しされるだけで、confirm は成功のまま。
	case "$confirm_phase" in
	'' | *[!0-9]*) ;;
	*) ccnavi worktree tidy "$family" --phase "$confirm_phase" || : ;;
	esac
	;;
request)
	# 段 -1: ELI5 の HTML。--eli5 を抜き出し、残りを実行ファイルへ渡す（実行ファイルは
	# --eli5 を知らない）。ロックを取る前に確かめ、欠けていれば何も書かずに止める。
	eli5=""
	eli5_n=$#
	eli5_i=0
	while [ "$eli5_i" -lt "$eli5_n" ]; do
		eli5_a="$1"
		shift
		eli5_i=$((eli5_i + 1))
		case "$eli5_a" in
		--eli5)
			eli5=""
			if [ "$eli5_i" -lt "$eli5_n" ]; then
				eli5="$1"
				shift
				eli5_i=$((eli5_i + 1))
			fi
			;;
		--eli5=*)
			eli5="${eli5_a#--eli5=}"
			;;
		*) set -- "$@" "$eli5_a" ;;
		esac
	done
	# 置き場は wip/eli5/ の下で、wip/ のほかの場所は ELI5 にさせない。マージリクエストの差分に載せ、
	# ユーザが crit push で行に指摘を送れるようにする。wip/ は ready の前に丸ごと消すので、squash した成果物には残らない。
	eli5_how="依頼の前に、変更の目的・何が変わるか・リスクを専門用語なしで書いた 1 枚の HTML（外部の読み込み無し）を、このワークツリーの wip/eli5/ の下（既定の名前は wip/eli5/phase-<N>.html。名前は英数字と . _ / - だけ）に普通のファイルとして書いてコミットし、push してから --eli5 <パス> で渡してください。"
	[ -n "$eli5" ] || fail explainer-missing "request には --eli5 <HTML> が必要です。${eli5_how}" 2
	case "$eli5" in
	*.[Hh][Tt][Mm][Ll] | *.[Hh][Tt][Mm]) ;;
	*) fail explainer-not-html "--eli5 には拡張子が .html か .htm のファイルを渡してください ($eli5)。${eli5_how}" 2 ;;
	esac
	# 相対パスは --body-file と同じく、スクリプトを打った場所（実行ファイルに渡す --cwd）から。
	case "$eli5" in
	/* | [A-Za-z]:[\\/]*) eli5_path="$eli5" ;;
	*) eli5_path="$here/$eli5" ;;
	esac
	# 欠けているものは全部を挙げてから止める（実行ファイルの前提の検査と同じ流儀）。ロックの前で、何も書かない。
	eli5_unmet=""
	eli5_rel=""
	eli5_top=""
	eli5_prefix=""
	if [ ! -f "$eli5_path" ]; then
		eli5_unmet="${eli5_unmet}
  - ファイルがありません。"
	elif ! grep -q '[^[:space:]]' "$eli5_path" 2>/dev/null; then
		eli5_unmet="${eli5_unmet}
  - 中身が空"
	fi
	# どのリポジトリのどこにあるかは、ファイルのあるディレクトリで git に聞く。打った場所のリポジトリ
	# （依頼を出すブランチ）と同じ根でなければ、マージリクエストの差分には載らない。
	eli5_here_top=$(git rev-parse --show-toplevel 2>/dev/null || :)
	eli5_dir=$(dirname "$eli5_path")
	if [ -d "$eli5_dir" ]; then
		eli5_top=$(cd "$eli5_dir" && git rev-parse --show-toplevel 2>/dev/null) || eli5_top=""
		eli5_prefix=$(cd "$eli5_dir" && git rev-parse --show-prefix 2>/dev/null) || eli5_prefix=""
	fi
	if [ -z "$eli5_top" ] || [ "$eli5_top" != "$eli5_here_top" ]; then
		eli5_unmet="${eli5_unmet}
  - このワークツリーの外にある（マージリクエストの差分に載らない）"
	else
		eli5_rel="${eli5_prefix}${eli5_path##*/}"
		# 名前に使える字を絞る。相対パスは依頼文とユーザが打つ crit の行にそのまま入るので、
		# `'`・`$`・バッククォート・空白・改行・日本語などを通すと、打ったユーザのシェルで別のコマンドになる
		# 文字列を置ける。C ロケールで、許す字を消して末尾の目印（:）だけが残るかで見る（改行も 1 字として残る）。
		eli5_rest=$(printf '%s:' "$eli5_rel" | LC_ALL=C tr -d 'A-Za-z0-9._/-')
		eli5_safe=yes
		if [ "$eli5_rest" != : ]; then
			eli5_safe=""
			eli5_unmet="${eli5_unmet}
  - 名前に使えない字がある（英数字と . _ / - だけにする）"
		fi
		case "$eli5_rel" in
		wip/eli5/*) ;;
		*)
			eli5_safe=""
			eli5_unmet="${eli5_unmet}
  - wip/eli5/ の下にありません。"
			;;
		esac
		# HEAD に入っていて、普通のファイル（モード 100644。リンク・実行ビット付き・submodule は止める）で、
		# 手元の中身が HEAD と同じこと。push 済みかは実行ファイルの前提が見る。
		# ファイルが無いときは「無い」だけを言う（HEAD に無いのは言うまでもない）。
		if [ -n "$eli5_safe" ] && [ -f "$eli5_path" ]; then
			eli5_mode=$(git -C "$eli5_top" ls-tree HEAD -- "$eli5_rel" 2>/dev/null | cut -d ' ' -f 1)
			if [ -z "$eli5_mode" ]; then
				eli5_unmet="${eli5_unmet}
  - HEAD にありません（未追跡か、まだコミットしていない）"
			elif [ "$eli5_mode" != 100644 ]; then
				eli5_unmet="${eli5_unmet}
  - HEAD で普通のファイルでない（モード ${eli5_mode}。シンボリックリンクや実行ビット付きは使わない）"
			elif ! git -C "$eli5_top" diff --quiet HEAD -- "$eli5_rel" 2>/dev/null; then
				eli5_unmet="${eli5_unmet}
  - HEAD から変わっている（未コミットの変更がある）"
			fi
		fi
	fi
	[ -z "$eli5_unmet" ] ||
		fail explainer-unmet "ELI5 の HTML ($eli5) が依頼の前提を満たさない:${eli5_unmet}
${eli5_how}"
	log_debug ELI5 を確かめた -- "eli5=set"
	# 段 0: 取り込み済みの親子のチケットなら write-and-push の前半（ロック・取り込み）を先に済ませる。
	need_parent_tree
	wap_start "$family"
	# 段 1: 前提。exe が依頼の本文を書き出す。
	tell_skew
	prepared=$(ccnavi review prepare "$@") || exit $?
	file=$(printf '%s\n' "$prepared" | sed -n 1p)
	# 段 1.5: マージリクエストの確認。無ければ作らずに止める（作るのは ccnavi-start.sh）。
	# 元ブランチがこのツリーの HEAD のブランチと同じで、マージ先が統合先であること。
	mr=$(find_mr)
	[ -n "$mr" ] ||
		fail no-mr-for-request "親ブランチ $branch に対応する open のマージリクエストが $host にありません。依頼は作らずに止めた。マージリクエストは ccnavi-start.sh が作る（既存のものは --mr で採用する）。ユーザに相談してください。"
	mr_source=$(printf '%s' "$mr" | "$JQ" -r '.source // ""')
	mr_target=$(printf '%s' "$mr" | "$JQ" -r '.target // ""')
	[ "$mr_source" = "$branch" ] ||
		fail mr-source-mismatch "マージリクエストの元ブランチ（${mr_source:-不明}）がこのツリーの HEAD のブランチ（${branch}）と違う。依頼は作らずに止めた。"
	mr_integration=$(default_branch) || exit "$?"
	[ "$mr_target" = "$mr_integration" ] ||
		fail mr-target-mismatch "マージリクエストのマージ先（${mr_target:-不明}）が統合先（${mr_integration}）ではない。依頼は作らずに止めた。マージ先を直すか、ユーザに相談してください。"
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	mr=$(printf '%s' "$mr" | "$JQ" -c '{number: .number, url: .url}')
	# 段 2: 投稿。同じ目印（親・フェーズ・キー）の依頼が既にあれば投稿し直さない（打ち直しや write-and-push の
	# やり直しで依頼を二重にしない）。
	request_marker=$(head -n 1 "$file" | tr -d '\r')
	posted=$(find_posted "$number" "$url" "$request_marker") ||
		fail request-posted-unknown "投稿済みの依頼を確かめられなかった（ホストの返事は上に出ている）。二重に投稿しないよう止めた。"
	if [ -n "$posted" ]; then
		printf '同じ依頼は投稿済み（%s）。投稿し直さない\n' "$(printf '%s' "$posted" | "$JQ" -r '.url')"
		# 前の試みが結果を確定できないまま残っていても、ここで送り済みに直す（「引き継ぎ未完了」を残さない）
		ccnavi ops mark-sent request "${branch:-}" "$number" "$request_marker" comment \
			"$(printf '%s' "$posted" | "$JQ" -r '.url' | tr -d '\r')" >/dev/null 2>&1 || :
	else
		# 本文の末尾に、ELI5 の HTML の在りかと crit での見方を 1 行足す。HTML の中身は
		# 載せない（マージリクエストの差分にある）。目印は 1 行目なので、足しても二重投稿の見分けは変わらない。
		eli5_posted="$state/review-request-eli5-$$.md"
		{
			cat "$file"
			printf '\n---\n\nELI5（変更をやさしく説明した HTML）はこのマージリクエストの差分の `%s`。手元のチェックアウトのルートで `crit review %s` を開いてソースの行に指摘を付け、`crit push %s` でここに送ってください（送った指摘は行のスレッドになり、未解決の間はレビュー済みにならない。`crit %s` だけだと描画のプレビューになり、そこで付けたピンは送られない）。\n' \
				"$eli5_rel" "$eli5_rel" "$number" "$eli5_rel"
		} >"$eli5_posted"
		tell_skew
		posted=$(comment_staged request "$request_marker" "$number" "$url" "$eli5_posted") ||
			{
				rm -f "$eli5_posted"
				fail request-post-failed "依頼を投稿できたか確定できなかった（上の案内を見る）。マーカーは置いていない。確かめてから、同じコマンドを打ち直す。"
			}
		rm -f "$eli5_posted"
	fi
	"$JQ" -n --arg host "$kind" --argjson mr "$mr" --argjson posted "$posted" \
		'{host: $host, mr: $mr} + $posted' >"$result"
	# 段 3: マーカー。
	wap_ccnavi "$branch のレビューを依頼した" -- review requested "$@" --result "$result"
	# 段 3.5: 依頼文の後始末。依頼文は request の入力で、依頼した後は要らない。ここからはレビュー待ちで、
	# ccnavi-*.sh を単独で打つ以外のシェルは止まり、エージェントは rm で消せない。だから request 自身が消す。
	# 消すのは、--body-file がワークスペースルート直下の scratchpad/ の中のファイルのときだけ（利用者のファイルは消さない）。
	# 消せなくても依頼は済んでいるので、警告だけで終了コードは変えない。
	request_body=""
	request_prev=""
	for request_a in "$@"; do
		[ "$request_prev" = --body-file ] && request_body="$request_a"
		case "$request_a" in --body-file=*) request_body="${request_a#--body-file=}" ;; esac
		request_prev="$request_a"
	done
	if [ -n "$request_body" ]; then
		case "$request_body" in
		/* | [A-Za-z]:[\\/]*) request_body_path="$request_body" ;;
		*) request_body_path="$here/$request_body" ;;
		esac
		# `.` と `..` を解いてから、大文字小文字を区別せずに scratchpad/ の中かを見る（ASCII の範囲）。
		request_body_abs=$(ccnavi_abs "$request_body_path" | tr '\\' '/' | LC_ALL=C tr 'A-Z' 'a-z') || request_body_abs=""
		request_scratch=$(printf '%s/scratchpad/' "$root" | tr '\\' '/' | LC_ALL=C tr 'A-Z' 'a-z')
		case "$request_body_abs" in
		"$request_scratch"?*)
			if [ -f "$request_body_path" ] || [ -L "$request_body_path" ]; then
				if rm -f "$request_body_path" 2>/dev/null; then
					printf '案内: 依頼文 %s を消した（scratchpad/ の下書き。依頼したあとは不要）\n' "$request_body"
				else
					printf '%s: 依頼文 %s を消せなかった。依頼は済んでいる。次の依頼の --body-file と取り違えないよう、ユーザに消してもらってください。\n' \
						"$review_label" "$request_body" >&2
				fi
			fi
			;;
		esac
	fi
	# 段 4: crit の案内。crit はユーザの手元で打つ道具。
	# crit push は crit の作業場所（打った場所）からの相対で行を送るので、ワークツリーのルートで打たせる。
	# crit・gh・glab が PATH に無くても止めない。案内だけ出す。
	# ツリーの絶対パスはユーザの置き場なので字を絞れない。いつも '…' で包み、中の ' は '\'' に置き換える
	# （打ったユーザのシェルで 1 語のまま、展開されない）。相対パスは上で字を絞ってあるので包まない。
	eli5_shown="'$(printf '%s' "$eli5_top" | sed "s/'/'\\\\''/g")'"
	eli5_rel_shown="$eli5_rel"
	printf 'ELI5 を見る: ユーザがターミナルで cd %s してから crit review %s を実行し、ソースの行に付けた指摘を crit push %s でマージリクエストに送る（描画は crit %s で見られるが、そこで付けたピンは送られない）\n' \
		"$eli5_shown" "$eli5_rel_shown" "$number" "$eli5_rel_shown"
	command -v crit >/dev/null 2>&1 ||
		printf '（この環境の PATH に crit はありません。ユーザの手元のターミナルで実行します）\n'
	if [ "$kind" = github ]; then eli5_forge=gh; else eli5_forge=glab; fi
	command -v "$eli5_forge" >/dev/null 2>&1 ||
		printf '（この環境の PATH に %s はありません。crit push はユーザの手元で認証済みの %s を使う）\n' "$eli5_forge" "$eli5_forge"
	;;
comment)
	body=""
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--body-file)
			body="${2:-}"
			shift 2
			;;
		*) shift ;;
		esac
	done
	[ -n "$body" ] && [ -f "$body" ] || fail no-body-file "comment には --body-file <本文> が必要です。" 2
	mr=$(find_mr)
	[ -z "$mr" ] && fail no-mr "親ブランチ $branch に対応するマージリクエストが $host にありません。"
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	noted="$state/review-comment-$$.md"
	{
		printf '<!-- ccnavi:comment -->\n'
		cat "$body"
	} >"$noted"
	comment_key="@$body"
	tell_skew
	posted=$(comment_staged comment "$comment_key" "$number" "$url" "$noted") ||
		{
			rm -f "$noted"
			fail comment-post-failed "コメントを投稿できたか確定できなかった（上の案内を見る）。確かめてから、同じコマンドを打ち直す。"
		}
	rm -f "$noted"
	printf 'OK: 記録した（%s）\n' "$(printf '%s' "$posted" | "$JQ" -r '.url')"
	;;
decide)
	# 残った指摘の一覧を JSON で返す。何も置かない（ボードとエージェントが読む）。
	# 引数は上で確かめた（--preview だけ。選んだものを置く形は ccnavi-user-decide.sh）。
	fetch_all >"$result"
	ccnavi --reviewed "$n" --accept-unresolved --preview --json --result "$result"
	exit $?
	;;
ready)
	# 親を閉じられる状態なら Draft を外す。exe が条件を確かめてマーカーとコメントの下書きを置き、
	# 閉じた親子のチケット（done/ の親子・phases/<親>/・flows/）を手元の logs/archive/ へ
	# 退避する（git の上では削除）。write-and-push の中なら、その削除をコミットして push してから、ここが外して
	# コメントを投稿する。マージはユーザ（squash。既定のブランチにチケットを残さない）。
	[ -n "$ready_parent" ] || need_parent_tree
	wap_start "$family"
	fetch_all >"$result"
	# 打ち直しの判断（Draft を外し損ねたか）に、MR がまだ Draft かを結果に足す。
	"$JQ" --argjson d "${mr_draft:-false}" '.mr.draft = $d' "$result" >"$result.draft" && mv -f "$result.draft" "$result"
	tell_skew
	# 互換の版が食い違っていても ready は止めない。実行ファイルは条件の確かめ・退避・wip/ の片付けを、
	# sh は push・Draft 外し・コメントを受け持ち、契約（標準出力の形）は前の版と変わらないため。
	# 組み立て直しはエージェントが勝手に回さない決まりなので、誰が何をするかを言う。
	if [ -n "${skew:-}" ]; then
		printf 'ccnavi-review: 食い違いがあっても ready は続ける。エージェントは build.py を実行せず、終わったらユーザに実行ファイルの組み立て直し（または配り直し）を頼む。\n' >&2
	fi
	ccnavi_wap_capture="$state/review-ready-$$.out"
	ready_rc=0
	wap_ccnavi "$branch の Draft を解除するマーカーを置いた" -- review ready --result "$result" || ready_rc=$?
	# 実行ファイルの標準出力は、1 行目が下書きのパス、2 行目が `tree <退避したツリーのルート>`。
	# wip/ から消したものがあれば、続けて `wip-from <消す前の HEAD>` と `wip <パス>`（1 行 1 本）。
	noted=$(sed -n '1p' "$ccnavi_wap_capture" 2>/dev/null || :)
	wip_from=$(sed -n 's/^wip-from //p' "$ccnavi_wap_capture" 2>/dev/null | head -n 1)
	wip_gone=$(sed -n 's/^wip //p' "$ccnavi_wap_capture" 2>/dev/null || :)
	rm -f "$ccnavi_wap_capture"
	ccnavi_wap_capture=""
	[ "$ready_rc" -eq 0 ] || exit "$ready_rc"
	if [ -n "$wip_gone" ]; then
		printf 'wip/ の追跡済みのファイルを削除しました（%s 件。削除する前のコミット %s から戻せる）:\n' \
			"$(printf '%s\n' "$wip_gone" | wc -l | tr -d ' ')" "$(printf '%.12s' "$wip_from")"
		printf '%s\n' "$wip_gone" | sed 's/^/  /'
	fi
	number=$(printf '%s' "$(cat "$result")" | "$JQ" '.mr.number')
	url=$(printf '%s' "$(cat "$result")" | "$JQ" -r '.mr.url')
	still=$(undraft "$number") || still=""
	[ "$still" = "false" ] || fail undraft-failed "Draft を解除できませんでした（${url}）。ホストの返事は上に出ている。"
	if [ -n "$noted" ] && [ -f "$noted" ]; then
		comment_staged ready ready "$number" "$url" "$noted" >/dev/null ||
			fail ready-comment-failed "Draft の解除は完了。補助のコメントは未完了（${url}）。確かめてから ready を打ち直すと、コメントだけ送り直す。"
		rm -f "$noted"
	fi
	printf 'OK: Draft を解除した（%s）。マージはユーザが行う\n' "$url"
	# 親を閉じる片付け: この親の送り終わった操作記録を消す（未完了のものは残す）
	ccnavi ops retire "${branch:-}" >/dev/null 2>&1 || :
	# 最後に子と親の片付けをする（`worktree retire`）。Draft を外す前には消さない（ready を打ち直せなくなる）。
	# 閉じた子のワークツリー（レビュー待ちだったものも）、子の手元のブランチ（合流した子は条件を確かめて削除。
	# 取り消した子は一覧を出すだけで、ユーザの確認が取れてから消す）、親のワークツリーの順。未コミットの変更や
	# git が無視しているファイルがあるワークツリーは、理由を出して残す。条件の確認も削除も実行ファイルが行う
	# （エージェントに git branch -D は開けない）。打った場所（--parent なら外）を cwd として渡す。中から
	# 打っていれば実行ファイルは消さず、外に出てから打つ 1 本を出す。消せなくても ready は成功のまま。
	if [ -n "$ready_from" ]; then
		cd "$ready_from" || cd "$root"
	fi
	exe_cwd="$here"
	ccnavi worktree retire "$family" || :
	# 親と名前が違うワークツリーや、下書きがあって消されなかった子のワークツリーは、drop では消えない。
	# 一覧を出すだけで消さない（消すかどうかはユーザが決める）。残りが無ければ何も出ない。
	ccnavi worktree remaining "$family" || :
	;;
esac

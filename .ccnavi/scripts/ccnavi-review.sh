#!/bin/sh
# ccnavi-review レビューの依頼と確認。親（メインエージェント）だけが呼ぶ。
#
#   sh .ccnavi/scripts/ccnavi-review.sh review-request --phase <N> --body-file <依頼文>
#   sh .ccnavi/scripts/ccnavi-review.sh confirm --phase <N>
#   sh .ccnavi/scripts/ccnavi-review.sh comment --body-file <本文>
#   sh .ccnavi/scripts/ccnavi-review.sh decide  <N> --preview （残った指摘の一覧を JSON で見るだけ。チケット管理画面も読む）
#   sh .ccnavi/scripts/ccnavi-review.sh fetch                 （取ってきた時点の状態を JSON で見る）
#   sh .ccnavi/scripts/ccnavi-review.sh merged [<番号>]       （MR がマージ済みか。番号で聞くこともできる。ccnavi-sync.sh が使う）
#   sh .ccnavi/scripts/ccnavi-review.sh mr-state <番号>       （MR の今の状態を読むだけ。チケット管理画面の「MR の状況を確認」が使う）
#   sh .ccnavi/scripts/ccnavi-review.sh ready --parent <親>                    （クローズしたチケットを logs/archive/ へ退避し、MR の Draft を外し、子と親のワークツリーと合流済みの子のブランチを片付ける）
#   sh .ccnavi/scripts/ccnavi-review.sh origin                                 （origin をどう読んだか。ホスト・scheme・API の URL）
#
# ユーザだけが打つ操作は、ユーザ専用の sh（ccnavi-user-*.sh）に分けた。エージェントからは hook が名前で止める。
#
#   ccnavi-user-decide.sh <N> [--choices <JSON> --digest <ダイジェスト>]   残った指摘の対応方針を決めて置く
#   ccnavi-user-close-early.sh --parent <親> --reason <理由> [--no-issue] まだ残っているが早めにクローズする
#
# リモート（GitHub / GitLab）を読み書きするのはこのスクリプトで、ccnavi の実行ファイルは
# ネットワークに出ない。実行ファイルが見るのはワークツリーの中（フェーズ・ブランチ・マーカー）
# だけで、マージリクエストの中身はここが取ってきて JSON で渡す（--result）。ホストを読む関数と write-and-push の支度は
# ccnavi-common-review.sh にあり、ユーザ専用のレビューの sh と共有する。
#
#   review-request: `ccnavi review prepare` が前提を確かめ、依頼の本文を書き出す → ここが依頼を
#            マージリクエストに投稿する → `ccnavi review requested` がマーカーを置く
#            マージリクエストは作らない（ccnavi-start.sh が作る）。親のブランチに open のマージリクエストが無ければ、
#            依頼を作らずに止める。タイトルから Draft を外してマージするのはユーザに任せる。
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
# 取り込み済みの親子のチケット（origin があり、write-and-push の対象に入るもの）では、状態を書く
# review-request（マーカー）・confirm・ready を write-and-push で回す
# （Chrome 拡張から見える親子のチケットに未 push の状態を溜めないため）。
# ロック → 途中の操作の確認 → hook のマーカーを先にコミット → 取り込み → 未送信の確かめを済ませてから
# ホストに触り、実行ファイルが書いたパスだけを commit --only して push する。送れなければ戻す。
# それ以外は今のまま。
#
# review-request・confirm・decide は親のワークツリーの中で実行すること。どの親かは cwd から引く。
# ready だけは親のワークツリーの外（ワークスペースルートなど）から `--parent <親>` を付けて打つ
# （ほかのサブコマンドに --parent を渡すと拒否する）。外から打つと、Draft を外したあとに親のワークツリーも消える。
# review-request・confirm・decide を統合先のブランチの上（ワークスペースルートなど）で打つと、
# 親が決まらないので、ホストへ繋ぐ前に「親のワークツリーの中で打ってください」と止める（理由に cwd を示し、`次:` に cd 付きの打ち直しを出す）。
# ready を --parent なしで打つと、ホストへ繋ぐ前に止めて、何も変えず、外から打つ 1 本を `次:` に出す。
# 終了コード: 0 成功 / 1 前提の未充足 / 2 引数か環境の誤り /
#             3 merged だけ: MR がマージ済みかを確かめられなかった（"unknown" を出す。推定せずに止める）
#
# mr-state <番号>: 読むだけ。何も書かない。応答の読みは実行ファイル（`ccnavi host mr-state`）。標準出力は 2 行で、機械が読む。
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
# フォークから出た MR は、状態に関わらず `unknown api`（merged 副命令が none と答えるものを、merged と答えない）。

set -eu

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-review.sh <review-request|confirm|comment|decide|ready|fetch|origin|merged|mr-state> [--phase <N>] [--body-file <path>]

  review-request --phase <N> --body-file <依頼文>
                                                  前提を確かめ、依頼をマージリクエストに投稿してマーカーを置く（マージリクエストが無ければ止まる）。--body-file が
                                                  ワークスペースルート直下の scratchpad/ の中のファイルなら、成功のあとに
                                                  review-request 自身が消す（待ち中は rm を打てないため。それ以外の場所のファイルは
                                                  消さない。消せなければ警告だけで、終了コードは変えない）
                                                  --check を付けると、同じ前提（フェーズの終了・合流・push・MR）を
                                                  確かめるだけで、投稿もマーカーも置かない。0 なら前提は通っている（取り込みとロックは確かめない）
  confirm      --phase <N>                        未解決（Unresolved）のスレッドが 1 つも無ければマーカーを置く（未解決のスレッドは依頼の時刻に関わらずすべて数える。手で done/ に動かして共有した子は、移動の前の指摘を台帳 accepted.json に版つきで受け入れて確定する）
  comment      --body-file <本文>                 判断の記録をマージリクエストのコメントに書き出す
  decide       <N> --preview                      未解決（Unresolved）の指摘と、選べる対応方針の一覧を JSON で見るだけ。何も置かない
  ready        --parent <親>                      クローズして push 済みなら、クローズ済みのチケットを logs/archive/ へ退避し wip/ の追跡済みのファイルを削除して削除を push し、Draft を解除する（「マージに進んでよい」の合図。マージはユーザが squash で）。最後に親のワークツリーを削除します。親のワークツリーの外から打ちます。--parent を付けないと、何もせず外から打つ 1 本を出して止まる
  fetch                                           リモートから取ってきた時点の状態を JSON で標準出力へ
  origin                                          origin をどう読んだか（ホスト・scheme・API の URL）
  merged [<番号>]                                 マージリクエスト（番号で指定、省けばいまのブランチのもの）がマージ済みなら "merged <番号> <先頭の sha> <元ブランチ>"、無ければ "none"、確かめられなければ "unknown"（終了コード 3。ccnavi-sync.sh がクローズしたかを確かめる）
  mr-state <番号>                                 マージリクエストの今の状態を読むだけ。1 行目 "mr-state 1"、2 行目は "open <番号> <yes|no> <URL>"（yes は Draft）・"merged <番号> <URL>"・"closed <番号> <URL>"・"unknown <種類> <理由>"（終了コード 1。種類は no-tool / auth / network / api / not-found）。チケット管理画面の「MR の状況を確認」が使う

ユーザがターミナルで実行する操作は別の sh:
  ccnavi-user-decide.sh <N>          指摘の対応方針を選んで置く（対応しない・このフェーズで直す・issue に回す）
  ccnavi-user-close-early.sh --parent <親> --reason <理由> [--no-issue]
                                     まだ残っているが早めにクローズする。残りを issue に書き出す。Draft は親が ready で外す

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
review-request | confirm | comment | decide | ready | fetch | origin | merged | mr-state) ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	fail unknown-sub "$sub は指定できません。指定できるのは review-request / confirm / comment / decide / ready / fetch / origin / merged / mr-state です。" 2
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

# 失敗の本文を受ける置き場。ccnavi_host_api は部分シェルの中で失敗を知らせるので、ファイルで受け渡す。
mr_state_note() {
	printf '%s' "$3" >"$mrs_err" 2>/dev/null || :
}

mr_state() {
	mrs_n="$1"
	# 接続できないときの種類と理由は sh が決める（接続は外との出入り）。応答の読みは実行ファイル。
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
	trap 'rm -f "$mrs_err" "$mrs_tmp/ccnavi-mr-state-$$" 2>/dev/null || :' EXIT
	ccnavi_h_tmp="$mrs_tmp"
	ccnavi_h_on_fail=mr_state_note
	ccnavi_h_max_time=30

	if [ "$ccnavi_h_kind" = github ]; then
		mrs_rel="repos/$ccnavi_h_path/pulls/$mrs_n"
	else
		mrs_rel="projects/$(ccnavi_host_encoded_path)/merge_requests/$mrs_n"
	fi
	mrs_file="$mrs_tmp/ccnavi-mr-state-$$"
	mrs_how=ok
	ccnavi_host_api GET "$mrs_rel" >"$mrs_file" || {
		mrs_how=failed
		cp "$mrs_err" "$mrs_file" 2>/dev/null || : >"$mrs_file"
	}
	# 応答（失敗なら返事）の読みは実行ファイル。失敗の種類の分け方・URL の組み方・理由の文もそこにある
	# （ホストの返事の本文は理由にコピーしない）。
	mrs_out=$(ccnavi_ask "$root" host mr-state "$ccnavi_h_kind" "$ccnavi_h_scheme" "$ccnavi_h_host" "$ccnavi_h_path" "$mrs_n" \
		"$mrs_how" "$mrs_file") || mr_state_unknown no-tool "ccnavi の実行ファイルが見つからない（scripts/ccnavi-build.sh で組み立てるか、scripts/ccnavi-setup.sh で配置する）"
	rm -f "$mrs_file" || :
	mrs_state=$(ccnavi_val state "$mrs_out")
	mrs_answer=$(ccnavi_val answer "$mrs_out")
	case "$mrs_state" in
	open | merged | closed) mr_state_known "$mrs_state" "$mrs_answer" ;;
	unknown)
		mrs_kind="${mrs_answer#unknown }"
		log_info mr-stateを答えた -- "state=unknown" "kind=${mrs_kind%% *}" "exit=1"
		mr_state_emit "$mrs_answer"
		exit 1
		;;
	esac
	mr_state_unknown no-tool "ccnavi の実行ファイルが答えない（scripts/ccnavi-build.sh で組み立て直す）"
}

review_place

if [ "$sub" = mr-state ]; then
	mr_state "$mr_state_number"
fi

# ---- ready --parent <親>。親のワークツリーの外（ワークスペースルートなど）から打つ形だけ。
#
# 親は cwd から引く決まりなので、ここで親のワークツリーへ移ってから先へ進む。git（origin・ブランチ・
# 親の識別子）と実行ファイルの cwd は親のワークツリーになる。
# 打った場所（here）は覚えておき、最後に親のワークツリーを消すときの cwd に使う。
# 消す前に打った場所へ戻る（自分の cwd が消す対象の中にあると、Windows では消せない）。
# --parent が無ければ、ホストへ繋ぐ前に止めて、外から打つ 1 本を案内する（何も変えない）。
ready_need_parent() {
	review_tree
	rn_parent="<親の識別子>"
	rn_hint="親の識別子は 'sh $script_dir/ccnavi-ticket.sh status' か 'sh $script_dir/ccnavi-git.sh worktree list' で確かめる。"
	if [ "$review_in_tree" = yes ]; then
		rn_parent="$family"
		rn_hint="ready は親のワークツリーの中では打たない（親のワークツリーを消すのは外から）。"
	fi
	fail ready-parent-required "ready は親のワークツリーの外から --parent <親の識別子> を付けて打ってください。いまの cwd（${here}）では何も変えていません。
次: cd $(ccnavi_sh_quote "$root") && sh $(ccnavi_sh_quote "$script_dir/ccnavi-review.sh") ready --parent ${rn_parent}
案内: ${rn_hint}" 2
}
ready_parent=""
ready_from=""
if [ "$sub" = ready ]; then
	review_parent_arg "$@"
	ready_parent="$review_parent"
	[ -n "$ready_parent" ] || ready_need_parent
	review_enter_parent "$ready_parent"
	ready_from="$review_from"
fi

# ---- 実行ファイル。merged は実行ファイルを起こさない（ホストに聞くだけ）ので、無くても進める。
review_exe || [ "$sub" = merged ] || review_no_bin

# 親のワークツリーの外で、親を cwd から引くサブコマンド（review-request・confirm・decide）を打ったとき、
# ホストへ繋ぐ前に止めて案内する。
#
# 親のワークツリーの外で居るブランチを親の識別子と読むと、統合先（main など）を親と読んでしまい、
# 「親のワークツリーを切り直せ」という見当違いの案内になる。ホストは cwd の origin で決まるので、先に繋ぐと
# 親のプロジェクトとは別のホスト（ワークスペース自身の origin）に繋ぎ、別の理由で止まる。原因は、親のワークツリーの中で
# 打っていないことにある。居るブランチが統合先のときだけ止める。統合先が決まらないときは、前どおり進める。
need_parent_tree() {
	review_tree
	[ "$review_in_tree" = yes ] && return 0
	np_tree=$(git rev-parse --show-toplevel 2>/dev/null || :)
	np_name=$(ccnavi_integration "${np_tree:-.}" 2>/dev/null) || np_name=""
	[ -n "$np_name" ] && [ "$np_name" = "$branch" ] || return 0
	np_args=""
	for np_a in "$@"; do
		np_args="$np_args $(ccnavi_sh_quote "$np_a")"
	done
	fail not-in-parent-tree "$sub は親のワークツリー（$root/.claude/worktrees/<親の識別子>）の中で打ってください。いまの cwd（${here}）は統合先のブランチ（${branch}）の上で、親を決められません。
次: cd $(ccnavi_sh_quote "$root/.claude/worktrees/<親の識別子>") && sh $(ccnavi_sh_quote "$script_dir/ccnavi-review.sh") ${sub}${np_args}
案内: 親の識別子は 'sh $script_dir/ccnavi-ticket.sh status' か 'sh $script_dir/ccnavi-git.sh worktree list' で確かめる。--body-file などの相対パスは cd で指す先が変わるので、絶対パスで打ち直す。" 2
}

# ---- 親を cwd から引くサブコマンドは、ホストへ繋ぐ前に cwd を確かめる。
case "$sub" in
review-request | confirm | decide) need_parent_tree "$@" ;;
esac

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

# ---- 目印で始まる投稿を探す。答えは comment と同じ 1 行 1 項目（無ければ空）。読めなければ 1。<番号> <URL> <目印>
#
# 一覧は取ってくるだけで、どれが目印で始まるかと欄の読みは実行ファイル（`ccnavi host found`）。pages は ( ) の中で打つ
# （ページが多すぎて止まるときも、ここで 1 を返して呼び手の文で止める）。
find_posted() {
	case "$3" in
	'<!-- ccnavi:'*) ;;
	*) return 0 ;;
	esac
	fp_file="$state/review-found-$$.json"
	fp_rc=0
	if [ "$kind" = github ]; then
		(pages "repos/$path/issues/$1/comments") >"$fp_file" || fp_rc=$?
	else
		(pages "projects/$(encoded_path)/merge_requests/$1/notes") >"$fp_file" || fp_rc=$?
	fi
	[ "$fp_rc" -ne 0 ] || ccnavi host found "$kind" "$3" "$2" "$fp_file" 2>/dev/null || fp_rc=$?
	rm -f "$fp_file" || :
	return "$fp_rc"
}

# ---- Draft を外す。GitHub は GraphQL で外す（GraphQL が 403 の環境では REST の ccr/ready_for_review を使う）。GitLab はタイトルの "Draft: " を除く。

# タイトルの頭の Draft の印（`Draft:`・`[Draft]`・`WIP:` など）を除いたタイトルと、Draft を外した後にまだ Draft かは、
# 実行ファイル（`ccnavi host undraft title`・`undraft done`）が応答から読む。sh は応答を取ってきてファイルに書き、
# 送る本文を組む（外との出入り）。

# undraft_ask <title|done> <種類か形> <応答のファイル> 実行ファイルの答えを出す。答えなければ空。
undraft_ask() {
	ccnavi host undraft "$1" "$2" "$3" 2>/dev/null | tr -d '\r' || :
}

undraft() {
	mr_number="$1"
	ud_file="$state/review-undraft-$$.json"
	if [ "$kind" = github ]; then
		api GET "repos/$path/pulls/$mr_number" >"$ud_file" || : >"$ud_file"
		ud_title=$(undraft_ask title github "$ud_file")
		node=$(ccnavi_val node "$ud_title")
		[ -n "$node" ] || fail no-node-id "マージリクエスト #$mr_number の node_id を読めない。"
		# タイトルの "Draft: " は GitLab の流儀で付けたもの。GitHub は Draft をフラグで持つので、フラグを外すときにタイトルからも除く。
		if [ "$(ccnavi_val title "$ud_title")" = new ]; then
			stripped=$(ccnavi_val new "$ud_title")
			api PATCH "repos/$path/pulls/$mr_number" "$("$JQ" -n --arg t "$stripped" '{title: $t}')" >/dev/null || return 1
		fi
		query=$("$JQ" -n --arg id "$node" '{
			query: "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{number isDraft}}}",
			variables: {id: $id}}')
		gql_rc=0
		gql "$query" >"$ud_file" || gql_rc=$?
		case "$gql_rc" in
		0) ccnavi_val draft "$(undraft_ask done graphql "$ud_file")" ;;
		3)
			# GraphQL を使えない環境。Draft を外したあと、実状態（draft）を読んで返す（戻り値の形は GraphQL と同じ true / false）
			rest_rc=0
			api POST "repos/$path/pulls/$mr_number/ccr/ready_for_review" '{}' >/dev/null || rest_rc=$?
			if [ "$rest_rc" -eq 0 ]; then
				api GET "repos/$path/pulls/$mr_number" >"$ud_file" || rest_rc=$?
			fi
			if [ "$rest_rc" -ne 0 ]; then
				rm -f "$ud_file" || :
				rest_fallback_failed "Draft の解除"
				return 1
			fi
			rm -f "${ccnavi_h_tmp:-${TMPDIR:-/tmp}}/ccnavi-gql-why-$$" || :
			ccnavi_val draft "$(undraft_ask done rest "$ud_file")"
			;;
		*)
			rm -f "$ud_file" || :
			return 1
			;;
		esac
	else
		api GET "projects/$(encoded_path)/merge_requests/$mr_number" >"$ud_file" || : >"$ud_file"
		ud_title=$(undraft_ask title gitlab "$ud_file")
		case "$(ccnavi_val title "$ud_title")" in
		same)
			rm -f "$ud_file" || :
			printf 'false\n'
			return 0
			;;
		empty)
			# 除いた結果が空なら、空のタイトルで更新しない。
			fail undraft-empty-title "マージリクエスト !$mr_number のタイトルから Draft を除くと空になる。タイトルは変えていない。タイトルを付けてから再実行してください。"
			;;
		new) stripped=$(ccnavi_val new "$ud_title") ;;
		*) fail no-mr-title "マージリクエスト !$mr_number のタイトルを読めない。" ;;
		esac
		# squash も有効にする。途中のコミットを既定のブランチに残さない。GitHub は MR ごとに持てない
		# （マージのときにユーザが選ぶ）ので、コメントに書くだけ。
		payload=$("$JQ" -n --arg t "$stripped" '{title: $t, squash: true}')
		api PUT "projects/$(encoded_path)/merge_requests/$mr_number" "$payload" >"$ud_file" || : >"$ud_file"
		ccnavi_val draft "$(undraft_ask done gitlab "$ud_file")"
	fi
	rm -f "$ud_file" || :
}

review_begin "$args_n"
# 抜けるときに exe に渡した JSON を消し、終わりの 1 行を診断ログに残す。終了コードは変えない。
# rm が失敗しても（JSON の置き場の名前がディレクトリ・権限など）、`set -e` がその失敗の値で抜けて
# 元の終了コードを上書きしないよう、失敗を無視してから元の値で抜け直す。
trap 'review_exit=$?; ccnavi_wap_end; rm -f "$result" "$result.re" 2>/dev/null || :; log_info 終わった -- "sub=$sub" "exit=$review_exit"; exit "$review_exit"' EXIT
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
	# MR がマージ済みか。ccnavi-sync.sh が、親のブランチがリモートから消えたときに、クローズしたか（第一の根拠）を
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
	# sh は応答を取ってくるだけ。マージ済みか（GitLab はこのプロジェクトから出たものだけ）と答えの行は実行ファイル
	# （`ccnavi host merged`）が読む。実行ファイルが無い・答えないときも、確かめられなかったとして unknown。
	merged_file="$state/review-merged-$$.json"
	merged_pid=-
	merged_rc=0
	if [ -z "${bin:-}" ]; then
		merged_rc=1
	elif [ "$kind" = github ]; then
		owner="${path%%/*}"
		if [ -n "$merged_number" ]; then
			api GET "repos/$path/pulls/$merged_number" >"$merged_file" || merged_rc=$?
		else
			api GET "repos/$path/pulls?state=closed&head=$(ccnavi_host_encode "$owner:$branch")" >"$merged_file" || merged_rc=$?
		fi
	else
		merged_pid=$(project_id) || merged_rc=1
		if [ "$merged_rc" -ne 0 ]; then
			:
		elif [ -n "$merged_number" ]; then
			api GET "projects/$(encoded_path)/merge_requests/$merged_number" >"$merged_file" || merged_rc=$?
		else
			# 全ページを読んでから絞る（フォークの MR で 1 ページ目が埋まっても見落とさない）
			# 多すぎて読み切れないときの fail は部分シェルの中で止め、確かめられなかった（unknown）として答える
			(pages "projects/$(encoded_path)/merge_requests?state=merged&source_branch=$(ccnavi_host_encode "$branch")") >"$merged_file" ||
				merged_rc=$?
		fi
	fi
	merged_out=""
	if [ "$merged_rc" -eq 0 ]; then
		merged_out=$(ccnavi host merged "$kind" "${merged_number:--}" "$merged_pid" "$merged_file" 2>/dev/null | tr -d '\r') ||
			merged_out=""
	fi
	rm -f "$merged_file" || :
	case "$merged_out" in
	'merged '* | none) printf '%s\n' "$merged_out" ;;
	*)
		printf 'unknown\n'
		exit 3
		;;
	esac
	;;
confirm)
	# マーカーに残すアカウントはここがホストに聞く。呼び手が渡したもの（--actor）は受けない
	for a in "$@"; do
		case "$a" in
		--actor | --actor=*) fail confirm-actor "confirm は --actor を受けない。マーカーに記すアカウントは、トークンの持ち主をホストに問い合わせて入れる。" 2 ;;
		esac
	done
	# ホストの API・ロック・取り込みより前に、ワークツリーの中で分かる前提（親とフェーズ・依頼の記録・
	# 依頼のあとに動いていないこと）を、何も変えずに断る。検査は本体と同じ関数（読むだけ）。
	# `--check` は位置引数の前に置く。
	ccnavi --check review confirm "$@" >/dev/null || exit $?
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
review-request)
	# --check を抜き出し、残りを実行ファイルへ渡す（--check は下で位置引数の前に付ける）。
	rr_check=""
	rr_n=$#
	rr_i=0
	while [ "$rr_i" -lt "$rr_n" ]; do
		rr_a="$1"
		shift
		rr_i=$((rr_i + 1))
		case "$rr_a" in
		--check) rr_check=yes ;;
		*) set -- "$@" "$rr_a" ;;
		esac
	done
	# ステップ 0: ワークツリーの中で分かる前提（フェーズの終了・合流・未コミット・push・依頼済みなど）は、
	# ロック・取り込みより前に、何も変えずに断る。検査はステップ 1 の prepare と同じ関数（読むだけ）。
	# `--check` は位置引数の前に置く。
	ccnavi --check review prepare "$@" >/dev/null || exit $?
	# 取り込み済みの親子のチケットなら write-and-push の前半（ロック・取り込み）を先に済ませる。
	# --check は読むだけなので、ロックも取り込みもしない。
	[ -n "$rr_check" ] || wap_start "$family"
	# ステップ 1: 前提。exe が依頼の本文を書き出す（取り込みのあとの状態で、ステップ 0 と同じ前提をもう一度通す）。
	# --check は本文を書かず、ステップ 0 で済んでいる。
	file=""
	if [ -z "$rr_check" ]; then
		prepared=$(ccnavi review prepare "$@") || exit $?
		file=$(printf '%s\n' "$prepared" | sed -n 1p)
	fi
	# ステップ 1.5: マージリクエストの確認。無ければ作らずに止める（作るのは ccnavi-start.sh）。
	# 元ブランチがこのツリーの HEAD のブランチと同じで、マージ先が統合先であること。
	find_mr
	[ -n "$mr_number" ] ||
		fail no-mr-for-request "親ブランチ $branch に対応する open のマージリクエストが $host にありません。依頼は作らずに止めた。マージリクエストは ccnavi-start.sh が作る（既存のものは --mr で採用する）。ユーザに相談してください。"
	[ "$mr_source" = "$branch" ] ||
		fail mr-source-mismatch "マージリクエストの元ブランチ（${mr_source:-不明}）がこのツリーの HEAD のブランチ（${branch}）と違う。依頼は作らずに止めた。"
	mr_integration=$(default_branch) || exit "$?"
	[ "$mr_target" = "$mr_integration" ] ||
		fail mr-target-mismatch "マージリクエストのマージ先（${mr_target:-不明}）が統合先（${mr_integration}）ではない。依頼は作らずに止めた。マージ先を直すか、ユーザに相談してください。"
	number="$mr_number"
	url="$mr_url"
	# --check はここまでの前提を全部通ったところで終える。何も書かない（本文も書き出さない）。
	if [ -n "$rr_check" ]; then
		printf '確認: ok（前提は通った。取り込みとロックは確かめていない。MR #%s）\n' "$number"
		exit 0
	fi
	# ステップ 2: 投稿。同じ目印（親・フェーズ・キー）の依頼が既にあれば投稿し直さない（打ち直しや write-and-push の
	# やり直しで依頼を二重にしない）。
	request_marker=$(head -n 1 "$file" | tr -d '\r')
	posted=$(find_posted "$number" "$url" "$request_marker") ||
		fail request-posted-unknown "投稿済みの依頼を確かめられなかった（ホストの返事は上に出ている）。二重に投稿しないよう止めた。"
	if [ -n "$posted" ]; then
		printf '同じ依頼は投稿済み（%s）。投稿し直さない\n' "$(ccnavi_val1 url "$posted")"
		# 前の試みが結果を確定できないまま残っていても、ここで送り済みに直す（「引き継ぎ未完了」を残さない）
		ccnavi ops mark-sent request "${branch:-}" "$number" "$request_marker" comment \
			"$(ccnavi_val1 url "$posted")" >/dev/null 2>&1 || :
	else
		posted=$(comment_staged request "$request_marker" "$number" "$url" "$file") ||
			fail request-post-failed "依頼を投稿できたか確定できなかった（上の案内を見る）。マーカーは置いていない。確かめてから、同じコマンドを打ち直す。"
	fi
	# review requested に渡す結果の JSON は実行ファイルが組む（MR と投稿の URL・作成時刻・作者）
	ccnavi host requested "$kind" "$number" "$url" "$(ccnavi_val1 url "$posted")" \
		"$(ccnavi_val1 created_at "$posted")" "$(ccnavi_val1 author "$posted")" >"$result"
	# ステップ 3: マーカー。
	wap_ccnavi "$branch のレビューを依頼した" -- review requested "$@" --result "$result"
	# ステップ 3.5: 依頼文の後始末。依頼文は review-request の入力で、依頼した後は要らない。ここからはレビュー待ちで、
	# ccnavi-*.sh（前の cd、後ろの | head・| tail は付けてよい）を打つ以外のシェルは止まり、エージェントは rm で消せない。だから review-request 自身が消す。
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
		# scratchpad/ の中かは実行ファイルが比べる（`.` と `..` を解き、大文字小文字を区別しない）。答えなければ消さない。
		request_under=$(ccnavi_val1 under "$(ccnavi_ask "$root" path under "$root/scratchpad" "$request_body_path")") ||
			request_under=""
		case "$request_under" in
		yes)
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
	find_mr
	[ -n "$mr_number" ] || fail no-mr "親ブランチ $branch に対応するマージリクエストが $host にありません。"
	number="$mr_number"
	url="$mr_url"
	noted="$state/review-comment-$$.md"
	{
		printf '<!-- ccnavi:comment -->\n'
		cat "$body"
	} >"$noted"
	comment_key="@$body"
	posted=$(comment_staged comment "$comment_key" "$number" "$url" "$noted") ||
		{
			rm -f "$noted"
			fail comment-post-failed "コメントを投稿できたか確定できなかった（上の案内を見る）。確かめてから、同じコマンドを打ち直す。"
		}
	rm -f "$noted"
	printf 'OK: 記録した（%s）\n' "$(ccnavi_val1 url "$posted")"
	;;
decide)
	# 残った指摘の一覧を JSON で返す。何も置かない（チケット管理画面とエージェントが読む）。
	# 引数は上で確かめた（--preview だけ。選んだものを置く形は ccnavi-user-decide.sh）。
	fetch_all >"$result"
	ccnavi --reviewed "$n" --accept-unresolved --preview --json --result "$result"
	exit $?
	;;
ready)
	# 親をクローズできる状態なら Draft を外す。exe が条件を確かめてマーカーとコメントの下書きを置き、
	# クローズした親子のチケット（done/ の親子・phases/<親>/・flows/）を手元の logs/archive/ へ
	# 退避する（git の上では削除）。write-and-push の中なら、その削除をコミットして push してから、ここが外して
	# コメントを投稿する。マージはユーザ（squash。既定のブランチにチケットを残さない）。
	# ロック・取り込みより前に、ワークツリーの中で分かる前提（親をクローズできるか・未コミット・push 済みか）を、
	# 何も変えずに断る。検査は本体と同じ関数（読むだけ）。`--check` は位置引数の前に置く。
	ccnavi --check review ready >/dev/null || exit $?
	wap_start "$family"
	# 打ち直しの判断（Draft を外し損ねたか）に、MR がまだ Draft かを結果に足す。
	fetch_all draft >"$result"
	ccnavi_wap_capture="$state/review-ready-$$.out"
	ready_rc=0
	wap_ccnavi "$branch の Draft を解除するマーカーを置いた" -- review ready --result "$result" || ready_rc=$?
	# 実行ファイルの標準出力は、1 行目が下書きのパス、2 行目が `tree <退避したツリーのルート>`。
	# wip/ から消したものがあれば、続けて `wip-from <消す前の HEAD>`・`wip <パス>`（1 行 1 本）と、成功のあとに出す報告の
	# `say <文>`。number と url は fetch_all が残したもの。
	noted=$(sed -n '1p' "$ccnavi_wap_capture" 2>/dev/null || :)
	ready_say=$(ccnavi_val say "$(cat "$ccnavi_wap_capture" 2>/dev/null)")
	rm -f "$ccnavi_wap_capture"
	ccnavi_wap_capture=""
	[ "$ready_rc" -eq 0 ] || exit "$ready_rc"
	[ -z "$ready_say" ] || printf '%s\n' "$ready_say"
	still=$(undraft "$number") || still=""
	[ "$still" = "false" ] || fail undraft-failed "Draft を解除できませんでした（${url}）。ホストの返事は上に出ている。"
	if [ -n "$noted" ] && [ -f "$noted" ]; then
		comment_staged ready ready "$number" "$url" "$noted" >/dev/null ||
			fail ready-comment-failed "Draft の解除は完了。補助のコメントは未完了（${url}）。確かめてから ready を打ち直すと、コメントだけ送り直す。"
		rm -f "$noted"
	fi
	printf 'OK: Draft を解除した（%s）。マージはユーザが行う\n' "$url"
	# 親をクローズする片付け: この親の送り終わった操作記録を消す（未完了のものは残す）
	ccnavi ops retire "${branch:-}" >/dev/null 2>&1 || :
	# 最後に子と親の片付けをする（`worktree retire`）。Draft を外す前には消さない（ready を打ち直せなくなる）。
	# 実行ファイルは、まず親のワークツリーを消せるか（cwd・未コミット）を確かめ、
	# 消せなければ子にも触らずに断る。消せるなら、クローズした子のワークツリー（レビュー待ちだったものも）、
	# 子の手元のブランチ（合流した子は条件を確かめて削除。取り消した子は一覧を出すだけで、ユーザの確認が
	# 取れてから消す）、親のワークツリーの順に片付ける。条件の確認も削除も実行ファイルが行う
	# （エージェントに git branch -D は開けない）。打った場所（親のワークツリーの外）を cwd として渡す。
	# 消せなくても ready は成功のまま。
	cd "$ready_from" || cd "$root"
	exe_cwd="$here"
	ccnavi worktree retire "$family" || :
	# 親と名前が違うワークツリーや、下書きがあって消されなかった子のワークツリーは、drop では消えない。
	# 一覧を出すだけで消さない（消すかどうかはユーザが決める）。残りが無ければ何も出ない。
	ccnavi worktree remaining "$family" || :
	# 最後の案内: マージはユーザ。マージされたあとの 1 手は note に置く。
	ccnavi_text "$root" NEXT_READY_DONE
	;;
esac

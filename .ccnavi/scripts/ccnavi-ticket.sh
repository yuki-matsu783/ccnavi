#!/bin/sh
# ccnavi-ticket チケットの状態を動かす（親（メインエージェント）だけが呼ぶ）。状態を聞く（誰でも呼べる）。
#
#   sh .ccnavi/scripts/ccnavi-ticket.sh status      [<親>]
#   sh .ccnavi/scripts/ccnavi-ticket.sh next-id     [<先頭の語> <slug>]
#   sh .ccnavi/scripts/ccnavi-ticket.sh learn      [--note <本文>] [--target <種類>] [--signal <信号>]
#   sh .ccnavi/scripts/ccnavi-ticket.sh approval-request     <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh consolidate <親>
#   sh .ccnavi/scripts/ccnavi-ticket.sh start       <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh finish      <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh cancel      <識別子> --reason <理由>
#   sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>
#
# approval-request は、書いた承認待ちの提案（wip/proposals/todo/）と子のフローの下書き（wip/proposals/flows/）を
# 親のブランチへコミットして push する。別のPCで承認して作業に入るために、承認を頼む前に打つ。
# 提案を書く親のワークツリーで足りる（親子の識別子を渡す）。送るのは親子の提案だけ。サブエージェントからは打てない。
# 親の提案は、送る前に計画の after の形と終端だけを検査し、誤りなら何も送らずに止まる（実行ファイル側の検査）。
# 親のブランチに未 push のコード（承認済みの置き場と提案の置き場の外）の変更があれば、一緒に公開しないよう止める。
# 送れなかったときも、書いた提案はワークツリーに残す（戻すのは自分のコミットだけ）。
#
# consolidate は、親の finish が「判定されていない気づきがある」と止めたときに打つ。処理する統合の子
# （<親>-00-<連番>。phase: 0、maintenance: learning-consolidate）を doing/ に作り、親のブランチへ
# コミットして push する（学びの蓄積が enable のときだけ。開いている統合の子があれば作らない）。
#
# record-risk は、実績のリスクの定性項目（risks.yml の `judge:`）の判定を記録する。判断するのは
# サブエージェント、記録するのは親。判定が揃うまで、その子は finish で閉じられない。
#
# status は読むだけ。承認済みチケットの置き場・承認の時刻・着手・未コミットか未 push か・止まっている理由・
# 次の一手を ccnavi が判断して出す。ネットワークには出ない（push 済みかは手元のリモート追跡の ref で見る）。
# サブエージェントも呼べる。状態を動かすコマンドを出す行には「親（メインエージェント）だけが実行する」と書く。
#
# next-id も読むだけ。直接作業のワークツリー名に付ける次の通し番号（引数なし）か、識別子
# `<先頭の語>-<番号>-<slug>`（先頭の語と slug を渡す）を 1 行で出す。形に合わなければ理由を言って止まる。
# `--lint` の warn を出させて番号を知る回り道の代わり。サブエージェントも呼べる。
# チケット制御が disable でも使える。手元のブランチとワークツリーの名前も数える。全部を数えられなかったときは番号を出さず、
# 終了コード 2 で理由を標準エラーへ出す（標準出力は空。番号を決められないので、ユーザに確かめる）。
#
# 状態は置き場で表し、チケットは 1 本のファイルが置き場を動く。承認待ちは wip/proposals/todo/、承認済みの作業中は
# .ccnavi/approved/doing/、レビュー待ちは wip/proposals/review/、閉じたものは
# .ccnavi/approved/done/。ユーザが動かす向きは .ccnavi/approved/ へ、エージェントが動かす向きは
# wip/proposals/ へ。エージェントの側を動かすのはこのスクリプトだけで、直接ファイルを
# 作ったり動かしたりするのは ccnavi が止める。
# サブエージェントからの start・finish・cancel・approval-request・consolidate・record-risk は ccnavi の hook が止める（判定がサブエージェントの
# 呼び出しを見分ける。実行ファイルとこのスクリプトは呼び手を知らない）。
#
# 本体は ccnavi の `ticket` サブコマンド。ここは実行ファイルを探して渡すだけ。
# 探す順は、環境変数 CCNAVI_ENTRY_PATH が指すもの → ワークスペースルートの dist/ccnavi/ccnavi →
# ソースツリーの `python -m ccnavi`。
#
# start・finish・approval-request・consolidate は、ロックの前に事前確認（ホストの API と git のリモートが使える状態か）を打つ。
#   直接作業では確かめない。標準出力には何も足さず、ok でない項目だけを標準エラーに出す（ticket_preflight）。
#
# 取り込み済みの親子のチケット（origin があり、write-and-push の対象に入るもの）の
# start・finish・cancel・approval-request・consolidate は write-and-push で回す。Chrome 拡張から見える親子のチケットに未 push の状態を溜めないため、次を 1 操作にする。
# ロック → 途中の操作の確認 → hook のマーカーを先にコミット →
# 取り込み（ccnavi-sync.sh）→ 未送信の確かめ → 書く → 書いたパスだけ commit --only → push。push が
# 通るまで完了にしない。送れなければ書いたものを戻す。record-risk のコミットと送信は、その子の finish が行う。
#
# 子の finish は、書く前に親のブランチへ合流する。親のワークツリーで子の先頭を `merge --no-ff --no-commit` で入れ、
# 完了の記録と子の承認済みチケットの merged_sha（合流した子の先頭）を足して、合流と状態を 1 つのマージコミットにして push する。
# 合流の前に、子のワークツリーに未コミットの変更が無いこと、親のワークツリーに状態の置き場以外の未コミットの変更が無いこと、
# 子の変更（親との共通の祖先から先頭まで）に置き場のパスと範囲の外のパスが無いことを確かめる。衝突したら何も変えずに止まる。
# push が通らなければ、合流したコードも含めて元の先頭まで戻す。
# それ以外の親子のチケットは今のまま（書くだけ。コミットと push はエージェント）。
#
# finish・cancel が通ったら、その親子の閉じた子のワークツリーを実行ファイルの `worktree tidy <親>` で
# 片付ける（write-and-push なら送り終えた後）。未コミットの変更があるもの・cwd が中にあるものは消さずに名指しする。
# 片付けが済まなくても finish・cancel の終了コードは変えない。
# 終了コード: 0 成功 / 1 前提の未充足（write-and-push で止めた・送れなかったを含む） / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-ticket.sh status [<親>]
sh .ccnavi/scripts/ccnavi-ticket.sh next-id [<先頭の語> <slug>]
sh .ccnavi/scripts/ccnavi-ticket.sh <start|finish|cancel|approval-request> <識別子> [--reason <理由>] [--check]
  （--check を付けると、書く前の検査だけをして何も変えない。approval-request は何も送らない）
sh .ccnavi/scripts/ccnavi-ticket.sh consolidate <親> [--check]
sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>
sh .ccnavi/scripts/ccnavi-ticket.sh learn [--note <本文>] [--target <種類>] [--signal <信号>]
（親の提案の雛形はこのスクリプトに無い: ccnavi ticket new <識別子> --title <タイトル> --plan <型,…> --issue <値> --mr <番号>）

  status      承認済みチケットの状態を聞く（読むだけ。サブエージェントからも実行できます）。親を省けば
               作業中・レビュー待ち・承認待ちのチケットがある親子を全部。ファイルを読んで推測せず、これを実行する

  next-id      直接作業のワークツリー名に付ける通し番号と識別子を決める（親チケットには使わない。読むだけ。サブエージェントからも実行できます）。引数なしなら
               次の通し番号、<先頭の語> <slug> を渡せば <先頭の語>-<番号>-<slug> を 1 行で出す。
               末尾が -<2 桁> になる slug や、推奨される長さを超える slug を指定すると、理由を表示して終了します。
               親チケットの識別子は issue から決まる（ccnavi-start.sh --issue）。提案の雛形は、実行ファイルの
               `ccnavi ticket new <識別子> --title <タイトル> --plan <型,…> --issue <値> --mr <番号>` が識別子のワークツリーの中で作る
               （このスクリプトに new は無い。手で書かない）

  approval-request      承認待ちの提案（wip/proposals/todo/）と子のフローの下書き（wip/proposals/flows/）を親のブランチへ
               コミットして push する。別のPCで承認できるように、承認を頼む前に打つ（親のワークツリーが必要です）。
               変わっていない提案は、未 push のコミットがあればそれだけを送る。未 push のコードの変更があれば止まる
  consolidate  親の finish が止めた気づきを処理する統合の子を doing/ に作り、親のブランチへ push する。
               打つのは親の finish の学び以外の検査を通ったあと。処理すべきものが無い・開いている統合の子がある
               ときは作らない
  start        .ccnavi/approved/doing/ の承認済みチケットに着手の時刻と基準点を書く。
               ワークツリー .claude/worktrees/<識別子> が必要です。承認済みチケットは置き場を移らない
  finish       doing/ -> wip/proposals/review/（フェーズがレビュー必須）か .ccnavi/approved/done/（不要）。
               完了の時刻を書く。子は実績のリスク（差分）を数えて記録する。子は親のブランチへ合流してから
               push する（合流と状態は 1 つのマージコミット。子の merged_sha に合流した先頭を書く。
               子のワークツリーの未コミットの変更・置き場や範囲の外の変更・衝突があれば、何も変えずに止まる）
  cancel       doing/ -> .ccnavi/approved/done/  --reason が必要です。未承認の提案（todo/）は削除すればよい
  record-risk  定性のリスク項目の判定を記録する（親が実行します。判断はサブエージェント）
  learn        子の気づきの記録の雛形と判定記録の要約を書く（--note なしの形）、または気づき 1 件を記録（直接作業では受信箱）に追加します（--note）。

  レビュー待ち（review/）から done/ へ動かすのはユーザ（ccnavi-review.sh confirm、ccnavi-user-decide.sh、
  ccnavi-user-decide.sh、Chrome 拡張のレビュー済み）。

  提案の plan に書くフェーズ定義は、共通の .ccnavi/config/common/phases.yml と、親の project: が
  指す config の phases.yml の和から選ぶ（自身のレイヤーは .ccnavi/config/project/phases.yml、
  プロジェクトは projects/<名前>/.ccnavi/config/project/phases.yml）。plan のステップには after: [番号] で先に済んでいる
  べきステップを書き（自分より小さい番号だけ）、最後のステップにほかの全部が先行するようにする。
  feedback: のステップも同じで、after はフィードバック計画の番号だけを指す。独立した作業は
  最後に 1 ステップで受けるか、一直線に並べる。計画が使う定義は親の phases: にコピーする
  （ccnavi --plan-order <親> --fill-phases。承認のあとの判定はこのコピーを読む）
USAGE
}

# help は引数の数を数える前に見る。`--help` だけで打たれたときに、使い方を出しながら
# 「引数の誤り」の終了コードを返さないため（ccnavi-review.sh と揃える）。
case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
esac

# status・next-id・learn は引数を省ける（引数 1 個で通す）。
case "${1:-}" in
status | next-id | learn) ;;
*)
	[ "$#" -ge 2 ] || {
		usage
		exit 2
	}
	;;
esac
case "$1" in
status | next-id | start | finish | cancel | approval-request | consolidate | record-risk | learn) ;;
*)
	printf 'ccnavi-ticket: 未対応のサブコマンドです：%s。使用できるサブコマンドは status / next-id / start / finish / cancel / approval-request / consolidate / record-risk / learn です。\n' "$1" >&2
	[ "$1" != new ] || printf '親の提案の雛形は、このスクリプトではなく実行ファイルが作ります: ccnavi ticket new <識別子> --title <タイトル> --plan <型,…> --issue <番号> --mr <番号> [--scope <glob>]（識別子のワークツリーの中で打つ。チケット作業は issue と MR のセット。issue の番号をユーザに聞き（無ければユーザに作ってもらい）、ccnavi-start.sh --issue <番号> でワークツリーと MR を作ると、識別子と --issue・--mr の値が出る）。\n' >&2
	exit 2
	;;
esac

# ワークスペースルート。承認済みチケットと設定と実行ファイルはここにある。
#
# git には聞かない。モード B（projects/ の下に別リポジトリを clone する形）では、
# cwd がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいもの（道具の置き場）とは違う。
root=$(ccnavi_workspace) || {
	printf 'ccnavi-ticket: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 識別子を実行ファイル（argparse）と同じに読む。`--` と `--reason <値>` を読み飛ばした最初の語。
# サブコマンド（1 つ目の語）は読まない。
ticket_id() {
	ti_id=""
	ti_skip=""
	ti_first=yes
	for ti_arg in "$@"; do
		if [ -n "$ti_first" ]; then
			ti_first=""
			continue
		fi
		if [ -n "$ti_skip" ]; then
			ti_skip=""
			continue
		fi
		case "$ti_arg" in
		--reason) ti_skip=yes ;;
		--* | -?*) ;;
		*) [ -n "$ti_id" ] || ti_id="$ti_arg" ;;
		esac
	done
	printf '%s' "$ti_id"
}

# approval-request は、どの道を通っても結果ファイルを 1 つ書く（Stop の AI 判定が、提案を送ったか送らなかったかを
# 確定する）。自分をもう 1 度起こし（CCNAVI_PROPOSE_INNER）、終わった後に外側が書く。中がどこで終わっても
# （止めた・送る前の失敗・出力を切られた）外側は残る。結果は ccnavi 自身の記録で、判定には使わない。
# 道は、中が申し送り（CCNAVI_PROPOSE_NOTE の route=）に書く。書かれていなければ、終了コードで決める。
# --check は確かめるだけで何も送らない。結果ファイルも書かない（送ったかどうかの記録に混ぜない）。
po_check=""
case "$1" in
start | finish | cancel | approval-request | consolidate)
	for po_arg in "$@"; do
		[ "$po_arg" != --check ] || po_check=yes
	done
	;;
esac
if [ "$1" = approval-request ] && [ -z "${CCNAVI_PROPOSE_INNER:-}" ] && [ -z "$po_check" ]; then
	po_id=$(ticket_id "$@")
	po_dir="$root/logs/state/stopops-result"
	po_start=$(date +%s 2>/dev/null || printf 0)
	po_file="$po_dir/$po_start-$$.kv"
	mkdir -p "$po_dir" 2>/dev/null || :
	: >"$po_file.note" 2>/dev/null || :
	po_rc=0
	CCNAVI_PROPOSE_INNER=1 CCNAVI_PROPOSE_NOTE="$po_file.note" sh "$0" "$@" || po_rc=$?
	po_route=$(sed -n 's/^route=//p' "$po_file.note" 2>/dev/null | tail -n 1)
	if [ -z "$po_route" ]; then
		po_route=failed_before
	elif [ "$po_rc" -ne 0 ] && [ "$po_route" = sent ]; then
		po_route=failed_after
	fi
	{
		printf 'id=%s\nroute=%s\nrc=%s\nstarted=%s\nended=%s\n' \
			"$po_id" "$po_route" "$po_rc" "$po_start" "$(date +%s 2>/dev/null || printf 0)"
		grep -v '^route=' "$po_file.note" 2>/dev/null || :
	} >"$po_file.tmp" 2>/dev/null && mv "$po_file.tmp" "$po_file" 2>/dev/null || :
	rm -f "$po_file.note" "$po_file.tmp" 2>/dev/null || :
	exit "$po_rc"
fi

# 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。
if ! ccnavi_wap_use_exe "$root"; then
	printf 'ccnavi-ticket: ccnavi の実行ファイルが見つかりません（CCNAVI_ENTRY_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/・PATH のどれにもありません）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。\n' >&2
	exit 2
fi

here="$(pwd -W 2>/dev/null || pwd)"

# finish・cancel が通った後に、その親子の閉じた（done/ の）子のワークツリーを全部片付ける。ワークツリーが
# あるのは作業中の子だけにする。子の finish・cancel でも、その子に限らず同じ親子の閉じた子をまとめて消す
# （親の finish では取りこぼしを拾う安全網）。レビュー待ち（review/）の子のものは残す（見返す場所。
# そこで積んだコミットは親へ合流しないので、差し戻しは同じフェーズに子を足す。confirm が done/ へ動かした後に消える）。消せなかったもの（cwd が中・未コミット）は名指しされるだけで、
# finish・cancel は成功のまま。
tidy_after() {
	case "$1" in
	finish | cancel) ;;
	*) return 0 ;;
	esac
	ta_id=$(ticket_id "$@")
	ccnavi_is_ident "$ta_id" || return 0
	# 子の識別子のまま渡す。親へ割り出すのは実行ファイル（チケットの `parent:` を読む）。名前の形で
	# 末尾の `-<2 桁>-<2 桁>` を剥がすと、その形で終わる親の識別子（rel-2026-10-06 など）を読み違える。
	ccnavi_wap_exe --cwd "$here" worktree tidy "$ta_id" || :
}

# 子の start で、このPCに子のワークツリーが無ければ、取り込み直後の親ブランチ（write-and-push の親のワークツリーの HEAD）から作る。
# 別のPCで承認された子でも、どのPCでも同じ場所に作る。ネットワークには出ない（git の局所の操作だけ）。
# 子のブランチ（子の識別子の名前）が手元に残っていれば、親の先頭が含むものだけ親の先頭に揃えて使う
# （含まない = 手元だけに積んだ仕事があるので、消さずに止める）。
# ここで止めるのは git の状態（手元だけのコミット・作れない行き先）を見た判定で、ccnavi の状態（チケット・
# マーカー）を読んで操作を止める判定ではない。shに判定を足さない決まり（hookと実行ファイルが判定する）の外になる。
ensure_child_worktree() {
	ew_path="$root/.claude/worktrees/$wap_id"
	if [ -e "$ew_path" ] || [ -L "$ew_path" ]; then
		return 0
	fi
	if git -C "$ccnavi_wap_tree" show-ref --verify --quiet "refs/heads/$wap_id"; then
		if ! git -C "$ccnavi_wap_tree" merge-base --is-ancestor "refs/heads/$wap_id" HEAD 2>/dev/null; then
			printf 'ccnavi-ticket: 手元のブランチ %s に親のブランチ %s に無いコミットがある。子のワークツリーを作らずに止めた。内容を確かめて、親へ取り込むか、ユーザが消してから再実行してください\n' "$wap_id" "$ccnavi_wap_branch" >&2
			log_info 子のワークツリーを作らずに止めた -- "child=$wap_id" "reason=child-branch-ahead"
			return 1
		fi
		git -C "$ccnavi_wap_tree" branch -f "$wap_id" HEAD >/dev/null 2>&1 || {
			printf 'ccnavi-ticket: 手元のブランチ %s を親のブランチの先頭に揃えられなかった\n' "$wap_id" >&2
			log_info 子のワークツリーを作らずに止めた -- "child=$wap_id" "reason=child-branch-reset"
			return 1
		}
		ew_rc=0
		git -C "$ccnavi_wap_tree" worktree add "$ew_path" "$wap_id" >/dev/null 2>"$ccnavi_wap_tmp/ew-err" || ew_rc=$?
	else
		ew_rc=0
		git -C "$ccnavi_wap_tree" worktree add "$ew_path" -b "$wap_id" HEAD >/dev/null 2>"$ccnavi_wap_tmp/ew-err" || ew_rc=$?
	fi
	if [ "$ew_rc" -ne 0 ]; then
		printf 'ccnavi-ticket: 子のワークツリー %s を作れなかった（%s）\n' "$ew_path" "$(head -n 1 "$ccnavi_wap_tmp/ew-err" 2>/dev/null)" >&2
		log_info 子のワークツリーを作れなかった -- "child=$wap_id" "reason=child-worktree-add"
		return 1
	fi
	printf 'ccnavi-ticket: 子のワークツリー .claude/worktrees/%s を親のブランチ %s の先頭から作った\n' "$wap_id" "$ccnavi_wap_branch" >&2
	log_info 子のワークツリーを作った -- "child=$wap_id" "parent=$ccnavi_wap_family_id"
}

# 事前確認（ホストの API と git のリモートが使える状態か）。標準出力には何も足さず、ok でない項目だけを
# 1 項目 1 行と `次:` 1 本で標準エラーに出す。ok なら何も出さない。
# 続けるのは、全体が ok か、ng が無く「api が確かめられず、権限は省略、git が ok」のときだけ（後者は注意を添える）。
# それ以外は何も書かずに止める。事前確認は保証ではない（通ったあとの取り込みや push の関門で止まることはある）。
# approval-request は外側と内側の 2 回起動するので、呼ぶのは内側（ここ）だけ。
# 親のブランチが一度も送られていないために止まったときの次の手（共通関数は事実だけを言う）。
# MR の有無はホストの事柄でエージェントは調べられないので、決めるのはユーザ。
ticket_unsent_parent_next() {
	[ -n "${ccnavi_wap_unsent_parent:-}" ] || return 0
	# 次の手は、手で切った親が最初に打つ approval-request・start のためのもの。ほかの操作は事実の文だけにする
	case "${1:-}" in
	start | approval-request) ;;
	*) return 0 ;;
	esac
	tup_mr="<MR の番号>"
	[ -z "${ccnavi_pi_mr:-}" ] || tup_mr="$ccnavi_pi_mr"
	tup_b="$ccnavi_wap_branch"
	tup_s=$(ccnavi_sh_quote "$ccnavi_wap_sh/ccnavi-start.sh")
	{
		printf '次の手: ユーザに状況を伝えて、どれにするか聞いて返事を待つ。直接実装には切り替えない\n'
		printf '  1. issue に紐づく MR がすでにある: その MR のブランチで作業するかを聞く。作業するなら sh %s --mr %s（--issue <番号> と一緒に渡せる）\n' "$tup_s" "$tup_mr"
		printf '  2. MR は無く、issue はある: %s を使わず sh %s --issue <番号> で新しく切る（ブランチ・ワークツリー・Draft MR を作る）。提案は新しい親のワークツリーへ移す。%s にコミットがあれば先に伝える\n' "$tup_b" "$tup_s" "$tup_b"
		printf '  3. %s を使い続ける: ユーザが git push -u origin %s と MR（説明欄に Closes #<番号>）の作成をしてから、sh %s --mr %s で採用する\n' "$tup_b" "$tup_b" "$tup_s" "$tup_mr"
		ccnavi_text "$root" NOTE_TICKET_NEW_ISSUE
	} >&2
}

ticket_preflight() {
	ccnavi_preflight "${ccnavi_wap_tree:-$here}"
	[ "$ccnavi_pf_result" != ok ] || return 0
	for tp_item in tools origin integration api access git ident; do
		eval "tp_kind=\$ccnavi_pf_$tp_item"
		[ "$tp_kind" != ok ] || { [ "$tp_item" = access ] && [ -n "$ccnavi_pf_access_unknown" ]; } || continue
		ccnavi_pf_line "$tp_item"
		ccnavi_wap_say "確認: $ccnavi_pf_line_text"
	done
	ccnavi_pf_top="${ccnavi_wap_tree:-$here}"
	ccnavi_pf_next
	[ -z "$ccnavi_pf_next_text" ] || ccnavi_wap_say "$ccnavi_pf_next_text"
	if [ "$ccnavi_pf_result" = unverified ] && [ "$ccnavi_pf_api" = unverified ] &&
		[ "$ccnavi_pf_access" = skipped ] && [ "$ccnavi_pf_git" = ok ]; then
		ccnavi_wap_say "注意: ホストの API を確かめられなかったが、続ける。このあとの review-request・confirm・ready はホストの API が要る"
		log_info 事前確認で注意して続ける -- "fix=${ccnavi_pf_fix:-none}" "auth=${ccnavi_pf_auth_source:-none}"
		return 0
	fi
	log_info 事前確認で止めた -- "result=$ccnavi_pf_result" "fix=${ccnavi_pf_fix:-none}" "auth=${ccnavi_pf_auth_source:-none}"
	return 1
}

# write-and-push（親子のチケットの start・finish・cancel）。
case "$1" in
start | finish | cancel | approval-request | consolidate)
	ccnavi_log_root="$root"
	ccnavi_wap_root="$root"
	ccnavi_wap_label=ccnavi-ticket
	ccnavi_wap_sh="$(dirname "$0")"
	trap 'ccnavi_wap_end' EXIT
	trap 'ccnavi_wap_end; exit 130' INT TERM HUP
	# 識別子は実行ファイル（argparse）と同じに読む（ticket_id。`start -- <親>` で write-and-push を経ずに通らないようにする）。
	wap_id=$(ticket_id "$@")
	if ! ccnavi_is_ident "$wap_id"; then
		printf 'ccnavi-ticket: %s には識別子を指定してください（%s は有効な識別子の形式ではありません）。\n' "$1" "${wap_id:-（無い）}" >&2
		exit 2
	fi
	ccnavi_wap_family "$wap_id"
	case "$ccnavi_wap_target" in
	stop)
		ccnavi_wap_note "route=stopped"
		ccnavi_wap_refuse
		exit 1
		;;
	yes)
		case "$1" in
		start) wap_words="$wap_id に着手" ;;
		finish) wap_words="$wap_id の作業を終えた" ;;
		approval-request) wap_words="$wap_id の提案を送る" ;;
		consolidate) wap_words="$wap_id の学びの統合の子を作る" ;;
		*) wap_words="$wap_id を取り消した" ;;
		esac
		# 取り消しは事前確認しない（ホストに届かない・権限が足りないときでも、取り消せるようにする）
		[ "$1" = cancel ] || ticket_preflight || exit 1
		# --check は、ロックも取り込みもせず、書く前の検査だけを実行ファイルで行う。
		if [ -n "$po_check" ]; then
			ccnavi_wap_end
			trap - EXIT INT TERM HUP
			ccnavi_wap_exe ticket "$@" || exit $?
			exit 0
		fi
		# 書く前に分かる条件は、ロック・取り込み・コミット・子のワークツリーの作成より前に、何も変えずに断る。
		# 検査は実行ファイルの本体と同じ関数（読むだけ）。通ったときの標準出力は捨てる。`--check` は位置引数の前に置く。
		# approval-request は上の事前確認の流れで検査が済む（--check と本体が同じ検査を通る）。
		if [ "$1" != approval-request ]; then
			ccnavi_wap_exe --check ticket "$@" >/dev/null || exit $?
		fi
		ccnavi_wap_begin || {
			ticket_unsent_parent_next "$1"
			exit 1
		}
		# 子の finish は、親のブランチへ合流してから push する。合流してよいかをここで確かめる。
		if [ "$1" = finish ] && [ "$wap_id" != "$ccnavi_wap_family_id" ]; then
			ccnavi_wap_merge_plan "$wap_id" || exit 1
		fi
		if [ "$1" = start ] && [ "$wap_id" != "$ccnavi_wap_family_id" ]; then
			ensure_child_worktree || exit 1
		fi
		if [ "$1" = approval-request ]; then
			# 失敗しても、利用者が書いた提案はワークツリーに残す（戻すのは自分のコミットだけ）。
			ccnavi_wap_keep_worktree=yes
		fi
		wap_rc=0
		ccnavi_wap_write "ccnavi: ${wap_words}" -- ticket "$@" || wap_rc=$?
		ccnavi_wap_end
		trap - EXIT INT TERM HUP
		if [ "$wap_rc" -eq 0 ] && [ "$1" = approval-request ]; then
			# 送り終えたあとの案内。出力先が閉じていても、送った結果を失敗にしない（この 1 行の間だけ PIPE を無視する）。
			trap '' PIPE
			ccnavi_text "$root" NEXT_TICKET_PROPOSED 2>/dev/null || :
			trap - PIPE
		fi
		[ "$wap_rc" -ne 0 ] || tidy_after "$@"
		exit "$wap_rc"
		;;
	esac
	ccnavi_wap_end
	trap - EXIT INT TERM HUP
	[ "$1" != approval-request ] || ccnavi_wap_note "route=write_only"
	;;
esac

rc=0
# learn は作業ディレクトリのワークツリーで書き先と識別子が決まるので、cwd を実行ファイルへ渡す
# （ソースで動かす形は cd "$root" してから起こすため、渡さないとワークスペースルートの扱いになる）。
case "$1" in
learn) ccnavi_wap_exe --cwd "$here" ticket "$@" || rc=$? ;;
*) ccnavi_wap_exe ticket "$@" || rc=$? ;;
esac
[ "$rc" -ne 0 ] || tidy_after "$@"
exit "$rc"

#!/bin/sh
# ccnavi-ticket チケットの状態を動かす（親（メインエージェント）だけが呼ぶ）。状態を聞く（誰でも呼べる）。
#
#   sh .ccnavi/scripts/ccnavi-ticket.sh status      [<親>]
#   sh .ccnavi/scripts/ccnavi-ticket.sh next-id     [<先頭の語> <slug>]
#   sh .ccnavi/scripts/ccnavi-ticket.sh learn      [--note <本文>] [--target <種類>] [--signal <信号>]
#   sh .ccnavi/scripts/ccnavi-ticket.sh start       <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh finish      <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh cancel      <識別子> --reason <理由>
#   sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>
#
# record-risk は、実績のリスクの定性項目（risks.yml の `judge:`）の判定を記録する。判断するのは
# サブエージェント、記録するのは親。判定が揃うまで、その子は finish で閉じられない。
#
# status は読むだけ。承認済みチケットの置き場・承認の時刻・着手・未コミットか未 push か・止まっている理由・
# 次の一手を ccnavi が判断して出す。ネットワークには出ない（push 済みかは手元のリモート追跡の ref で見る）。
# サブエージェントも呼べる。状態を動かすコマンドを出す行には「親（メインエージェント）だけが実行する」と書く。
#
# next-id も読むだけ。issue の無い親の提案に使う次の通し番号（引数なし）か、識別子
# `<先頭の語>-<番号>-<slug>`（先頭の語と slug を渡す）を 1 行で出す。形に合わなければ理由を言って止まる。
# `--lint` の warn を出させて番号を知る回り道の代わり。サブエージェントも呼べる。
#
# 状態は置き場で表し、チケットは 1 本のファイルが置き場を動く。承認待ちは wip/proposals/todo/、承認済みの作業中は
# .ccnavi/approved/doing/、レビュー待ちは wip/proposals/review/、閉じたものは
# .ccnavi/approved/done/。ユーザが動かす向きは .ccnavi/approved/ へ、エージェントが動かす向きは
# wip/proposals/ へ。エージェントの側を動かすのはこのスクリプトだけで、直接ファイルを
# 作ったり動かしたりするのは ccnavi が止める。
# サブエージェントからの start・finish・cancel・record-risk は ccnavi の hook が止める（判定がサブエージェントの
# 呼び出しを見分ける。実行ファイルとこのスクリプトは呼び手を知らない）。status は止めない。
#
# 本体は ccnavi の `ticket` サブコマンド。ここは実行ファイルを探して渡すだけ。
# 探す順は、環境変数 CCNAVI_BIN_PATH が指すもの → ワークスペースルートの dist/ccnavi/ccnavi →
# ソースツリーの `python -m ccnavi`。
#
# 取り込み済みの親子のチケット（origin があり、親子のチケットの取り込み状態が present。マージリクエストで見るフェーズが無いものを除く）の
# start・finish・cancel は C1 で回す。Chrome 拡張から見える親子のチケットに未 push の状態を溜めないため、次を 1 操作にする。
# ロック → 途中の操作の確認 → hook のマーカーと状態の履歴を先にコミット →
# 取り込み（ccnavi-sync.sh）→ 未送信の確かめ → 書く → 書いたパスだけ commit --only → push。push が
# 通るまで完了にしない。送れなければ書いたものを戻す。record-risk は C1 にしない（その子の finish がコミットして送る）。
#
# 子の finish は、書く前に親のブランチへ合流する。親のワークツリーで子の先頭を `merge --no-ff --no-commit` で入れ、
# 完了の記録と子の承認済みチケットの merged_sha（合流した子の先頭）を足して、合流と状態を 1 つのマージコミットにして push する。
# 合流の前に、子のワークツリーに未コミットの変更が無いこと、親のワークツリーに状態の置き場以外の未コミットの変更が無いこと、
# 子の変更（親との共通の祖先から先頭まで）に置き場のパスと範囲の外のパスが無いことを確かめる。衝突したら何も変えずに止まる。
# push が通らなければ、合流したコードも含めて元の先頭まで戻す。cancel は合流しない。
# それ以外の親子のチケットは今のまま（書くだけ。コミットと push はエージェント）。
#
# finish・cancel が通ったら、その親子の閉じた子のワークツリーを実行ファイルの `worktree tidy <親>` で
# 片付ける（C1 なら送り終えた後）。未コミットの変更があるもの・cwd が中にあるものは消さずに名指しする。
# 片付けが済まなくても finish・cancel の終了コードは変えない。
# 終了コード: 0 成功 / 1 前提の未充足（C1 で止めた・送れなかったを含む） / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-ticket.sh status [<親>]
sh .ccnavi/scripts/ccnavi-ticket.sh next-id [<先頭の語> <slug>]
sh .ccnavi/scripts/ccnavi-ticket.sh <start|finish|cancel> <識別子> [--reason <理由>]
sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>
sh .ccnavi/scripts/ccnavi-ticket.sh learn [--note <本文>] [--target <種類>] [--signal <信号>]

  status       承認済みチケットの状態を聞く（読むだけ。サブエージェントからも実行できます）。親を省けば
               作業中・レビュー待ち・承認待ちのチケットがある親子を全部。ファイルを読んで推測せず、これを実行する

  next-id      issue の無い親の提案に使う識別子を決める（読むだけ。サブエージェントからも実行できます）。引数なしなら
               次の通し番号、<先頭の語> <slug> を渡せば <先頭の語>-<番号>-<slug> を 1 行で出す。
               末尾が -<2 桁> になる slug や、推奨される長さを超える slug を指定すると、理由を表示して終了します。

  start        .ccnavi/approved/doing/ の承認済みチケットに着手の時刻と基準点を書く。
               ワークツリー .claude/worktrees/<識別子> が必要です。承認済みチケットは置き場を移らない
  finish       doing/ -> wip/proposals/review/（フェーズがレビュー必須）か .ccnavi/approved/done/（不要）。
               完了の時刻を書く。子は実績のリスク（差分）を数えて記録する。子は親のブランチへ合流してから
               push する（合流と状態は 1 つのマージコミット。子の merged_sha に合流した先頭を書く。
               子のワークツリーの未コミットの変更・置き場や範囲の外の変更・衝突があれば、何も変えずに止まる）
  cancel       doing/ -> .ccnavi/approved/done/  --reason が必要です。未承認の提案（todo/）は削除すればよい
  record-risk  定性のリスク項目の判定を記録する（親が実行します。判断はサブエージェント）
  learn        子の気づきの記録の雛形と判定記録の要約を書く（--note なしの形）、または気づき 1 件を記録（直接作業では受信箱）に追加します（--note）。

  レビュー待ち（review/）から done/ へ動かすのはユーザ（ccnavi-review.sh confirm / decide、
  ccnavi --reviewed）。

  提案の plan に書くフェーズ定義は、共通の .ccnavi/config/common/phases.yml と、親の project: が
  指す config の phases.yml の和から選ぶ（自身のレイヤーは .ccnavi/config/project/phases.yml、
  プロジェクトは projects/<名前>/.ccnavi/config/project/phases.yml）。plan の項には after: [番号] で先に済んでいる
  べき項を書き（自分より小さい番号だけ）、最後の項がほかの全部を待つようにする。
  feedback: の項も同じで、after はフィードバック計画の番号だけを指す。独立した作業は
  最後に 1 項で受けるか、一直線に並べる。計画が使う定義は親の phases: に写す
  （ccnavi --plan-order <親> --fill-phases。承認のあとの判定はこの写しを読む）
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
status | next-id | start | finish | cancel | record-risk | learn) ;;
*)
	printf 'ccnavi-ticket: 未対応のサブコマンドです：%s。使用できるサブコマンドは status / next-id / start / finish / cancel / record-risk / learn です。\n' "$1" >&2
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

# 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。
if bin=$(ccnavi_bin "$root"); then
	skew=$(ccnavi_compat_skew "$root" "$bin") || printf 'ccnavi-ticket: %s\n' "$skew" >&2
	ccnavi_c1_exe() { "$bin" --root "$root" "$@"; }
elif [ -f "$root/src/ccnavi/__main__.py" ]; then
	ccnavi_c1_exe() { (cd "$root" && uv run python -m ccnavi --root "$root" "$@"); }
else
	printf 'ccnavi-ticket: ccnavi の実行ファイルが見つかりません（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ のどれにもありません）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。\n' >&2
	exit 2
fi

here="$(pwd -W 2>/dev/null || pwd)"

# 識別子を実行ファイル（argparse）と同じに読む。`--` と `--reason <値>` を読み飛ばした最初の語。
# 副命令（1 つ目の語）は読まない。
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
	ccnavi_c1_exe --cwd "$here" worktree tidy "$ta_id" || :
}

# 子の start で、このPCに子のワークツリーが無ければ、取り込み直後の親ブランチ（C1 の親のワークツリーの HEAD）から作る。
# 別のPCで承認された子でも、どのPCでも同じ場所に作る。ネットワークには出ない（git の局所の操作だけ）。
# 子のブランチ（子の識別子の名前）が手元に残っていれば、親の先頭が含むものだけ親の先頭に揃えて使う
# （含まない = 手元だけに積んだ仕事があるので、消さずに止める）。
# ここで止めるのは git の状態（手元だけのコミット・作れない行き先）を見た判定で、ccnavi の状態（チケット・
# 取り込み状態）を読んで操作を止める判定ではない。shに判定を足さない決まり（hookと実行ファイルが判定する）の外になる。
ensure_child_worktree() {
	ew_path="$root/.claude/worktrees/$c1_id"
	if [ -e "$ew_path" ] || [ -L "$ew_path" ]; then
		return 0
	fi
	if git -C "$ccnavi_c1_tree" show-ref --verify --quiet "refs/heads/$c1_id"; then
		if ! git -C "$ccnavi_c1_tree" merge-base --is-ancestor "refs/heads/$c1_id" HEAD 2>/dev/null; then
			printf 'ccnavi-ticket: 手元のブランチ %s に親のブランチ %s に無いコミットがある。子のワークツリーを作らずに止めた。内容を確かめて、親へ取り込むか、ユーザが消してから再実行してください\n' "$c1_id" "$ccnavi_c1_branch" >&2
			log_info 子のワークツリーを作らずに止めた -- "child=$c1_id" "reason=child-branch-ahead"
			return 1
		fi
		git -C "$ccnavi_c1_tree" branch -f "$c1_id" HEAD >/dev/null 2>&1 || {
			printf 'ccnavi-ticket: 手元のブランチ %s を親のブランチの先頭に揃えられなかった\n' "$c1_id" >&2
			log_info 子のワークツリーを作らずに止めた -- "child=$c1_id" "reason=child-branch-reset"
			return 1
		}
		ew_rc=0
		git -C "$ccnavi_c1_tree" worktree add "$ew_path" "$c1_id" >/dev/null 2>"$ccnavi_c1_tmp/ew-err" || ew_rc=$?
	else
		ew_rc=0
		git -C "$ccnavi_c1_tree" worktree add "$ew_path" -b "$c1_id" HEAD >/dev/null 2>"$ccnavi_c1_tmp/ew-err" || ew_rc=$?
	fi
	if [ "$ew_rc" -ne 0 ]; then
		printf 'ccnavi-ticket: 子のワークツリー %s を作れなかった（%s）\n' "$ew_path" "$(head -n 1 "$ccnavi_c1_tmp/ew-err" 2>/dev/null)" >&2
		log_info 子のワークツリーを作れなかった -- "child=$c1_id" "reason=child-worktree-add"
		return 1
	fi
	printf 'ccnavi-ticket: 子のワークツリー .claude/worktrees/%s を親のブランチ %s の先頭から作った\n' "$c1_id" "$ccnavi_c1_branch" >&2
	log_info 子のワークツリーを作った -- "child=$c1_id" "parent=$ccnavi_c1_family_id"
}

# C1（親子のチケットの start・finish・cancel）。
case "$1" in
start | finish | cancel)
	ccnavi_log_root="$root"
	ccnavi_c1_root="$root"
	ccnavi_c1_label=ccnavi-ticket
	ccnavi_c1_sh="$(dirname "$0")"
	trap 'ccnavi_c1_end' EXIT
	trap 'ccnavi_c1_end; exit 130' INT TERM HUP
	# 識別子は実行ファイル（argparse）と同じに読む（ticket_id。`start -- <親>` で C1 を経ずに通らないようにする）。
	c1_id=$(ticket_id "$@")
	if ! ccnavi_is_ident "$c1_id"; then
		printf 'ccnavi-ticket: %s には識別子を指定してください（%s は有効な識別子の形式ではありません）。\n' "$1" "${c1_id:-（無い）}" >&2
		exit 2
	fi
	ccnavi_c1_family "$c1_id"
	case "$ccnavi_c1_target" in
	stop)
		ccnavi_c1_refuse
		exit 1
		;;
	yes)
		case "$1" in
		start) c1_words="$c1_id に着手" ;;
		finish) c1_words="$c1_id の作業を終えた" ;;
		*) c1_words="$c1_id を取り消した" ;;
		esac
		ccnavi_c1_begin || exit 1
		# 子の finish は、親のブランチへ合流してから push する。合流してよいかをここで確かめる。
		if [ "$1" = finish ] && [ "$c1_id" != "$ccnavi_c1_family_id" ]; then
			ccnavi_c1_merge_plan "$c1_id" || exit 1
		fi
		if [ "$1" = start ] && [ "$c1_id" != "$ccnavi_c1_family_id" ]; then
			ensure_child_worktree || exit 1
		fi
		c1_rc=0
		ccnavi_c1_write "ccnavi: ${c1_words}" -- ticket "$@" || c1_rc=$?
		ccnavi_c1_end
		trap - EXIT INT TERM HUP
		[ "$c1_rc" -ne 0 ] || tidy_after "$@"
		exit "$c1_rc"
		;;
	esac
	ccnavi_c1_end
	trap - EXIT INT TERM HUP
	;;
esac

rc=0
# learn は作業ディレクトリのワークツリーで書き先と識別子が決まるので、cwd を実行ファイルへ渡す
# （ソースで動かす形は cd "$root" してから起こすため、渡さないとワークスペースルートの扱いになる）。
case "$1" in
learn) ccnavi_c1_exe --cwd "$here" ticket "$@" || rc=$? ;;
*) ccnavi_c1_exe ticket "$@" || rc=$? ;;
esac
[ "$rc" -ne 0 ] || tidy_after "$@"
exit "$rc"

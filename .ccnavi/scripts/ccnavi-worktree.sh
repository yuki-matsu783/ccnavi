#!/bin/sh
# ccnavi-worktree直接作業のワークツリーを、取り込み先のブランチから切る。
#
# 直接作業（チケットを使わない作業）用で、名前と同じブランチを新しく切る。親子のチケットの親のワークツリーには
# 使わない（親はccnavi-start.sh --mr <番号> / --issue <番号> で、親のブランチを直接チェックアウトして作る）。
#
#   sh .ccnavi/scripts/ccnavi-worktree.sh <名前> [--base <ブランチ>] [--accept <確認コード>]
#                                        [--copy-wip | --no-wip] [--side-by-side] [--dry-run]
#   sh .ccnavi/scripts/ccnavi-worktree.sh <名前> --adopt --base <ブランチ> [--accept <確認コード>] [--side-by-side]
#
# 取り込み先は、切った時点でユーザのチェックアウト（`git worktree list` の先頭）が開いているブランチ。
# 起点Sは、書きかけをコピーするとき（--copy-wip）は確認した時点の書きかけを含むスナップショット、それ以外は
# 取り込み先のHEAD。ルートのindexとワークツリーには書かない（スナップショットは一時的なindexで作る）。
# 手元の取り込み先は進めない（進めるのはセッション開始時のccnavi-fetch.shだけ）。originからも切らない。
#
# 次のどれかにヒットするとき、ワークツリーを切らずに終了コード 3 で止まり、状態・勧め・リスク・選択肢・確認コードを
# 出す。報告をユーザに伝え、了承を得てから --base / --acceptなどを付けて打ち直す。
#   C4 cwdのブランチと取り込み先が違う（--base <取り込み先> が要る）
#   C5 取り込み先が統合先（origin/HEAD）でない
#   C6 origin/<取り込み先> より遅れている・進んでいる・分岐している・originに無い・リモート未確認
#   C7 ユーザのチェックアウトに書きかけ（追跡済みの変更・新規ファイル）がある（--copy-wipか --no-wipが要る）
#   C5〜C7 のどれかにヒットするときは --accept <確認コード> も要る。確認コードは今の状態から決まるので、
#   状態が動けば報告からやり直しになる。
# ヒットしないときは確認なしでS = 取り込み先のHEADから切る。--side-by-sideだけはaskルールがユーザの確認を取る。
#
# 先に断るもの: ユーザのチェックアウトがdetached HEAD（C1）、取り込み先がチケットに結び付く・結び付くかを
# 確かめられない（C2）、gitが 2.40 未満（C3）、
# cwdが親のワークツリーで、親が承認済み・レビュー待ち・着手済みの子がある（終了コード 1）。
#
# 手で打つgitではなくccnavi-git.shを通す。fetchはorigin <取り込み先> だけ。
# ワークツリーが既にあれば、何も切らずに、パスと取り込みの記録の有無を出します（冪等）。
#
# origin か手元に同じ識別子になるブランチ（同じ名前、`/` を `-` に置き換えると同じになる名前、英字の大文字小文字だけ
# 違う名前）があるときは、切らずに終了コード 1 で止まり、ccnavi-start.sh へ案内する。既にあるブランチ（MR の元ブランチ
# など）は、MR を探して採用する確認をしてから出す必要があり、ここで新しく切ると、同じ識別子のブランチが 2 本になる
# （ワークツリーの置き場が重なる）。
#
# 名前の字の種類・NFC・長さ（64 文字まで）も実行ファイルが確かめる。
#
# 記録はgitの共通ディレクトリのccnavi-direct/<名前>（version 4）に書く。Sはrefs/ccnavi/direct/<名前>/sに置く。
# 書きかけをコピーする候補は確認が要るときにrefs/ccnavi/direct/<名前>/pendingに置き、切ったら消す。
#
# --dry-runは何も作らず、調べた結果と予定だけを出す（fetchもしない）。
# --offline / --from-originは廃止（終了コード 2）。--acceptで代わりができる。--adoptは記録の無い既存のワークツリーを引き取る（2 回の呼び出しで確認コードを使う）。
#
# 判定・ローカルの git とファイルの操作・出力の文は実行ファイルの `ccnavi worktree direct plan|cut`（go/internal/tickets/lifecycle/cut）
# が持つ。ここは段取り（plan → fetch と ls-remote → cut の順）と外との出入り（fetch・ls-remote）だけを受け持つ。
# 実行ファイルが答えなければ、何も切らずに止まる。
#
# 終了コード: 0 完了 / 1 前提の未充足かworktree addの拒否 / 2 引数か環境の誤り / 3 確認が要る

set -eu

# ユーザのチェックアウトの状態を読むだけの git が、index を書き直さない（確認している間、ルートの index に
# 書かない）。ユーザの git add / commit と index.lock を取り合わない。
GIT_OPTIONAL_LOCKS=0
export GIT_OPTIONAL_LOCKS

# fail <識別子> <文面> [終了コード]。文面は標準エラーへ出す契約。識別子は診断ログにだけ残す。
fail() {
	printf 'ccnavi-worktree: %s\n' "$2" >&2
	log_info 止めた -- "exit=${3:-1}" "reason=$1"
	exit "${3:-1}"
}

# 共通部分。ワークスペースルートの探し方と、診断ログはここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-worktree.sh <名前> [--base <ブランチ>] [--accept <確認コード>] [--copy-wip | --no-wip] [--side-by-side] [--dry-run]

  <名前>          ワークツリーの名前。ブランチ名も同じ名前で切る（/ を含められない）。行き先は
                  <ワークスペースルート>/.claude/worktrees/<名前>
  --base <ブランチ>
                  確認した取り込み先（ユーザのチェックアウトが開いているブランチ）
  --accept <確認コード>
                  報告したずれ（取り込み先・origin との関係・書きかけ）を、ユーザが了承して進める
  --copy-wip / --no-wip
                  書きかけをコピーして切るか、コピーせず HEAD から切るか（書きかけがあるときはどちらかが要る）
  --side-by-side  このワークツリーが残っている間、ユーザのチェックアウトで見つかった変更を自動で戻さず報告にする
                  （ccnavi の設定ファイルは戻す）
  --adopt         記録の無い既存の直接作業のワークツリー（.claude/worktrees/<名前>）を引き取る。--base <取り込み先> が要る。
                  1 回目は推定した起点・取り込む範囲・差分を見せて確認コードを出し（終了コード 3）、
                  ユーザの了承を得て --accept <確認コード> を付けて打ち直すと記録を書く
  --dry-run       何も作らず、調べた結果と予定だけ出す

直接作業（チケットを使わない作業）用。親子のチケットの親のワークツリーには使わない。
親のワークツリーの中から打てるのは、親の提案が承認前（todo/ にあるだけ）のときだけ。
承認済み・レビュー待ち・着手済みの子があるときは、切る前に終了コード 1 で断る
（親は ccnavi-start.sh --mr <番号> / --issue <番号> で、親のブランチを直接チェックアウトして作る）。
ずれが無ければ確認なしで切る。ずれがあれば切らずに状態・勧め・リスク・確認コードを出して終了コード 3 で止まる。
ワークツリーが既にあれば、パスと取り込みの記録の有無を出すだけです（冪等）。
終了コード: 0 完了 / 1 前提の未充足か worktree add の拒否 / 2 引数か環境の誤り / 3 確認が要る
USAGE
}

root=$(ccnavi_workspace) ||
	fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
ccnavi_log_root="$root"
scripts="$root/.ccnavi/scripts"

# 段取りの判定と出力を実行ファイルに任せる（`ccnavi worktree direct <動詞>`）。答えは ccnavi_do（ccnavi-common-state.sh）が
# 実行し、値は ccnavi_val で読む。答えが無ければ止める。
wt_do() {
	wd_bin=$(ccnavi_bin "$root") ||
		fail exe-missing "ccnavi の実行ファイルが見つかりません。何も切らずに止めました。実行ファイルを組む（sh scripts/ccnavi-build.sh）か置いてから打ち直してください。"
	wd_out=$("$wd_bin" --root "$root" worktree direct "$@" 2>/dev/null </dev/null | tr -d '\r') || wd_out=""
	[ -n "$wd_out" ] ||
		fail exe-unanswered "ccnavi の実行ファイルが答えませんでした（worktree direct $1）。何も切らずに止めました。実行ファイルを組む（sh scripts/ccnavi-build.sh）か置いてから打ち直してください。"
	ccnavi_do "$wd_out"
}

# 1. 切る前に決まることを確かめる。引き取り（--adopt）と --dry-run はここで終わる。
wt_do plan -- "$@"
target=$(ccnavi_val fetch)
top=$(ccnavi_val top)
log_info 始めた -- "target=$target"

# 2. 外との出入り。fetch は取り込み先だけ（ccnavi-git.sh 経由）。届かなくても止めず、結果をそのまま実行ファイルに渡す
# （origin に無い・リモート未確認の読み分けは実行ファイル。git の断りの文を英語で読むので LC_ALL=C で打つ）。fetch が通ったときだけ、
# origin のブランチの一覧を取る（同じ名前・同じ識別子のブランチが origin に無いかを、実行ファイルが確かめる）。
state="$root/logs/state"
mkdir -p "$state" 2>/dev/null || :
fetch_out="$state/worktree-fetch-$$"
heads="$state/worktree-heads-$$"
heads_err="$state/worktree-ls-remote-err-$$"
trap 'rm -f "$fetch_out" "$heads" "$heads_err"' EXIT
fetch=ok
LC_ALL=C sh "$scripts/ccnavi-git.sh" fetch origin "$target" >"$fetch_out" 2>&1 || {
	fetch=failed
	cat "$fetch_out" >&2
	log_info fetch-failed -- "target=$target"
}
heads_arg=""
err_arg=""
if [ "$fetch" = ok ]; then
	if ccnavi_git_timed "${CCNAVI_WORKTREE_GIT_TIMEOUT:-30}" "$heads_err" "$top" ls-remote --heads origin >"$heads"; then
		heads_arg="$heads"
	else
		: >>"$heads_err"
		err_arg="$heads_err"
		log_info ls-remote できず -- "target=$target"
	fi
fi

# 3. ずれと書きかけを確かめ、確認が要れば報告して止め、要らなければ記録を書いて切る。
wt_do cut "fetch=$fetch" "fetch-out=$fetch_out" "heads=$heads_arg" "heads-err=$err_arg" -- "$@"
log_info 完了 -- "target=$target" "snap=$(ccnavi_val snap)"

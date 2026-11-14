#!/bin/sh
# ccnavi-git 安全な git だけを通し、出力を抑えて結果だけ返すラッパースクリプト。
#
# 生の `git` は PreToolUse で拒否し、拒否の文面からここへ誘導する。狙いは 2 つ。
#
#   1. 出力がコンテキストに丸ごと載るのを止める。`git log -p` や `git diff` は
#      入力次第で数千行になる。全量は logs/ に残し、標準出力へは要約と先頭数十行
#      だけを返す。足りなければログを名指しで読ませる
#   2. 入口を 1 本にして、オプションの穴を防ぐ。`permissions.allow` の `Bash(git diff:*)`
#      は前方一致でしかないので、後ろに何を足されても通る。サブコマンドごとに
#      使ってよい形を書けるのは、入口を 1 本にしたここだけ
#
# ホワイトリストに無いものは既定で拒否する。拒否の文面には必ず「代わりに何をするか」を
# 書く。理由だけ返すとエージェントは言い換えて再試行する。
#
# これは失敗と浪費を減らすためのもので、敵対的な回避への防御ではない。
# `sh -c` や `python -c` に埋めれば hook の文字列一致は外れる。そこまで防ぐなら
# permissions.deny か sandbox が要る。
#
# 使い方:  sh .ccnavi/scripts/ccnavi-git.sh <サブコマンド> [引数...]
# 終了コード: 0 成功 / 1 git が失敗 / 2 引数か環境の誤り (拒否を含む)

set -eu

# 標準出力に返す本文の上限。超えたぶんはログにだけ残る。
MAX_LINES="${CCNAVI_GIT_MAX_LINES:-40}"

# 失敗したときに返す末尾の行数。原因はたいてい最後に出る。
FAIL_LINES="${CCNAVI_GIT_FAIL_LINES:-30}"

# 残す記録の本数。放っておくと増え続けるので世代で切る。
KEEP_LOGS="${CCNAVI_GIT_KEEP_LOGS:-50}"

# 対話になる経路を全部止める。Bash ツールの stdin は /dev/null だが、git の
# 資格情報プロンプトは /dev/tty を直接開くので stdin だけでは止まらない。
GIT_TERMINAL_PROMPT=0
GIT_PAGER=cat
PAGER=cat
GIT_EDITOR=true
export GIT_TERMINAL_PROMPT GIT_PAGER PAGER GIT_EDITOR

# 環境変数から設定を差し込む経路を閉じる。`-c diff.external=<コマンド>` を引数で
# 拒否しても、GIT_CONFIG_COUNT/KEY/VALUE と GIT_EXTERNAL_DIFF で同じことができる。
# 引数だけ見て環境を見ないと、防いだつもりの穴が別の経路で開いたままになる。
# GIT_CONFIG_KEY_n / VALUE_n は GIT_CONFIG_COUNT が無ければ読まれないので、
# 番号を数えて消す必要はない。GIT_CONFIG_COUNT を消せば全部読まれない。
# 例外は、このあと実行直前に ccnavi_git_auth が足す gh / glab の credential helper だけ（利用者の環境変数は引き継がない）。
unset GIT_EXTERNAL_DIFF GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_ALTERNATE_OBJECT_DIRECTORIES 2>/dev/null || true
# git が操作するリポジトリとワークツリーを、居場所（cwd）でなく環境変数で決める経路も閉じる。
# `GIT_DIR=<親>/.git GIT_WORK_TREE=<親> sh ccnavi-git.sh commit` を子のワークツリーで打つと、
# 居場所の検査（hook と、下の子のワークツリーの検査）は子を見るのに、git は親へコミットする。
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_NAMESPACE \
	GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM 2>/dev/null || true

# 拒否の文面で代わりの形を示すときの、自分の呼び方。生の git は PreToolUse で
# 止まるので、案内に `git stash push -u` と書くと、案内された先でもう 1 度拒否される。
# 代わりの手段が拒否される案内は、案内が無いのとほとんど同じ。
# $0 は呼ばれたときのパスそのままなので、ワークツリーの中から相対で呼ばれても合う。
SELF="sh $0"
# 取り込みの sh。リモートに合わせる経路はここへ案内する。
SYNC="sh $(dirname "$0")/ccnavi-sync.sh"
# 親のワークツリーを作る入口。既にあるブランチを親にする経路はここへ案内する。
START="sh $(dirname "$0")/ccnavi-start.sh"

# reject <識別子> <文面>。文面は標準エラーへ出す契約。識別子は拒否の種類を表す短い語で、
# 診断ログにだけ残す。文面にはユーザの引数（URL など）が入るので、ログにはコピーしない。
reject() {
	printf 'ccnavi-git: %s\n' "$2" >&2
	# 診断ログ。上の文面は契約として決まっている出力で、診断ログはそれとは別に残すだけ。
	log_info 拒否した -- "sub=${sub:-}" "reason=$1"
	exit 2
}

# exe_reject <識別子> <文面>。実行ファイル（`ccnavi git check`）が拒否したとき。識別子は実行ファイルが決めるので、
# reject と違って文字のまま書けない。出し方と記録の仕方は reject と同じ。
exe_reject() {
	printf 'ccnavi-git: %s\n' "$2" >&2
	log_info 拒否した -- "sub=${sub:-}" "reason=$1"
	exit 2
}

# 共通部分。ワークスペースルートの探し方と、プロジェクト名の導出はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

# 案内の文専用の、引用した自分の呼び方。パスに空白や ' があっても、そのまま打てる。
# `SELF` は今までの動きのまま変えない。
SELF_Q="sh $(ccnavi_sh_quote "$0")"
WT_SH_Q="sh $(ccnavi_sh_quote "$(dirname "$0")/ccnavi-worktree.sh")"
TICKET_SH_Q="sh $(ccnavi_sh_quote "$(dirname "$0")/ccnavi-ticket.sh")"

# ワークスペースルート。道具と記録の置き場。git のトップとは違うもので、
# モード B（projects/ の下に別リポジトリを clone する形）では一致しない。
# 上へたどって `.ccnavi/scripts/ccnavi-common.sh` を探す（保護済みスクリプトはワークスペースルートの .ccnavi/scripts/ に置くため）。
WS=$(ccnavi_workspace) ||
	reject no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。"
# 解いたルートを logger に渡し、書くたびに探し直させない。
ccnavi_log_root="$WS"
# git が返すパス（リンクを解いたもの）と比べるための、リンクを解いたルート。
WS_P=$(ccnavi_phys "$WS")

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-git.sh <サブコマンド> [引数...]

通すもの
  読む      status log show diff blame shortlog describe rev-parse rev-list
            ls-files ls-tree merge-base diff-tree cat-file grep
  一覧      branch (-d は可 / -D -m -M -C -f -u は不可)  tag (一覧のみ。作成はユーザに依頼)
            remote (-v / show / get-url のみ)  worktree (list add prune remove)
            worktree add は行き先の名前 = ブランチ名の / を - に置き換えたもの、の形だけ
                  (add <行き先> -b <ブランチ> [<起点>] か add <行き先> <既にあるブランチ>。
                  -B / --detach / -f は不可。既にあるブランチを出す形は、識別子が親に使えるもの
                  （58 文字以内・末尾が -2 桁でない。子の識別子の形は 64 文字まで）だけ)
  変える    add  commit (--no-verify は不可)  rm <パス> (-f は不可)
            restore <パス>  (衝突の解決は restore --ours / --theirs -- <パス>)
            checkout / switch (ブランチを移る形だけ。-f・checkout -B・switch -C と -- <パス> は不可。
                  親のワークツリーでは別のブランチへ移れない)
            承認済みチケットの置き場 (.ccnavi/approved/ と wip/proposals/review/) には
                  restore --source / --ours / --theirs と checkout <ref> <パス> を利用できない
            ccnavi 自身を守る場所 (.ccnavi/ .claude/settings*.json .claude/hooks/ logs/ の記録と state
                  ccnavi-git.sh) には rm と restore --source と checkout <ref> <パス> を利用できない
                  (衝突の解決の restore --ours / --theirs は利用できる。変更が必要ならユーザに依頼する)
            stash (list show push pop apply)
            merge (-X ours / -s ours / --no-verify は不可。統合先のユーザのチェックアウトでは --abort / --continue / --quit 以外は不可)
            merge-file -p (標準出力に出す形だけ。ファイルへは書かない)
                  両方取り込むときは merge-file -p --union --object-id :2:<パス> :1:<パス> :3:<パス>
                  (現在・祖先・相手の順) で出力し、それを読んで Edit で書く
  通信      fetch  pull  (--force / --prune は不可。取ってくるのは <リモート> <ブランチ> だけで、
                   : や + を含む引数(refspec・URL)は不可。手元の ref は ccnavi-sync.sh が進める。
                   統合先のユーザのチェックアウトの pull は、引数なしと現在のブランチと同じ名前
                   (pull [--ff-only] <リモート> <統合先>) だけ。別のブランチは merge と同じく不可)
            push  (居るブランチを同じ名前で送る形だけ。force / delete / all は不可。
                   main master develop release と統合先 (そのリポジトリの origin/HEAD、無ければ
                   origin/main・origin/master) へ直接は送れない。
                   子チケットのワークツリーからは送れない。親が取り込んでから親のツリーで送る。
                   リモートから消えた親のブランチへは送れない。親のワークツリーは push の前に
                   リモートに在るかを確かめる。破棄した親ならワークツリーを片付ける)
  取り込み  直接作業(チケットを起こさない)のワークツリーを、切ったときの取り込み先のブランチへ 1 コミットで載せる。
            取り込み先をチェックアウトしているユーザのチェックアウトで実行します。チケットの作業は対象外(マージリクエストで出す)
            integrate <名前>          取り込み結果を計算して一時ワークツリー(logs/direct-tmp/)に展開し、差分と変わるファイルを
                                      出して止まる。ユーザのチェックアウトの index とワークツリーには書かない。
                                      wip/handoff/ の下書きは取り込まない
            integrate --abort <名前>  取り込みをやめる(一時ワークツリーと記録を消す。作業ワークツリーには触らない)
            integrate-done <名前> -m <メッセージ>
                                      一時ワークツリーで 1 コミットにし(フックを通す)、ユーザのチェックアウトで
                                      merge --ff-only --no-overwrite-ignore する。そのあとワークツリー・ブランチ・
                                      記録を片付ける。何度再実行してもよい

通さないもの(代わりの手段)
  reset clean   sh .ccnavi/scripts/ccnavi-git.sh stash push -u で退避する。削除しない
                リモートに合わせるなら sh .ccnavi/scripts/ccnavi-sync.sh <P> (親のブランチ)。
                ほかのブランチは fetch <リモート> <ブランチ> のあと merge <リモート>/<ブランチ>。
                分かれていて進めないならユーザの対応に切り替える (checkout -B でブランチを移さない)
  rebase cherry-pick revert am apply bisect  履歴を書き換えない
  config clone submodule  ユーザに依頼する
  -c / --config-env / --git-dir / -C / --output / --upload-pack / --exec-path
                読み取り専用のサブコマンドでも任意コマンドの実行や書き込みに
                なってしまうので、値を見ずに一律で拒否する

オプション: branch checkout switch fetch pull merge commit rm restore cat-file worktree は
            許可リストで読む。長いオプションは略さずに書く (略した表記は通らない)。
            短いオプションはまとめて書いてよく、値を取る字 (-b など) の後ろは値として読む

出力: 成功なら要約と先頭 40 行、失敗なら末尾 30 行。全量は logs/ に残る。
環境変数: CCNAVI_GIT_MAX_LINES / CCNAVI_GIT_FAIL_LINES / CCNAVI_GIT_KEEP_LOGS
USAGE
}

[ "$#" -eq 0 ] && {
	usage
	exit 2
}

case "$1" in
-h | --help | help)
	usage
	exit 0
	;;
esac

# 引数の形を通すかは実行ファイルが決める（`ccnavi git check`）。決めるのは、サブコマンドより前のオプション
# （`-c diff.external=<コマンド>`・`-C <パス>`・`--git-dir` など。値を見ずに形で断る）、どのサブコマンドでも許さない形
# （設定の一時上書き・ファイル書き込みや外部コマンド実行に変えるオプション・判定の起点を動かすオプションと、その略）、
# 引数だけで決まるサブコマンドの判定（読むだけの一覧・cat-file・grep・branch・tag・remote・stash・add・commit・
# 通さないサブコマンド）。答えは allow（通す）・pass（続きはこのあとの case が見る）・deny <識別子> と拒否の文。
# git の引数は標準入力に NUL 区切りで渡す（ccnavi の引数として渡すと、`-m` などを ccnavi のオプションとして読むため）。
# 診断ログの sub。サブコマンドより前のオプションで止めるときは空のまま。
case "$1" in
-*) ;;
*) sub="$1" ;;
esac
gc_bin=$(ccnavi_bin "$WS") ||
	reject no-exe "ccnavi の実行ファイルが見つからないため、git の引数の形を確かめられません。sh .ccnavi/scripts/ccnavi-launcher.sh --version で実行ファイルを確かめ、無ければユーザに伝えてください。"
gc_out=$(printf '%s\0' "$@" | "$gc_bin" --root "$WS" git check "$SELF" "$SYNC" "$START" "$SELF_Q" "$WT_SH_Q" 2>/dev/null) || gc_out=""
gc_out=$(printf '%s\n' "$gc_out" | tr -d '\r')
# 答えの行を読む。allow に続けて、git の引数の前に足す語（prepend）・出力の後ろに出す警告（warn）・push の前に確かめる
# 親のブランチ（gate-top・gate-branch）・統合先のユーザのチェックアウトの統合先（target）が付くことがある。
gc_line() { printf '%s\n' "$gc_out" | sed -n "s/^$1 //p" | head -n 1; }
case "$(printf '%s\n' "$gc_out" | head -n 1)" in
allow | pass) ;;
deny\ *)
	gc_id=$(printf '%s\n' "$gc_out" | sed -n '1s/^deny //p')
	exe_reject "$gc_id" "$(printf '%s\n' "$gc_out" | sed -n '2,$p')"
	;;
*) reject no-exe "ccnavi の実行ファイルが git の引数の形に答えませんでした。sh .ccnavi/scripts/ccnavi-launcher.sh --version で実行ファイルを確かめ、直せなければユーザに伝えてください。" ;;
esac
gc_warn=$(gc_line warn)
gc_gate_top=$(gc_line gate-top)
gc_gate_branch=$(gc_line gate-branch)
gc_target=$(gc_line target)
gc_prepend=$(gc_line prepend)

sub="$1"
shift
[ -z "$gc_prepend" ] || set -- "$gc_prepend" ${1+"$@"}
[ "$sub" != worktree ] || action="${1:-list}"

# 共通の git ディレクトリ（fetch が失敗した印の置き場。印は worktree add の起点の確認が読む）。
wt_fresh_dir() {
	wt_fd=$(git rev-parse --git-common-dir 2>/dev/null) || return 1
	(cd "$wt_fd" 2>/dev/null && pwd)
}

# ---- 直接作業の取り込み（integrate / integrate --abort / integrate-done）
#
# 判定と操作（記録・一時ワークツリー・コミット・取り込み先への fast-forward・片付け）は実行ファイルの
# `ccnavi git integrate`（go/internal/tickets/lifecycle/land）が行う。ここはロック（キーは
# logs/state/locks/<repo>/.integrate。持ち主はこの sh）を取ってから呼ぶ。標準入力はそのまま渡す（自己防衛の対象を含む取り込みは
# ターミナルから打たせるので、端末かを実行ファイルが見る）。
ig_run() {
	ig_top=$(git rev-parse --show-toplevel 2>/dev/null || :)
	ig_repo=""
	[ -z "$ig_top" ] || ig_repo=$(ccnavi_project "$ig_top" "$WS")
	[ -n "$ig_repo" ] || ig_repo=self
	ccnavi_lock_take "$WS" "$ig_repo" .integrate 0 ||
		reject integrate-locked "別の integrate か integrate-done が動いています（ロック: logs/state/locks/${ig_repo}/.integrate）。終わってから再実行してください。"
	trap 'ccnavi_lock_drop' EXIT
	trap 'exit 130' INT
	trap 'exit 143' TERM
	trap 'exit 129' HUP
	ig_rc=0
	"$gc_bin" --root "$WS" git integrate "$SELF" "$SELF_Q" "$WT_SH_Q" "$TICKET_SH_Q" "$MAX_LINES" -- "$sub" ${1+"$@"} || ig_rc=$?
	exit "$ig_rc"
}

case "$sub" in
integrate | integrate-done)
	ig_run ${1+"$@"}
	;;
*)
	: # ほかのサブコマンドは、上の `ccnavi git check` が通した
	;;
esac

# リモートから消えた親のブランチへは送らない（push のゲート。write-and-push・ccnavi-user-push-approved.sh と同じ）。
# 普通の push で作り直すと、消えた理由（改名・消し間違い・破棄した親子のチケット）を確かめないまま親子のチケットが
# 動き出す。ゲートを掛けるか（送り先が origin で、ワークツリーから送る）は実行ファイルが決め、ls-remote はここで打つ。
if [ -n "$gc_gate_top" ]; then
	push_gate_rc=0
	# 秒数は write-and-push・ccnavi-user-push-approved.sh と同じ既定（60 秒。CCNAVI_WRITE_AND_PUSH_TIMEOUT で変えられる）。
	case "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" in
	'' | *[!0-9]*) push_gate_secs=60 ;;
	*) push_gate_secs="$CCNAVI_WRITE_AND_PUSH_TIMEOUT" ;;
	esac
	ccnavi_push_gate "$gc_gate_top" "$gc_gate_branch" "${gc_gate_top##*/}" "$WS_P" "$push_gate_secs" || push_gate_rc=$?
	case "$push_gate_rc" in
	0) ;;
	1) reject push-parent-missing "${ccnavi_pg_why}。エージェントは戻せない（生の git は利用できない）ので、ユーザに伝えて止まってください。" ;;
	*) reject push-parent-unverified "${ccnavi_pg_why}。直せなければユーザに伝えて止まってください（生の git で押し通さない）。" ;;
	esac
fi

# 外部 diff ドライバは設定にも書けるので、読む系では毎回無効にして呼ぶ。
case "$sub" in
diff | show | log | whatchanged) set -- --no-ext-diff ${1+"$@"} ;;
esac

root=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -z "$root" ] && reject not-a-repo "git リポジトリの中で実行してください。"
log_info 受け付けた -- "sub=$sub" "args=$#"
log_debug 判定の材料 -- "sub=$sub" "action=${action:-}" "top=$root" "cwd=$PWD" "workspace=$WS"

# 記録はワークスペースの下にまとめる（git のトップとワークスペースルートは別で、モード B では異なるため）。git のトップに書くと、
# モード B ではプロジェクトのリポジトリの中に出る。ワークスペースの .gitignore の
# /logs/ はワークスペースルート起点なので該当せず、public のリポジトリに運用の痕跡が入る。
logproject=$(ccnavi_project "$(pwd)" "$WS")
if [ -n "$logproject" ]; then
	logdir="$WS/logs/$logproject"
else
	logdir="$WS/logs"
fi
mkdir -p "$logdir"
logfile="$logdir/git-$(date '+%Y%m%d-%H%M%S')-$$.log"

# 全量を必ず残す。成功でも書く。破棄すると「あのとき何が出ていたか」を後から
# 確かめられない。logs/ は .gitignore に入っていて、コミット対象にならない。
{
	printf '# %s  cwd=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$(pwd)"
	printf '$ git %s' "$sub"
	for arg in ${1+"$@"}; do printf ' %s' "$arg"; done
	printf '\n--- 出力 ---\n'
} >"$logfile"

# ネットワークに出る git には、gh / glab の資格情報を使わせる（認証済みなら git の認証も通る）。
case "$sub" in
push | fetch | pull | ls-remote) ccnavi_git_auth . ;;
esac

status=0
git --no-pager "$sub" ${1+"$@"} >>"$logfile" 2>&1 || status=$?

# fetch を試して失敗した印（worktree add の起点の確認が、オフラインで詰まらないように使う）。
# 成功すれば消す。FETCH_HEAD の更新時刻は成功した fetch でしか進まない。
if [ "$sub" = fetch ]; then
	fetch_common=$(wt_fresh_dir) || fetch_common=""
	if [ -n "$fetch_common" ]; then
		if [ "$status" -eq 0 ]; then
			rm -f "$fetch_common/ccnavi-fetch-failed"
		else
			: >"$fetch_common/ccnavi-fetch-failed" 2>/dev/null || :
		fi
	fi
fi

# 記録のパス。エージェントがそのまま sed -n で開ける形で返す。
#
# 基準はワークスペースルート。モード B ではエージェントの cwd がプロジェクトの中に
# あるので、git のトップからの相対を返すと届かない。ワークスペースの中に居るときは
# 短い相対、そうでなければ絶対を返す（git のトップとワークスペースルートは別だから）。
case "$logfile" in
"$WS"/*) logrel="${logfile#"$WS"/}" ;;
*) logrel="$logfile" ;;
esac
# cwd から相対で開けないなら、絶対パスをそのまま返す。2 つ並べない。
# 並べると、受け取った側がどちらを開くか迷い、パスの切り出しも要る。
if [ ! -f "$logrel" ]; then
	logrel="$logfile"
fi

# 本文は目印の次の行から。目印と同じ行が出力に含まれても、awk は最初の 1 件で
# f を立て、それより後ろだけを出すので取り違えない。
body() { awk 'f; /^--- 出力 ---$/ { f = 1 }' "$logfile"; }

lines=$(body | awk 'END { print NR + 0 }')

# 要約は「次の判断に使う数値」だけにする。本文をもう 1 度読ませないためのもので、
# 本文の代わりではない。
summary=$(body | awk -v cmd="$sub" '
	/^diff --git /			{ files++ }
	/^\+/ && !/^\+\+\+/		{ plus++ }
	/^-/ && !/^---/			{ minus++ }
	/^commit [0-9a-f]/		{ commits++ }
	/^[ MADRCU?!][ MADRCU?!] /	{ entries++ }
	/^\t/				{ entries++ }
					{ n++ }
	END {
		if ((cmd == "diff" || cmd == "show") && files > 0)
			printf "%d ファイル +%d -%d", files, plus, minus
		else if ((cmd == "log" || cmd == "shortlog") && commits > 0)
			printf "%d コミット", commits
		else if (cmd == "status")
			printf "変更 %d 件", entries + 0
		else
			printf "%d 行", n + 0
	}')

if [ "$status" -eq 0 ]; then
	printf 'ok  git %s  %s  log=%s\n' "$sub" "$summary" "$logrel"
	body | head -n "$MAX_LINES"
	if [ "$lines" -gt "$MAX_LINES" ]; then
		printf '... 残り %d 行は %s にある（全文は Read ツールでこのファイルを読む）\n' "$((lines - MAX_LINES))" "$logrel"
	fi
	if [ -n "$gc_warn" ]; then
		printf '警告: %s\n' "$gc_warn"
	fi
else
	printf 'fail  git %s  exit=%d  log=%s\n' "$sub" "$status" "$logrel"
	body | tail -n "$FAIL_LINES"
	# Windows では、プロセスの cwd になっているディレクトリは使用中になる。Bash ツールの cwd は呼び出しを
	# またいで残る親のシェルのものなので、ワークツリーの中へ cd したまま remove すると、git が
	# 中身を消したあと最後のディレクトリで Permission denied になり、空のディレクトリが残る。
	# サブシェルの中で cd してから打っても防げず、ここで pwd を見ても親の cwd は分からないので、
	# 起きたときに直し方を言う。
	if [ "$sub" = worktree ] && [ "${action:-}" = remove ] && body | grep -q 'Permission denied'; then
		rm_path_q='<パス>'
		[ -z "${2:-}" ] || rm_path_q=$(ccnavi_sh_quote "$2")
		ccnavi_text "$WS" NOTE_GIT_WORKTREE_REMOVE_CWD "ws=$WS" "self_q=$SELF_Q" "path_q=$rm_path_q"
	fi
	# 統合先のユーザのチェックアウトの pull が衝突したとき（未解決の索引がある）だけ、解かずにユーザへ頼む案内を足す。
	# ワークツリーの中の衝突は、そこで解く普段の作業なので足さない。
	if [ -n "$gc_target" ] && [ -n "$(git ls-files -u 2>/dev/null)" ]; then
		ccnavi_text "$WS" NOTE_GIT_PULL_CONFLICT_ON_TARGET "integ=$gc_target"
	fi
fi

# 世代で切る。新しい順に並べ、上限より後ろを消す。
ls -1t "$logdir"/git-*.log 2>/dev/null | awk -v keep="$KEEP_LOGS" 'NR > keep' |
	while IFS= read -r old; do rm -f "$old"; done

log_info 終わった -- "sub=$sub" "exit=$status" "lines=$lines" "log=$logrel"

[ "$status" -eq 0 ] || exit 1
exit 0

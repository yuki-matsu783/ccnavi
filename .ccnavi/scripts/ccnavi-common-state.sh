# ccnavi-common-state.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# state の置き場とロック、統合先の名前、親ブランチの push の関門、タイムアウト監視つきの git。

# ---- state の置き場とロック
#
# 1 行 1 項目の `<キー> <値>` のファイルは、sh が `sed -n 's/^<キー> //p'` で読み、jq を使わない
# （JSON とチケットの YAML は実行ファイルが読んで、sh には 1 行で返す）。
# 置き場はワークスペースルートの `logs/state`（固定。ccnavi-review.sh と同じ）。
#
#   locks/<リポジトリ>/<P>/          ロック。中の owner に持ち主を 1 行で書く
#
# <リポジトリ> はワークスペース自身なら `self`、プロジェクトならその名前。
# 親子ごとの取り込み状態や統合先のコピーは持たない（判定は親ブランチとチケットの置き場だけで決まる）。

# state の置き場（`logs/state/`）の絶対パス。
ccnavi_state() {
	printf '%s\n' "$1/logs/state" # 固定
}

# そのツリーが属するリポジトリを分ける名前（ロックの置き場）。<ツリー> <ワークスペースルート>
ccnavi_repo_key() {
	ccnavi_rk_name=$(ccnavi_project "$1" "$2")
	if [ -n "$ccnavi_rk_name" ]; then
		printf '%s\n' "$ccnavi_rk_name"
	else
		printf 'self\n'
	fi
}

# 親のブランチ名として <名前> を受けてよいなら 0。<名前> <識別子>
#
# 識別子と同じ名前は識別子の検査（ccnavi_is_ident）で見る（前からの識別子は ccnavi_is_branch の予約に
# ヒットすることがある）。違う名前は ccnavi_is_branch で見る。どちらも実行ファイルの検査（`ticket_ids.id_problem`・
# `branch_problem`）の、ASCII で見分けられる分だけの複製で、受けたものを使う側の 2 段目の確認にあたる。
ccnavi_branch_ok() {
	if [ "$1" = "$2" ]; then
		ccnavi_is_ident "$1"
	else
		ccnavi_is_branch "$1"
	fi
}

# ワークツリーの HEAD が指すブランチ名。<ワークツリー> <識別子>
#
# HEAD のブランチ名を変換（ccnavi_branch_to_id。/ を - に置き換え）
# したものがフォルダ名の識別子と一致し、ブランチ名として受けてよいときだけ出す。
# ワークツリーが無い・ブランチの外（detached HEAD）なら 1、一致しない・使えない名前なら 2。
# git は読み取りの 1 回だけ。
ccnavi_head_branch() {
	{ [ -d "$1" ] && [ ! -L "$1" ]; } || return 1
	ccnavi_hb_name=$(git -C "$1" symbolic-ref --quiet --short HEAD 2>/dev/null) || ccnavi_hb_name=""
	[ -n "$ccnavi_hb_name" ] || return 1
	[ "$(ccnavi_branch_to_id "$ccnavi_hb_name")" = "$2" ] || return 2
	ccnavi_branch_ok "$ccnavi_hb_name" "$2" || return 2
	printf '%s\n' "$ccnavi_hb_name"
}

# 実行ファイルに聞く。答え（標準出力）を、Windows の実行ファイルの CR を除いて出す。<ワークスペースルート> <引数>...
#
# 実行ファイルが無ければ 1（ソースで動かしている ccnavi のリポジトリでは uv で起こす）。実行ファイルが
# 断った・答えなかったときは空を出して 0。答えの頭の行を確かめるのは呼ぶ側。
ccnavi_ask() {
	ccnavi_ak_root="$1"
	shift
	if ccnavi_ak_bin=$(ccnavi_bin "$ccnavi_ak_root"); then
		ccnavi_ak_out=$("$ccnavi_ak_bin" --root "$ccnavi_ak_root" "$@" 2>/dev/null </dev/null) || ccnavi_ak_out=""
	elif [ -f "$ccnavi_ak_root/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
		ccnavi_ak_out=$(cd "$ccnavi_ak_root" && uv run --quiet python -m ccnavi --root "$ccnavi_ak_root" "$@" 2>/dev/null </dev/null) ||
			ccnavi_ak_out=""
	else
		return 1
	fi
	printf '%s\n' "$ccnavi_ak_out" | tr -d '\r'
}

# 文面ファイルの文を、実行ファイル（`ccnavi text`）に引かせて標準出力に出す。
# <ワークスペースルート> <識別子> [<名前>=<値>]... [@<欄>=<場合>]...
#
# 値は引用せずそのまま渡す。コマンドの一部になる値は、文面ファイルの側が `{名前:sh}` で引用する。引数の並びなど
# 呼ぶ側が引用を済ませた値は、名前を `_q` で終える。sh は文面ファイルを直接読まない。
#
# 引けないとき（実行ファイルが無い・版が合わない・識別子や値が合わない・文面ファイルが壊れている）は、
# 識別子の種類に合わせた短い既定の文を出して 1 を返す。止める・止めないは呼ぶ側が決めたままで、変えない。
ccnavi_text() {
	ccnavi_tx_root="$1"
	ccnavi_tx_id="$2"
	shift 2
	if ccnavi_tx_out=$(ccnavi_ask "$ccnavi_tx_root" text "$ccnavi_tx_id" "$@") && [ -n "$ccnavi_tx_out" ]; then
		printf '%s\n' "$ccnavi_tx_out"
		return 0
	fi
	ccnavi_tx_gone="ユーザに、ccnavi の実行ファイルと文面ファイル（.ccnavi/config/common/messages/）が使えるかを確かめてもらい、返事を待つ（文面 ${ccnavi_tx_id} を引けませんでした）"
	case "$ccnavi_tx_id" in
	CHOICES_*) printf '次の手:\n  1. %s\n' "$ccnavi_tx_gone" ;;
	NOTE_*) printf '案内: 文面 %s を引けませんでした\n' "$ccnavi_tx_id" ;;
	*) printf '次: %s\n' "$ccnavi_tx_gone" ;;
	esac
	return 1
}

# 名前のワークツリーがチケットに結び付くかを実行ファイル（`tree bound`）に聞く。<ワークスペースルート> <ワークツリーのパス> <名前>
#
# 答えは ccnavi_tb_state（yes・no・unavailable（実行ファイルに確かめられない）・undecided（決められない））と、
# 結び付くときの理由 ccnavi_tb_why に入れる。結び付かない（no）ときだけ 0。`ccnavi-git.sh integrate` と
# `ccnavi-worktree.sh --adopt` が同じ判定で断るための入口で、sh はチケットの置き場を自分で読まない。
# 止める側の呼び出し元は、no 以外を「引き取らない・取り込まない」に倒す。
ccnavi_tree_bound() {
	ccnavi_tb_out=$(ccnavi_ask "$1" tree bound "$2" "$3") || ccnavi_tb_out=""
	ccnavi_tb_why=""
	if [ "$(printf '%s\n' "$ccnavi_tb_out" | head -n 1)" != "tree 1" ]; then
		ccnavi_tb_state=unavailable
		return 1
	fi
	ccnavi_tb_why=$(printf '%s\n' "$ccnavi_tb_out" | sed -n 's/^why //p' | head -n 1)
	case "$(printf '%s\n' "$ccnavi_tb_out" | sed -n 's/^bound //p' | head -n 1)" in
	no)
		ccnavi_tb_state=no
		return 0
		;;
	yes) ccnavi_tb_state=yes ;;
	*) ccnavi_tb_state=undecided ;;
	esac
	return 1
}

# ブランチ名から作る識別子と、それが使えるかを実行ファイル（`worktree id`）に聞く。<ワークスペースルート> <ブランチ>
#
# 答えは ccnavi_id_ident（識別子）・ccnavi_id_problem（親のブランチとして採用できない理由。58 文字・末尾の
# -<2 桁>・字の種類・NFC）・ccnavi_id_direct（直接作業や子のワークツリーとして使えない理由。64 文字・字の種類・NFC）・
# ccnavi_id_child（子の識別子の形なら yes）に入れる。sh は字の種類と NFC と文字数を自分で確かめない
# （`ccnavi_is_ident` と `ccnavi_is_branch` は ASCII の外のバイトを通す。`${#x}` は dash ではバイト数になる）。
# 実行ファイルが無い・答えないなら 1（呼ぶ側は「確かめられない」として止める）。
ccnavi_id_ask() {
	ccnavi_ia_out=$(ccnavi_ask "$1" worktree id "$2") || ccnavi_ia_out=""
	ccnavi_id_ident=$(printf '%s\n' "$ccnavi_ia_out" | sed -n 's/^id //p' | head -n 1)
	ccnavi_id_problem=$(printf '%s\n' "$ccnavi_ia_out" | sed -n 's/^problem //p' | head -n 1)
	ccnavi_id_direct=$(printf '%s\n' "$ccnavi_ia_out" | sed -n 's/^direct //p' | head -n 1)
	ccnavi_id_child=$(printf '%s\n' "$ccnavi_ia_out" | sed -n 's/^child //p' | head -n 1)
	[ -n "$ccnavi_id_ident" ]
}

# <一覧> のブランチ名（1 行 1 本）のうち、<ブランチ> と同じ識別子になる別の名前を 1 つ出す。無ければ 1。<ブランチ> <一覧>
#
# 識別子（/ を - に置き換えたもの）は英字の大文字小文字をそろえて比べる。大文字小文字を区別しない機械
# （macOS・Windows）では、`.claude/worktrees/Feature-12-x` と `feature-12-x` が同じ置き場になる。
# 同じ識別子になる名前は長さも同じ（`${#x}` がバイト数でも字数でも、同じシェルの中では同じ数え方）なので、
# 長さの違うものは比べずに飛ばす。
ccnavi_id_clash() {
	ccnavi_ic_want=$(ccnavi_branch_to_id "$1" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
	while IFS= read -r ccnavi_ic_other; do
		[ -n "$ccnavi_ic_other" ] || continue
		[ "$ccnavi_ic_other" != "$1" ] || continue
		[ "${#ccnavi_ic_other}" -eq "${#1}" ] || continue
		if [ "$(ccnavi_branch_to_id "$ccnavi_ic_other" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')" = "$ccnavi_ic_want" ]; then
			printf '%s\n' "$ccnavi_ic_other"
			return 0
		fi
	done <"$2"
	return 1
}

# 1 行 1 項目のファイル（integrate のマーカー）から 1 項目を読む。無ければ空。<ファイル> <キー>
ccnavi_record_get() {
	[ -f "$1" ] || return 0
	sed -n "s/^$2 //p" "$1" 2>/dev/null | head -n 1
}

# 1 行 1 項目のファイル（integrate のマーカー）を書き直す。<ファイル> <キー> <値> [<キー> <値>...]
#
# 同じディレクトリの一時ファイルに書いてから mv で置き換える（読む側が半端な中身を見ない）。
# 値の改行は空白に置き換える（1 行 1 項目の契約）。書けなければ 1。渡したキーだけを書く。
ccnavi_record_write() {
	ccnavi_rw_file="$1"
	shift
	mkdir -p "${ccnavi_rw_file%/*}" 2>/dev/null || return 1
	ccnavi_rw_tmp="$ccnavi_rw_file.tmp.$$"
	: >"$ccnavi_rw_tmp" 2>/dev/null || return 1
	while [ "$#" -ge 2 ]; do
		ccnavi_rw_value=$(printf '%s' "$2" | tr '\r\n' '  ')
		printf '%s %s\n' "$1" "$ccnavi_rw_value" >>"$ccnavi_rw_tmp" || {
			rm -f "$ccnavi_rw_tmp"
			return 1
		}
		shift 2
	done
	mv -f "$ccnavi_rw_tmp" "$ccnavi_rw_file" 2>/dev/null || {
		rm -f "$ccnavi_rw_tmp"
		return 1
	}
}

# そのリポジトリのデフォルトブランチの名前。分からなければ 1。<リポジトリ>
#
# `origin/HEAD` は clone のときに置かれる。`git init` してから `remote add` した手元や、
# 古い clone には無いので、そのときは `origin/main`・`origin/master` の在る側を使う。
# どちらも無ければ「分からない」。
# ネットワークには出ない（手元の ref だけを読む）。
ccnavi_default_branch() {
	ccnavi_db_head=$(git -C "$1" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || :)
	case "$ccnavi_db_head" in
	origin/?*)
		printf '%s\n' "${ccnavi_db_head#origin/}"
		return 0
		;;
	esac
	for ccnavi_db_try in main master; do
		if git -C "$1" rev-parse --verify --quiet "refs/remotes/origin/$ccnavi_db_try" >/dev/null 2>&1; then
			printf '%s\n' "$ccnavi_db_try"
			return 0
		fi
	done
	return 1
}

# そのツリーが属するリポジトリの統合先の名前。分からなければ 1。<ツリー>
#
# 手元の origin/HEAD（無ければ origin/main・origin/master。ccnavi_default_branch）だけで決める。
# 手元の origin/HEAD は ccnavi-fetch.sh がホストに合わせる。
# ccnavi-fetch.sh（ワークツリーの起点を進める）・ccnavi-git.sh（統合先への push の拒否）・
# ccnavi-review.sh（マージリクエストの宛先）が同じ決め方になるよう、ここに 1 つだけ置く。
ccnavi_integration() {
	ccnavi_default_branch "$1"
}

# 統合先が決まらないときの案内の 1 文。<ツリー>
#
# origin が無ければ「origin が無い」と言い、あれば origin/HEAD を直す方法を示す。呼ぶ側が前置きを付ける。
ccnavi_integration_hint() {
	if git -C "$1" remote get-url origin >/dev/null 2>&1; then
		printf '統合先 (origin/HEAD) が決まらない。ユーザに git fetch origin のあと git remote set-head origin -a を頼んでください。'
	else
		printf 'origin が無いので統合先が決まらない。ユーザに origin を足してもらい、git fetch origin のあと git remote set-head origin -a を頼んでください。'
	fi
}

# そのツリーが、名前の親子のチケットの親のワークツリーか。<ツリー> <名前>
#
# 置き場（承認済みの doing/・done/、提案の todo/・review/）に `ticket: <名前>` の親チケットか提案が
# あれば 0（SessionStart で早送りする対象の条件の 1 つ）。ready が退避した後は、手元の退避の親チケットも見る。
# 子チケット（`parent:` を持つ）は数えない。
#
# sh はチケットを読まない。読むのは実行ファイル（`tree parent <ツリー> <名前>`）で、sh は答えの 1 行を見る。
# 親でなければ 1。ツリーが無ければ聞かずに 1。決められなければ 2（実行ファイルが無い・答えない・古くて
# サブコマンドを知らない、置き場に名前のファイルが在るのに frontmatter を読めない）。2 をどちらに倒すかは
# 呼ぶ側が決める。止める側の検査は親として扱い、ツリーを動かす側は親でないとして扱う。
# 実行ファイルの --root に渡すワークスペースルートは `.claude/worktrees/<名前>` の 2 つ上。
ccnavi_parent_tree() {
	ccnavi_parent_info "$1" "$2" || return 2
	case "$ccnavi_pi_parent" in
	yes) return 0 ;;
	no) return 1 ;;
	esac
	return 2
}

# `tree parent` の答えを変数に入れる。ツリーが無ければ 1、答えを得られなければ 2。<ツリー> <名前>
#
# 入れる変数は、どれも yes / no / unreadable / unknown のどれか（unknown は、実行ファイルが無い・答えない）。
#   ccnavi_pi_parent     そのツリーが親のワークツリーか
#   ccnavi_pi_approved   親のワークツリーの承認済み（approved/ の doing か done）に親チケットがある（見るのは承認済みのチケットだけ）
#   ccnavi_pi_requested  承認済みの phases/<名前>/ に <N>.requested が 1 つ以上ある
#   ccnavi_pi_archived   手元の退避（ready 済み）に親がある
#   ccnavi_pi_started    承認済みの親チケットの started_at が空でない（着手は push してから完了する）
#   ccnavi_pi_mr         親チケットの mr 欄（無ければ空）
ccnavi_parent_info() {
	ccnavi_pi_parent=unknown
	ccnavi_pi_approved=unknown
	ccnavi_pi_requested=unknown
	ccnavi_pi_archived=unknown
	ccnavi_pi_started=unknown
	ccnavi_pi_mr=""
	[ -d "$1" ] || return 1
	case "$1" in
	*/.claude/worktrees/*) ccnavi_pi_ws="${1%/.claude/worktrees/*}" ;;
	*) ccnavi_pi_ws=$(ccnavi_workspace) || return 2 ;;
	esac
	ccnavi_pi_out=$(ccnavi_ask "$ccnavi_pi_ws" tree parent "$1" "$2") || return 2
	[ "$(printf '%s\n' "$ccnavi_pi_out" | head -n 1)" = "tree 1" ] || return 2
	for ccnavi_pi_key in parent approved requested archived started; do
		ccnavi_pi_ans=$(printf '%s\n' "$ccnavi_pi_out" | sed -n "s/^$ccnavi_pi_key //p" | head -n 1)
		case "$ccnavi_pi_ans" in
		yes | no | unreadable) ;;
		*) ccnavi_pi_ans=unknown ;;
		esac
		case "$ccnavi_pi_key" in
		parent) ccnavi_pi_parent="$ccnavi_pi_ans" ;;
		approved) ccnavi_pi_approved="$ccnavi_pi_ans" ;;
		requested) ccnavi_pi_requested="$ccnavi_pi_ans" ;;
		archived) ccnavi_pi_archived="$ccnavi_pi_ans" ;;
		started) ccnavi_pi_started="$ccnavi_pi_ans" ;;
		esac
	done
	ccnavi_pi_mr=$(printf '%s\n' "$ccnavi_pi_out" | sed -n 's/^mr //p' | head -n 1)
	[ "$ccnavi_pi_mr" != "-" ] || ccnavi_pi_mr=""
	return 0
}

# 親のブランチを一度は送った形跡があれば 0。ccnavi_parent_info を呼んだ後に使う。<ツリー> <ブランチ>
#
# 形跡は、手元の `refs/remotes/origin/<ブランチ>`（`branch.<ブランチ>.remote` は使わない。origin/main から切った
# だけの未 push の親でも設定されるため）か、実行ファイルが答える .requested・退避・着手済み（started_at）
# （fetch.prune で参照が消えても残る）。承認済みというだけでは形跡にしない（直接作業で提案を書き、同名のワークツリーを
# 切って承認し、最初の push を打つ流れが、初回の push として通らなくなる）。着手（ticket start）は write-and-push で push して
# から完了するので、started_at があれば一度は push している。push の関門（ccnavi_push_gate）と ccnavi-sync.sh の
# 「消えた親」の判定が同じ述語を使う。
ccnavi_parent_trace() {
	if git -C "$1" show-ref --verify --quiet "refs/remotes/origin/$2" 2>/dev/null; then
		return 0
	fi
	[ "$ccnavi_pi_requested" = yes ] || [ "$ccnavi_pi_archived" = yes ] || [ "$ccnavi_pi_started" = yes ]
}

# 親のブランチの push の関門。0 通す、1 拒否、2 確かめられない（拒否側）。
#   ccnavi_push_gate <ツリー> <ブランチ> <識別子> <ワークスペースルート> [<秒>]
# 理由は変数 ccnavi_pg_why に入る（拒否と確かめられないとき）。
#
# 親のワークツリー（<ワークスペースルート>/.claude/worktrees/<識別子> で、ブランチ名から作る識別子がそれと
# 一致し、実行ファイルが親と答える）のブランチだけを見る。通常のブランチと子の push には掛けない。
# 親のブランチがリモートに無いまま push すると、改名・消し間違い・破棄した親子のチケットの理由を確かめないまま
# 同じ名前で作り直してしまう。リモートから消えた形跡があるのにリモートに無いときだけ止める。
#
# 形跡は 2 つ。手元の `refs/remotes/origin/<ブランチ>`（`branch.<ブランチ>.remote` は使わない。origin/main から
# 切っただけの未 push の親でも設定されるため）と、実行ファイルが答える .requested・退避・着手済み
# （fetch.prune で参照が消えても残る）。どちらかがあれば `ls-remote` で在るかを聞く。聞けない・時間切れは 2。
# 承認済みというだけでは形跡にしない（承認して最初に打つ push が、初回として通る）。
# 形跡が無ければ、最初の push として通す。ただし承認済みチケットを読めない（unreadable）ときは、
# 着手済みかを確かめられないので 2。実行ファイルが無い・古くて新しい行を知らないときは、参照の形跡だけで決める。
ccnavi_push_gate() {
	ccnavi_pg_why=""
	ccnavi_pg_tree=$(ccnavi_phys "$1")
	ccnavi_pg_b="$2"
	ccnavi_pg_id="$3"
	ccnavi_pg_ws=$(ccnavi_phys "$4")
	ccnavi_pg_secs="${5:-15}"
	case "$ccnavi_pg_tree" in
	"$ccnavi_pg_ws"/.claude/worktrees/"$ccnavi_pg_id") ;;
	*) return 0 ;;
	esac
	[ "$(ccnavi_branch_to_id "$ccnavi_pg_b" | LC_ALL=C tr 'A-Z' 'a-z')" = "$(printf '%s' "$ccnavi_pg_id" | LC_ALL=C tr 'A-Z' 'a-z')" ] || return 0
	ccnavi_parent_info "$ccnavi_pg_tree" "$ccnavi_pg_id" || :
	[ "$ccnavi_pi_parent" != no ] || return 0
	if ! ccnavi_parent_trace "$ccnavi_pg_tree" "$ccnavi_pg_b"; then
		if [ "$ccnavi_pi_approved" = unreadable ]; then
			ccnavi_pg_why="親のブランチ $ccnavi_pg_b の承認済みチケットを読めないため、リモートから消えた親かを確かめられない。チケットの frontmatter を直してから打ち直してください"
			return 2
		fi
		return 0
	fi
	ccnavi_pg_err=$(mktemp 2>/dev/null || mktemp -t ccnavi-pg) || {
		ccnavi_pg_why="親のブランチ $ccnavi_pg_b がリモートにあるかを確かめられない（一時ファイルを作れない）"
		return 2
	}
	ccnavi_pg_out="$ccnavi_pg_err.out"
	ccnavi_pg_rc=0
	ccnavi_git_timed "$ccnavi_pg_secs" "$ccnavi_pg_err" "$ccnavi_pg_tree" ls-remote --heads origin "refs/heads/$ccnavi_pg_b" >"$ccnavi_pg_out" || ccnavi_pg_rc=$?
	ccnavi_pg_found=""
	if awk -F '\t' -v r="refs/heads/$ccnavi_pg_b" '$2 == r { f = 1 } END { exit f ? 0 : 1 }' "$ccnavi_pg_out" 2>/dev/null; then
		ccnavi_pg_found=yes
	fi
	rm -f "$ccnavi_pg_err" "$ccnavi_pg_out"
	if [ "$ccnavi_pg_rc" -ne 0 ]; then
		ccnavi_pg_why="親のブランチ $ccnavi_pg_b がリモートにあるかを確かめられない（ls-remote が失敗したか、${ccnavi_pg_secs} 秒で終わらなかった）。ネットワークと認証を確かめて打ち直してください"
		return 2
	fi
	[ -z "$ccnavi_pg_found" ] || return 0
	ccnavi_pg_why="親のブランチ $ccnavi_pg_b がリモートに無い。改名・消し間違いなら、ユーザに元の名前で戻してもらい（ユーザがターミナルで git push origin $ccnavi_pg_b を一度打つ）、破棄した親なら親のワークツリーを片付ける。診断は ccnavi-sync.sh $ccnavi_pg_id"
	return 1
}

# ---- タイムアウト監視つきの git（取ってくる操作。ccnavi-fetch.sh と ccnavi-sync.sh が使う）
#
#   ccnavi_git_timed <秒> <標準エラーの書き先> <リポジトリ> <git の引数>...
#
# 認証を尋ねさせず（GIT_TERMINAL_PROMPT=0・GCM_INTERACTIVE=never、ssh は BatchMode）、<秒> で切る。
# ssh の BatchMode は、ユーザが GIT_SSH_COMMAND・GIT_SSH・core.sshCommand を持っていればそちらを尊重する。
# git 用の環境を前置きする薄い層。時間の監視は ccnavi_run_timed（ccnavi-common-host.sh）に任せる。
# 戻り値は git のもの。時間切れで切ったときは 124（ccnavi_run_timed と同じ）。標準出力は呼ぶ側がリダイレクトする。
ccnavi_git_timed() {
	ccnavi_gt_limit="$1"
	ccnavi_gt_err="$2"
	ccnavi_gt_repo="$3"
	shift 3
	ccnavi_git_auth "$ccnavi_gt_repo"
	ccnavi_gt_ssh="${GIT_SSH_COMMAND:-}"
	if [ -z "$ccnavi_gt_ssh" ] && [ -z "${GIT_SSH:-}" ] &&
		! git -C "$ccnavi_gt_repo" config --get core.sshCommand >/dev/null 2>&1; then
		ccnavi_gt_ssh="ssh -o BatchMode=yes"
	fi
	if [ -n "$ccnavi_gt_ssh" ]; then
		ccnavi_run_timed "$ccnavi_gt_limit" /dev/null - "$ccnavi_gt_err" \
			env GIT_SSH_COMMAND="$ccnavi_gt_ssh" GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never LC_ALL=C \
			git -C "$ccnavi_gt_repo" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 "$@"
	else
		ccnavi_run_timed "$ccnavi_gt_limit" /dev/null - "$ccnavi_gt_err" \
			env GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never LC_ALL=C \
			git -C "$ccnavi_gt_repo" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 "$@"
	fi
}

# git が拒んだ理由を 1 行にする（ccnavi-fetch.sh と ccnavi-sync.sh の文面）。<標準エラーを書いたファイル>
#
# 書きかけとの重なり（「would be overwritten」の後に git が字下げして並べるパス）、索引のロック、
# コミットするユーザの名前が無い、署名の失敗、hook、それ以外は git の 1 行目。理由どおりに言い、
# 重なっていないのに「重なる」とは言わない。
ccnavi_git_refusal() {
	ccnavi_gr_paths=$(awk '/would be overwritten/ { f = 1; next }
		/^[^ \t]/ { f = 0 }
		f && /^[ \t]+[^ \t]/ { sub(/^[ \t]+/, ""); printf "%s%s", sep, $0; sep = " " }' "$1" 2>/dev/null)
	if [ -n "$ccnavi_gr_paths" ]; then
		printf '書きかけの %s と重なる。コミットか退避をしてから再実行してください' "$ccnavi_gr_paths"
	elif grep -q 'index\.lock' "$1" 2>/dev/null; then
		printf '索引のロック（index.lock）が残っている。別の git が動いていないか確かめ、異常終了して残ったものならユーザが削除します。'
	elif grep -Eqi 'tell me who you are|empty ident|user\.email|user\.name' "$1" 2>/dev/null; then
		printf 'コミットするユーザの名前（user.name・user.email）が決まっていない'
	elif grep -Eqi 'gpg|signing' "$1" 2>/dev/null; then
		printf 'コミットの署名に失敗した'
	elif grep -qi 'hook' "$1" 2>/dev/null; then
		printf 'git の hook が止めた（%s）' "$(head -n 1 "$1")"
	else
		printf 'git が拒んだ（%s）' "$(head -n 1 "$1" 2>/dev/null)"
	fi
}

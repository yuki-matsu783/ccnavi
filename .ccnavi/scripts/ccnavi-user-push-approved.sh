#!/bin/sh
# ccnavi-user-push-approved 承認済みチケットの置き場（と、承認で todo/ から消えた提案、取り込んで消えた
# フローとテンプレートの下書き）だけをコミットし、
# 保護されたブランチでなければ push する。
# ユーザだけが打つ（ボードがターミナルへ送る 1 行も、ユーザが打つ）。エージェントからは hook が名前で止める。
#
#   sh .ccnavi/scripts/ccnavi-user-push-approved.sh [<親>...]
#   sh .ccnavi/scripts/ccnavi-user-push-approved.sh --path <パス> [--path <パス>...]
#
# 承認済みチケットは置かれただけでは他の機械に届かないので、置いたあとに
# コミットして push するのがこの sh。ターミナルの承認（ccnavi-user-agree.sh）、ボードの承認とフローの保存
# （ターミナルに送った 1 行）、ユーザの判断（ccnavi-user-close-early.sh）が、それぞれ最後にこの sh を呼ぶ。
# ユーザの判断を溜めずにその場で送るためで、送れなければ次の write-and-push が止まり、この sh の打ち直しを案内する。
# 対になるのはセッションの頭に取ってくる ccnavi-fetch.sh。
#
# 取り込み済みの親子のチケット（origin があり、write-and-push の対象に入るもの）のワークツリーは、
# write-and-push と同じ手順でコミットして push する。取り込んでから送るので、Chrome での承認と重なっても push が拒まれにくい。
# 手順は、ロック（write-and-push の中からの入れ子を許す）→ 途中の操作の確認 → 取り込み（ccnavi-sync.sh）→
# 置き場（承認済みと、レビュー待ちの review/ と、承認で消えた todo/ の提案と、取り込んで消えた flows/ の下書き）を commit --only → push → 失敗したように見えたら届いたかを ls-remote で確かめる。
# push が失敗してもコミットは残す（ユーザが打ち直せる）。取り込み済みかは実行ファイル（`write-and-push family`）に聞く。
# 取り込みの後、push の前に、親のブランチがリモートから消えていないかを push のゲート（ccnavi_push_gate）で確かめる。
# 消えていれば（確かめられないときも）コミットも push もせずに止める。親でないツリーの push の前にも同じゲートを通す。
#
# <親> を並べると、その親子のチケットだけをコミットして push する。取り込み済みでない親子のチケットは
# コミットしない（今のまま、ユーザがコミットする）。省けば今どおり、置き場に変更のあるツリー全部。
#
# `--path <パス>` を並べると、置き場の変更のうちそのパスだけをコミットして push する。パスはワークスペース
# ルートからの相対（区切りは /）。承認が実際に書いた・消したパスの一覧（`ccnavi user agree` の
# `--record-writes`、`--yes --json` の `written`）を渡す。同じ置き場にある承認と無関係な変更
# （review/ の変更や、前の失敗で残ったファイル）を承認のコミットに入れないため。並べたパスの無いツリーは
# 見ない（子のワークツリーの置き場に変更があっても、失敗に数えない）。<親> とは同時に指定できない。
#
# 数えるツリーは、ワークスペース、projects の下、.claude/worktrees の下。
# 置き場は .ccnavi/approved（どの置き場も固定）。承認では提案を
# wip/proposals の todo/ から動かすので、
# そこで追跡されていたファイルの削除も同じコミットに入れる。
# 同じく、ボードのフロー編集画面が取り込んだ下書き（wip/proposals の flows/）を
# フローの保存のあとに消す。
# 追跡されていた下書きの削除だけをコミットする。`ccnavi user handoff` の下書き（wip/handoff/）は
# 追跡しないので、ここでは扱わない。
# 消えたものだけをここで拾う。
#
# - コミットはパスを限る（他人の書きかけを巻き込まないため）
# - シンボリックリンクは辿らない。置き場（projects/ や .claude/worktrees/）そのものも、その下の
#   1 件ずつも。辿るとワークスペースの外のリポジトリにコミットして push する
# - ブランチの上に居ない（detached）ツリーは名指しして処理しない。失敗には数えない
# - 保護されたブランチ（ccnavi_is_protected。main / master / develop / release / release/* / release-*）と、そのリポジトリの統合先（ccnavi-common-state.sh の
#   ccnavi_integration。決まらなければ固定のリストだけ）はコミットだけして push しない
# - 1 本のツリーで add・commit・push が失敗しても、他のツリーはコミットして push する
#
# 実行ファイルを起こさずに動く作り（承認の直後、実行ファイルの入れ替え中でも push できるように）。実行ファイルが
# 答えないときに省くもの:
# - 取り込み済みの親子のチケット（write-and-push の対象）のワークツリーは、取り込めないので書かずに止める（今までどおり）
# - 統合先（ccnavi_integration）は決まらないので、固定の保護されたブランチ（ccnavi_is_protected）だけで送らないと決める
# - push のゲートは親かを確かめられないので、手元に origin の参照があるブランチだけ ls-remote で確かめる
# - git が断った理由（ccnavi_git_refusal）は読み分けず、git の 1 行目だけを添える
# - push に失敗したツリーの未送信のパスの一覧（unsent_paths。実行ファイルの write-and-push unsent）は出さない
#
# 実行ファイルに任せるのは、実行ファイルが無いときに省いても push が止まらないものだけ。取り込み済みの親子のチケットの
# 途中の操作の確かめとコミット（write-and-push busy・commit）は、実行ファイルが取り込み済みと答えたときだけ通る道なので
# 実行ファイルに任せる。ツリーの列挙とリンクの検査、置き場の変更のパス（carry_paths・given_in）、親子のチケットでない
# ツリーのコミットは、実行ファイルが無くても承認を送れるようにここに残す（sh で実装するもののうち「実行ファイルを
# 起こさずに動くもの」）。そのために持つ規則の写しと、実行ファイルとの一致を確かめる表:
# - 置き場の名前（ccnavi-common.sh の ccnavi_*_dir。tests/fixtures/layout.json）
# - status の読みと書きかけの一時ファイル（ccnavi_porcelain_paths・ccnavi_drop_temp）と、--path のパスの大文字小文字を
#   区別しない比べ（ccnavi_path_under・ccnavi_path_pick）。tests/fixtures/approved-carry.json
# - 識別子の形と保護されたブランチ（ccnavi_is_ident・ccnavi_is_protected。tests/fixtures/ident-cases.json）
# - push の資格情報を選ぶための origin の読み（ccnavi_git_auth の ccnavi_host_parse。tests/fixtures/host-kinds.json）
#
# 終了コード: 0 コミットするものが無い・全部コミットした（push しなかったブランチ、処理しなかったツリーを含む） /
#           1 ステージかコミットできなかったツリーか、push が失敗したツリーが 1 つ以上ある /
#           2 引数の誤り・ワークスペースルートが見つからない・一時ファイルが作れない

set -eu

. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-push-approved.sh [<親>...]
sh .ccnavi/scripts/ccnavi-user-push-approved.sh --path <パス> [--path <パス>...]

  承認済みチケットの置き場に変更があるツリーごとに、その置き場だけをコミットし、
  保護されたブランチでなければ push する。取り込み済みの親子のチケットの親のワークツリーは、
  取り込んでから送る（write-and-push と同じ手順）。<親> を並べるとその親子のチケットだけ。
  --path を並べると、置き場の変更のうちそのパス（ワークスペースルートからの相対）だけ。
USAGE
}

case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
esac
# --path で渡したパス（1 行 1 つ）。空なら今どおり、置き場の変更を全部。
given=""
named=""
nl='
'
while [ "$#" -gt 0 ]; do
	case "$1" in
	--path)
		if [ "$#" -lt 2 ]; then
			printf 'ccnavi-user-push-approved: --path の後ろにパスがありません。\n' >&2
			usage >&2
			exit 2
		fi
		case "$2" in
		"" | /* | ../* | *"$nl"*)
			printf 'ccnavi-user-push-approved: --path にはワークスペースルートからの相対パスを渡してください（%s）。\n' "$2" >&2
			exit 2
			;;
		esac
		given="$given$2$nl"
		shift 2
		;;
	*)
		if ! ccnavi_is_ident "$1"; then
			printf 'ccnavi-user-push-approved: %s は親の識別子の形ではありません。\n' "$1" >&2
			usage >&2
			exit 2
		fi
		named="$named $1"
		shift
		;;
	esac
done
if [ -n "$given" ] && [ -n "$named" ]; then
	printf 'ccnavi-user-push-approved: --path と <親> は一緒に指定できません。\n' >&2
	exit 2
fi
# 識別子は空白も glob の字も持たない（上の ccnavi_is_ident で確かめた）ので、分けて並べ直してよい。
set -f
# shellcheck disable=SC2086
set -- $named
set +f

# 置き場の操作はルートの下だけで cwd に依らないので、ワークスペースの外の端末からも打てる。
root=$(ccnavi_workspace_anywhere) || ccnavi_no_workspace ccnavi-user-push-approved

# 置き場の名前は ccnavi-common.sh の写し（実行ファイルとの一致は tests/fixtures/layout.json）。
approved="$ccnavi_approved_dir"
proposals="$ccnavi_proposals_dir"
projects="$ccnavi_projects_dir"
worktrees="$ccnavi_worktrees_dir"

# ツリーごとの結果を subshell (while はパイプの右側なので別プロセス) の外へ持ち出すための一時ファイル。
state=$(mktemp "${TMPDIR:-/tmp}/ccnavi-user-push-approved.XXXXXX") || {
	printf 'ccnavi-user-push-approved: 一時ファイルが作れません。\n' >&2
	exit 2
}
trap 'rm -f "$state"; ccnavi_wap_end' EXIT
trap 'rm -f "$state"; ccnavi_wap_end; exit 130' INT TERM HUP

# ---- 取り込み済みの親子のチケットは write-and-push と同じ手順でコミットして push する
ccnavi_log_root="$root"
ccnavi_wap_root="$root"
ccnavi_wap_label=ccnavi-user-push-approved
ccnavi_wap_sh="$(dirname "$0")"
# 実行ファイルが無ければ ccnavi_wap_exe は 1 を返すだけになり、取り込み済みかを聞けない親子のチケットは止まる。
ccnavi_wap_use_exe "$root" || :
tab=$(printf '\t')

# 置き場の変更のパス（承認済み、yes ならレビュー待ちの review/ も、承認で消えた todo/ の提案、取り込んで消えた
# flows/ の下書き）を ccnavi_wap_tmp/carry に。<ツリー> <yes|no（review/ を含めるか）>
# 書きかけの一時ファイル（ccnavi_drop_temp）はコミットしない。
#
# 実行ファイルを起こさずに動くための写し（status の読みは ccnavi_porcelain_paths、一時ファイルは ccnavi_drop_temp。
# どちらも ccnavi-common-write-and-push.sh）。親子のチケットでないツリーの承認を、実行ファイルの入れ替え中でも送るため。
carry_paths() {
	: >"$ccnavi_wap_tmp/carry"
	cp_review=""
	[ "$2" = yes ] && cp_review="$proposals/review"
	git -C "$1" -c core.quotepath=false status --porcelain -z --untracked-files=all --no-renames \
		-- "$approved" ${cp_review:+"$cp_review"} 2>/dev/null | ccnavi_porcelain_paths >"$ccnavi_wap_tmp/carry-all" || :
	git -C "$1" ls-files --deleted -z -- "$proposals/todo" "$proposals/flows" 2>/dev/null | tr '\000' '\n' \
		>>"$ccnavi_wap_tmp/carry-all" || :
	ccnavi_drop_temp <"$ccnavi_wap_tmp/carry-all" >"$ccnavi_wap_tmp/carry"
	# --path で渡したときは、そのうち渡したパスだけ。
	if [ -n "$given" ]; then
		given_in "$1" >"$ccnavi_wap_tmp/given"
		if [ -s "$ccnavi_wap_tmp/given" ]; then
			ccnavi_path_pick "$ccnavi_wap_tmp/given" <"$ccnavi_wap_tmp/carry" >"$ccnavi_wap_tmp/carry-given" || :
			mv "$ccnavi_wap_tmp/carry-given" "$ccnavi_wap_tmp/carry"
		else
			: >"$ccnavi_wap_tmp/carry"
		fi
	fi
}

# --path で渡したパスのうち、ツリー $1 の中のものを、そのツリーからの相対で 1 行ずつ。大文字小文字を区別せず比べる
# （ccnavi_path_under）。ワークスペースルートのツリーには、projects/ と .claude/worktrees/ の下（別のツリー）を入れない。
given_in() {
	printf '%s' "$given" | while IFS= read -r gi_path; do
		[ -n "$gi_path" ] || continue
		if [ "$1" = "$root" ]; then
			ccnavi_path_under "$gi_path" "$projects" >/dev/null && continue
			ccnavi_path_under "$gi_path" "$worktrees" >/dev/null && continue
			printf '%s\n' "$gi_path"
		elif gi_rel=$(ccnavi_path_under "$gi_path" "${1#"$root"/}") && [ "$gi_rel" != . ]; then
			printf '%s\n' "$gi_rel"
		fi
	done
}

# まだ送れていないパス（リモートの先頭との差。リモートの先頭が無ければ直近のコミットの分）を 1 行ずつ。
# push に失敗したとき、何が未送信かを言うため。差は実行ファイル（write-and-push unsent）が読む。実行ファイルが
# 答えないときは言わない（送れなかったことは呼ぶ側が言っている）。$1 ツリー $2 ブランチ
unsent_paths() {
	ccnavi_val unsent "$(ccnavi_wap_exe write-and-push unsent "$1" "$2" </dev/null 2>/dev/null)" | sed 's/^/  未送信: /' >&2 || :
}

# 取り込み済みの親子のチケット 1 つをコミットして push する。ccnavi_wap_family を済ませた後に呼ぶ。0 送った（push するものが無いを含む）/ 1 失敗した
#
# ロックは識別子（ccnavi_wap_family_id）、ref・push・ls-remote は
# 親のブランチ名（ccnavi_wap_branch。親のワークツリーの HEAD から読む）。
carry_family() {
	cf_p="$ccnavi_wap_family_id"
	cf_b="$ccnavi_wap_branch"
	cf_tree="$ccnavi_wap_tree"
	cf_rc=0
	ccnavi_lock_take "$root" "$ccnavi_wap_repo" "$cf_p" "$(ccnavi_number_or "${CCNAVI_LOCK_WAIT:-}" 120)" || cf_rc=$?
	if [ "$cf_rc" -ne 0 ]; then
		if ccnavi_lock_long "$(ccnavi_state "$root")/locks/$ccnavi_wap_repo/$cf_p"; then
			printf 'ccnavi-user-push-approved: %s のロックが 10 分を超えて取られたままです。持ち主はまだ動いているため、強制取得はしません。終わるのを待つか、持ち主をユーザが確かめてください。%s\n' \
				"$cf_p" "$(ccnavi_lock_describe "$(ccnavi_state "$root")/locks/$ccnavi_wap_repo/$cf_p")" >&2
		else
			printf 'ccnavi-user-push-approved: %s のロックを他の操作が持っている。終わってから再実行してください。\n' "$cf_p" >&2
		fi
		return 1
	fi
	cf_busy=$(ccnavi_wap_busy "$cf_tree")
	if [ -n "$cf_busy" ]; then
		printf 'ccnavi-user-push-approved: %s の親のワークツリーに途中の操作（%s）がある。済ませてから再実行してください。\n' "$cf_p" "$cf_busy" >&2
		ccnavi_lock_drop
		return 1
	fi
	# 取り込んでから送る（Chrome の承認と重なっても push が拒まれにくい）。衝突したら取りやめてユーザの対応に切り替える。
	cf_rc=0
	sh "$(dirname "$0")/ccnavi-sync.sh" "$cf_p" </dev/null >"$ccnavi_wap_tmp/sync" 2>&1 || cf_rc=$?
	sed 's/^/  /' "$ccnavi_wap_tmp/sync" >&2
	if [ "$cf_rc" -ne 0 ]; then
		printf 'ccnavi-user-push-approved: %s を取り込めなかった（上の ccnavi-sync.sh の文面）。ユーザの判断はまだ送っていません。直してから再実行してください。\n' "$cf_p" >&2
		ccnavi_lock_drop
		return 1
	fi
	# 親のブランチがリモートから消えていれば、push で作り直さずに止める（push のゲート。write-and-push・ccnavi-git.sh push と同じ）。
	cf_rc=0
	ccnavi_push_gate "$cf_tree" "$cf_b" "$cf_p" "$root" "$(ccnavi_number_or "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" 60)" || cf_rc=$?
	if [ "$cf_rc" -ne 0 ]; then
		printf 'ccnavi-user-push-approved: %s。ユーザの判断はまだ送っていません。\n' "$ccnavi_pg_why" >&2
		ccnavi_lock_drop
		return 1
	fi
	carry_paths "$cf_tree" yes
	if [ -s "$ccnavi_wap_tmp/carry" ]; then
		# コミットは write-and-push と同じ実行ファイルの口（パスを限る・タイムアウト・失敗したら索引を戻す）。
		# ここに来るのは実行ファイルが取り込み済みと答えた親子のチケットだけなので、実行ファイルはある。
		if ! ccnavi_wap_commit "$ccnavi_wap_tmp/carry" "ccnavi: 承認済みチケットを更新"; then
			[ -z "$ccnavi_wap_refused" ] ||
				printf 'ccnavi-user-push-approved: %s で承認済みチケットをコミットできない（%s）。\n' "$cf_p" "$ccnavi_wap_refused" >&2
			ccnavi_lock_drop
			return 1
		fi
	fi
	cf_head=$(git -C "$cf_tree" rev-parse HEAD)
	if [ "$cf_head" = "$(git -C "$cf_tree" rev-parse --verify -q "refs/remotes/origin/$cf_b" 2>/dev/null || :)" ]; then
		printf '%s: コミットして push するものはありません。\n' "$cf_p"
		ccnavi_lock_drop
		return 0
	fi
	cf_timeout=$(ccnavi_number_or "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" 60)
	if ccnavi_git_timed "$cf_timeout" "$ccnavi_wap_tmp/push-err" "$cf_tree" \
		push --quiet origin "refs/heads/$cf_b:refs/heads/$cf_b" >/dev/null ||
		{ ccnavi_git_timed "$cf_timeout" "$ccnavi_wap_tmp/ls-err" "$cf_tree" \
			ls-remote origin "refs/heads/$cf_b" >"$ccnavi_wap_tmp/ls" &&
			grep -F -x -q -- "$cf_head${tab}refs/heads/$cf_b" "$ccnavi_wap_tmp/ls"; }; then
		printf '承認済みチケットを取り込んだうえで、ブランチ %s にpushしました。\n' "$cf_b"
		ccnavi_lock_drop
		return 0
	fi
	# 失敗してもコミットは残す（ユーザが打ち直せる）。write-and-push は未送信を見つけて止まり、これを打ち直すよう言う。
	printf 'ccnavi-user-push-approved: %s の push に失敗しました（%s）。コミットはローカルに残っています。接続を確認してから、次のコマンドを再実行してください: sh %s/ccnavi-user-push-approved.sh %s\n' \
		"$cf_p" "$(head -n 1 "$ccnavi_wap_tmp/push-err" 2>/dev/null)" "$(dirname "$0")" "$cf_p" >&2
	unsent_paths "$cf_tree" "$cf_b"
	ccnavi_lock_drop
	return 1
}

# 親子のチケットを名指しされたとき。取り込み済みならコミットして push し、そうでなければ何もしない（今のまま）。
if [ "$#" -gt 0 ]; then
	named_rc=0
	for want in "$@"; do
		ccnavi_wap_family "$want"
		case "$ccnavi_wap_target" in
		yes) carry_family || named_rc=1 ;;
		stop)
			ccnavi_wap_refuse
			named_rc=1
			;;
		*)
			printf '%s: 取り込み済みの親子のチケットでない（%s）。ここではコミットも push もしない（今のまま、ユーザがコミットする）。\n' \
				"$want" "${ccnavi_wap_why:-対象外}"
			;;
		esac
	done
	exit "$named_rc"
fi

# ワークスペースルートから rel を 1 階層ずつ下り、シンボリックリンクの階層があれば 0。
# その階層（ルートからのパス）を linked に残す。`.claude` だけがリンクでも見逃さない。
linked=""
linked_segment() {
	linked=""
	rest="$1"
	walked=""
	while [ -n "$rest" ]; do
		segment="${rest%%/*}"
		if [ "$segment" = "$rest" ]; then
			rest=""
		else
			rest="${rest#*/}"
		fi
		[ -n "$segment" ] || continue
		walked="${walked:+$walked/}$segment"
		if [ -L "$root/$walked" ]; then
			linked="$walked"
			return 0
		fi
	done
	return 1
}

# 数えるのは 置き場 → その下の 1 件ずつ、の 2 階層。どちらの階層でもリンクは辿らない。
trees="$root"
for place in "$projects" "$worktrees"; do
	if linked_segment "$place"; then
		printf 'ccnavi-user-push-approved: %s はシンボリックリンクなので、その下を辿りません。\n' "$linked" >&2
		continue
	fi
	[ -d "$root/$place" ] || continue
	for dir in "$root/$place"/*; do
		if [ -L "$dir" ]; then
			printf 'ccnavi-user-push-approved: %s はシンボリックリンクなので辿りません。\n' \
				"$place/$(basename "$dir")" >&2
			continue
		fi
		# glob が何にもヒットしなければパターンがそのまま残るので、-d で除く。
		[ -d "$dir" ] || continue
		trees="$trees
$dir"
	done
done

# 一時の置き場は先に作る（下のループはパイプの右側の別プロセスで、そこで作ると後始末が届かない）。
ccnavi_wap_scratch || {
	printf 'ccnavi-user-push-approved: 一時ディレクトリが作れません。\n' >&2
	exit 2
}
printf '%s\n' "$trees" | while IFS= read -r tree; do
	[ -n "$tree" ] || continue
	[ -d "$tree/$approved" ] || continue
	git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
	# 置き場の変更（--path で渡したときは、そのうちこのツリーのもの）。無ければこのツリーは見ない。
	# 渡さないときは review/ を含めない（ユーザの判断の途中のものを巻き込まない）。
	if [ -n "$given" ]; then
		carry_paths "$tree" yes
	else
		carry_paths "$tree" no
	fi
	[ -s "$ccnavi_wap_tmp/carry" ] || continue

	# 何か 1 つでもコミットする対象があったことの記録。detached で処理しなくても「無い」とは言わない。
	printf 'seen\n' >>"$state"

	name=$(basename "$tree")
	branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
		printf 'ccnavi-user-push-approved: %s は現在のHEADがブランチに属していません（detached HEAD）。承認済みチケットは手でコミットしてください。\n' \
			"$name" >&2
		continue
	fi

	# 取り込み済みの親子のチケットの親のワークツリー（ディレクトリ名 = 識別子。ブランチは HEAD のブランチで、
	# その / を - に置き換えたものが識別子）は、取り込んでから送る。
	case "$tree" in
	"$root/$worktrees/"*)
		ccnavi_wap_family "$name"
		if [ "$ccnavi_wap_target" != no ] && [ "$ccnavi_wap_family_id" != "$name" ]; then
			# 取り込み済みの親子のチケットの子のワークツリー。チケットとマーカーは親のワークツリーに置くので、
			# 子のツリーの置き場の変更はコミットしない（子のブランチはリモートに出さない）。
			printf 'ccnavi-user-push-approved: %s は親子のチケット %s の子のワークツリー。子のツリーの置き場の変更はコミットしない（チケットは親のワークツリーに置く）。ユーザが中身を確かめる。\n' \
				"$name" "$ccnavi_wap_family_id" >&2
			printf 'fail\n' >>"$state"
			continue
		fi
		case "$ccnavi_wap_target" in
		yes)
			if grep -F -x -q -- "$ccnavi_wap_family_id" "$ccnavi_wap_tmp/carried" 2>/dev/null; then
				continue
			fi
			printf '%s\n' "$ccnavi_wap_family_id" >>"$ccnavi_wap_tmp/carried"
			carry_family </dev/null || printf 'fail\n' >>"$state"
			continue
			;;
		stop)
			ccnavi_wap_refuse
			printf 'fail\n' >>"$state"
			continue
			;;
		esac
		;;
	esac

	# 置き場の変更だけ。1 件ずつステージして、そのパスだけをコミットする（ほかのステージ済みを巻き込まない）。
	while IFS= read -r one; do
		[ -n "$one" ] || continue
		git -C "$tree" add -A -- ":(literal)$one" 2>/dev/null || :
	done <"$ccnavi_wap_tmp/carry"
	sed 's/^/:(literal)/' "$ccnavi_wap_tmp/carry" | tr '\n' '\000' >"$ccnavi_wap_tmp/pathspec"
	git -C "$tree" commit --quiet --only -m "ccnavi: 承認済みチケットを更新" \
		--pathspec-from-file="$ccnavi_wap_tmp/pathspec" --pathspec-file-nul || {
		printf 'ccnavi-user-push-approved: %s で承認済みチケットをコミットできない。\n' "$name" >&2
		printf 'fail\n' >>"$state"
		continue
	}
	if ccnavi_is_protected "$branch"; then
		printf 'ccnavi-user-push-approved: %s では保護対象のブランチ %s を使用しているため、pushしません。送るかどうかはユーザが決めます。\n' \
			"$name" "$branch" >&2
		continue
	fi
	# 固定のリストに無い名前の統合先（develop-v1.0.0 など）も送らない。名前は ccnavi-fetch.sh・
	# ccnavi-git.sh と同じ決め方（origin/HEAD → origin/main・master）で、そのツリーが属する
	# リポジトリについて決める。決まらなければ固定のリストだけ。
	integ=$(ccnavi_integration "$tree") || integ=""
	if [ -n "$integ" ] && [ "$branch" = "$integ" ]; then
		printf 'ccnavi-user-push-approved: %s では保護対象の統合先 %s を使用しているため、pushしません。送るかどうかはユーザが決めます。\n' \
			"$name" "$branch" >&2
		continue
	fi

	# 親のワークツリーのブランチは、リモートから消えていないことを確かめてから送る（push のゲート）。
	# 親でないツリー・初回の push はゲートが通す。
	gate_rc=0
	ccnavi_push_gate "$tree" "$branch" "$name" "$root" "$(ccnavi_number_or "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" 60)" || gate_rc=$?
	if [ "$gate_rc" -ne 0 ]; then
		printf 'ccnavi-user-push-approved: %s。コミットはローカルに残っている。\n' "$ccnavi_pg_why" >&2
		unsent_paths "$tree" "$branch"
		printf 'fail\n' >>"$state"
		continue
	fi

	# push は失敗しても巻き戻さない。コミットは残るので、ユーザがもう一度送れる。
	ccnavi_git_auth "$tree"
	if git -C "$tree" push --quiet -u origin "$branch" 2>/dev/null; then
		printf '承認済みチケットをブランチ %s にpushしました（%s）。\n' "$branch" "$name"
	else
		printf 'ccnavi-user-push-approved: %s の push に失敗しました。手で送ってください（git push -u origin %s）。\n' \
			"$name" "$branch" >&2
		unsent_paths "$tree" "$branch"
		printf 'fail\n' >>"$state"
	fi
done

if [ ! -s "$state" ]; then
	printf 'コミットして push する承認済みチケットはありません。\n'
	exit 0
fi
grep -q '^fail$' "$state" && exit 1
exit 0

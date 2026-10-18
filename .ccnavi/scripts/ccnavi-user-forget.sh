#!/bin/sh
# ccnavi-user-forget 捨てた親子のチケットの取り込み状態を消す。ユーザが端末で打つ。
#
#   sh .ccnavi/scripts/ccnavi-user-forget.sh <P>...
#
# 前は `ccnavi-sync.sh --forget <P>...` だった。ユーザだけが打つ sh は ccnavi-user-*.sh に分けてあり、
# エージェントからは hook が名前で止める。
#
# 親子のチケットの取り込み状態は、親のワークツリーを片付けても消さない（ccnavi-sync.sh の冒頭）。消すと、
# 決まらないで止めていた親子のチケット（gone など）が取り込み状態の無いものに戻り、止めが外れるため。
# ここはその取り込み状態を、ユーザが捨てると決めた親子のチケットについてだけ消す。親のワークツリー
# （.claude/worktrees/<P>）がまだあれば消さない。ネットワークも実行ファイルも使わない。
#
# <P> は親の識別子（= .claude/worktrees/<P>）。リポジトリごとの置き場（state の sync/<リポジトリ>/families/<P>）を
# 全部見て、ロックを取ってから消す。置き場がシンボリックリンクなら辿らずに残す。
#
# 環境変数: CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）
# 終了コード: 0 全部消した（消すものが無いを含む） / 1 消さなかったものがある / 2 引数か環境の誤り

set -eu

. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-forget.sh <P>...

  ユーザが実行します。捨てた親子のチケット <P>（識別子）の取り込み状態を削除します
  （親のワークツリーを片付けた後だけ）。削除すると、その名前で切り直した親子のチケットは
  取り込み状態の無いものとして扱われる。
USAGE
}

case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
'')
	printf 'ccnavi-user-forget: 親の識別子が要ります。\n' >&2
	usage >&2
	exit 2
	;;
esac

for want in "$@"; do
	if ! ccnavi_is_ident "$want"; then
		printf 'ccnavi-user-forget: %s は親の識別子の形ではありません（親のブランチ名ではなく識別子を渡します）。\n' "$want" >&2
		exit 2
	fi
done

root=$(ccnavi_workspace) || {
	printf 'ccnavi-user-forget: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}
ccnavi_log_root="$root"
state=$(ccnavi_state "$root")
here_sh="$(dirname "$0")"
lock_wait=$(ccnavi_c1_number "${CCNAVI_LOCK_WAIT:-}" 120)

trap 'ccnavi_lock_drop' EXIT
trap 'ccnavi_lock_drop; exit 130' INT TERM HUP

log_info 受け付けた -- "args=$#"

forget_rc=0
for want in "$@"; do
	if [ -e "$root/.claude/worktrees/$want" ] || [ -L "$root/.claude/worktrees/$want" ]; then
		printf '%s: 親のワークツリー（.claude/worktrees/%s）がまだある。取り込み状態を削除するのは捨てた親子のチケットだけ。先に片付けてから実行してください（sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s）\n' \
			"$want" "$want" "$here_sh" "$want"
		forget_rc=1
		continue
	fi
	fg_found=no
	for fg_record in "$state"/sync/*/families/"$want"; do
		[ -e "$fg_record" ] || [ -L "$fg_record" ] || continue
		fg_key="${fg_record%/families/*}"
		fg_key="${fg_key##*/}"
		if [ -L "$state/sync/$fg_key" ] || [ -L "$state/sync/$fg_key/families" ]; then
			printf '%s: 取り込み状態の置き場（sync/%s）がシンボリックリンク。辿らないので削除しません。ユーザが中身を確かめる\n' "$want" "$fg_key"
			forget_rc=1
			continue
		fi
		fg_found=yes
		fg_lock_rc=0
		ccnavi_lock_take "$root" "$fg_key" "$want" "$lock_wait" || fg_lock_rc=$?
		if [ "$fg_lock_rc" -ne 0 ]; then
			printf '%s: 他の操作がロックを持っている。終わってから再実行してください\n' "$want"
			forget_rc=1
			continue
		fi
		fg_state=$(ccnavi_record_get "$fg_record" state)
		if rm -f "$fg_record"; then
			printf '%s: 親子のチケットの取り込み状態（sync/%s/families/%s。state %s）を削除しました。この名前の親子のチケットは取り込み状態の無いものとして扱われる\n' \
				"$want" "$fg_key" "$want" "${fg_state:-?}"
			log_info 親子のチケットの取り込み状態を削除した -- "family=$want" "repo=$fg_key" "state=$fg_state"
		else
			printf '%s: 親子のチケットの取り込み状態（sync/%s/families/%s）を削除できませんでした。\n' "$want" "$fg_key" "$want"
			forget_rc=1
		fi
		ccnavi_lock_drop
	done
	[ "$fg_found" = yes ] || printf '%s: 親子のチケットの取り込み状態はありません（削除するものはありません）\n' "$want"
done
exit "$forget_rc"

#!/bin/sh
# ccnavi-clean ワークツリー 1 本の生成物を消す（--worktree は、無視されたファイルの退避とワークツリーの削除まで行う）。
# `git worktree remove` の前に打つ。
#
#   sh .ccnavi/scripts/ccnavi-clean.sh <名前>
#   sh .ccnavi/scripts/ccnavi-clean.sh <名前> --dry-run
#   sh .ccnavi/scripts/ccnavi-clean.sh --worktree <名前>
#
# 親を閉じたときに残した子の手元のブランチを消す入口は、ユーザ専用の ccnavi-user-clean-branch.sh。
#
# --worktree は生成物の掃除と `git worktree remove`（--force なし）を 1 本で行う。ccnavi が ready の
# 最後や confirm の後に消せなかったワークツリー（cwd が中にあった、など）を、外から消し直すための入口。
# 中身は ccnavi の実行ファイルの `worktree drop <名前>`（消すかどうかの判断も実行ファイルが持つ）。
# cwd がそのワークツリーの中なら消さない。未コミットの変更があれば何も消さない。ブランチは消さない。
#
# Windows では、pnpm の node_modules が深すぎる（260 文字を超える）ことと、uv の
# .venv が使用中であることで、`git worktree remove` が途中で止まり、消しきれなかったディレクトリが残る。
# 先に生成物だけを消しておく。
#
# <名前> は .claude/worktrees/ の直下のディレクトリの名前。パスは受け付けない。
# `rm -rf` の代わりに任意の場所を消す手段にしないためで、ccnavi の builtin:recursive-delete の
# 趣旨（消す対象を名指しする）に合わせてある。
#
# 消すのは、作り直せば戻る決まった名前のディレクトリだけ。何を消すかと消し方は実行ファイル
# （src/ccnavi/tickets/lifecycle/worktrees.py の run_clean）にある。
#
# ワークツリーに未コミットの変更があれば、何も消さずに止める。別のセッションが
# そこで作業している見込みが高い。git に登録の残っていない、消しきれなかったディレクトリ（.git が無い、
# または .git が指す先が消えている）は確かめようが無いので、確かめずに進める。
# 消すのは生成物だけなので、書きかけは残る。
#
# 終了コード: 0 成功 / 1 前提の未充足か消し残し / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-clean.sh <名前> [--dry-run]
sh .ccnavi/scripts/ccnavi-clean.sh --worktree <名前>

  <名前>      .claude/worktrees/ の直下の名前。パスは書けない
  --dry-run   削除するものを並べるだけで、削除しない
  --worktree  生成物を削除してから、ワークツリーそのものも削除します（git worktree remove。--force なし）。
              cwd がその中なら削除しません。ブランチは残す。このときの <名前> はチケットの識別子
              （.claude/worktrees/<識別子>）だけを受ける。識別子の形でない名前は断る

削除するもの: node_modules / .venv / __pycache__ などのキャッシュと、印のファイル（package.json・Cargo.toml など）の隣の out / dist / .cache / target / build（一覧は worktrees.py）
未コミットの変更があるワークツリーでは、何も削除せずに止まる
USAGE
}

name=""
dry=""
whole=""
for arg in "$@"; do
	case "$arg" in
	-h | --help | help)
		usage
		exit 0
		;;
	--dry-run)
		dry=1
		;;
	--worktree)
		whole=1
		;;
	-*)
		printf 'ccnavi-clean: %s は指定できません。指定できるのは --dry-run・--worktree だけです。\n' "$arg" >&2
		exit 2
		;;
	*)
		if [ -n "$name" ]; then
			printf 'ccnavi-clean: ワークツリーは 1 回に 1 本だけ指定します（%s と %s）。\n' "$name" "$arg" >&2
			exit 2
		fi
		name="$arg"
		;;
	esac
done

[ -n "$name" ] || {
	usage
	exit 2
}

case "$name" in
. | .. | */* | *\\* | *:*)
	printf 'ccnavi-clean: 名前にパスは書けません（%s）。.claude/worktrees/ の直下の名前だけを渡してください。\n' "$name" >&2
	exit 2
	;;
esac

root=$(ccnavi_workspace) || {
	printf 'ccnavi-clean: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 中身は ccnavi の実行ファイル。--worktree は `worktree drop`（cwd の確かめ・未コミットの確かめ・生成物の
# 掃除・無視ファイルの退避・worktree remove）、ないときは `worktree clean`（未コミットの確かめ・生成物の掃除）。
if [ -n "$whole" ]; then
	if [ -n "$dry" ]; then
		printf 'ccnavi-clean: --worktree と --dry-run は同時に指定できません（削除するものを見るだけなら --dry-run だけで実行します）。\n' >&2
		exit 2
	fi
	sub=drop
else
	target="$root/.claude/worktrees/$name"
	# ワークツリーの置き場そのものがリンクなら、消す先が置き場の外にある。たどらない。
	if [ -L "$target" ]; then
		printf 'ccnavi-clean: %s はリンクです。リンクの先は消しません。\n' "$target" >&2
		exit 2
	fi
	[ -d "$target" ] || {
		printf 'ccnavi-clean: %s がありません。\n' "$target" >&2
		exit 2
	}
	sub=clean
fi
here="$(pwd -W 2>/dev/null || pwd)"
if bin=$(ccnavi_bin "$root"); then
	exec "$bin" --root "$root" --cwd "$here" worktree "$sub" "$name" ${dry:+--preview}
fi
printf 'ccnavi-clean: ccnavi の実行ファイルが見つかりません（CCNAVI_ENTRY_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/・PATH のどれにもありません）。scripts/ccnavi-build.sh で組み立てるか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。\n' >&2
exit 2

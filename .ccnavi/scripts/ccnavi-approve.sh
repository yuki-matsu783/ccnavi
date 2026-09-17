#!/bin/sh
# ccnavi-approve — 提案を承認し、承認済みチケットを親のブランチに乗せて push する。人が端末で打つ。
#
#   sh .ccnavi/scripts/ccnavi-approve.sh [<識別子>...]
#
# 承認そのものは ccnavi の `--approve`。承認された提案は todo/ から親チケットのツリーの
# $CCNAVI_TICKETS_APPROVED（既定 .ccnavi/approved）の doing/ へ動く（ADR-0055）。そこは
# プロジェクトの git が追跡していて、コミットして push するまで他の機械には届かない（設計 §9.2）。
# A が承認して B の機械で作業する流れは、この push で成り立つ。
#
# 識別子を並べると、その分だけを承認の対象にする（`--approve <識別子>...`）。同じ親の
# 承認待ちが 2 本以上あるとき、1 本だけを先に承認するために使う。絞りは対象を狭めるだけで、
# 絞らないときに落ちるものは通さない（判定は実行ファイル）。
#
# 実行ファイルはネットワークに出さない（設計 §3）ので、コミットと push はここでやる。
# 実行ファイルが持つのは、どこに置くかと、何を承認してよいかの判定だけ。
#
# コミットはパスを限る。`-a` も `add -A` も使わない。親のワークツリーには人や他の
# セッションの書きかけがあるので、巻き込むと承認のコミットが他人の変更を運ぶ。
#
# 統合先が保護されている（main / master / develop / release）ときは push しない。
# そこへ直接送る判断は人のものなので、綴りを出して止める。
#
# 終了コード: 0 成功 / 1 承認が通らなかった / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 §11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-approve.sh [<識別子>...]

  承認待ちのチケットをまとめて見せ、承認したら承認済みチケットをコミットして push する。
  識別子を並べると、その分だけを承認の対象にする（例: ccnavi-approve.sh i0002-03）。
  端末から人が打つ。エージェントからは呼べない。
USAGE
}

# 使い方を出すのは、その語が 1 つだけのとき。`help` という識別子もありうるので、
# 識別子と並んだ `help` は識別子として渡す（`-h` と `--help` は下の検査で断る）。
if [ $# -eq 1 ]; then
	case "$1" in
	-h | --help | help)
		usage
		exit 0
		;;
	esac
fi

# 並べた語は識別子として `--approve` の後ろにそのまま渡す。承認待ちに在るかは実行ファイルが見る。
# ここで断るのは空の語と `-` で始まる語。`--yes` や `--root` が混ざると、端末の y/N を経ない
# 経路や別のワークスペースの承認に化けるので、この sh からは選択肢を渡さない。
for id in "$@"; do
	case "$id" in
	"" | -*)
		printf 'ccnavi-approve: 識別子でない引数は取りません (%s)。\n' "$id" >&2
		usage >&2
		exit 2
		;;
	esac
done

# main の根。ワークツリーの中から呼ばれても、ツリーの一覧は main の側から数える。
root=$(ccnavi_workspace) || {
	printf 'ccnavi-approve: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 実行ファイル。設定に書かれた綴りを優先し、無ければ既定の置き場、それも無ければソース。
# `set --` の右の "$@" は置き換える前の引数（並べた識別子）に展開される。
case "${CCNAVI_BIN_PATH:-}" in
/* | [A-Za-z]:*) bin="$CCNAVI_BIN_PATH" ;;
*) bin="$root/${CCNAVI_BIN_PATH:-dist/ccnavi/ccnavi}" ;;
esac
if [ -x "$bin" ]; then
	set -- "$bin" --root "$root" --approve "$@"
elif [ -x "$bin.exe" ]; then
	set -- "$bin.exe" --root "$root" --approve "$@"
elif [ -f "$root/ccnavi/__main__.py" ]; then
	set -- uv run python -m ccnavi --root "$root" --approve "$@"
else
	printf 'ccnavi-approve: ccnavi の実行ファイルが無い (%s)。build.py で組み立ててください。\n' "$bin" >&2
	exit 2
fi

# 承認。落ちたらそこで終わり。承認済みチケットが 1 つも書かれていないので、運ぶものも無い。
"$@" || exit 1

# 運ぶのは ccnavi-push-approved.sh に任せる（設計 §1.4）。落ちても承認は巻き戻さない。
sh "$(dirname "$0")/ccnavi-push-approved.sh" || :
exit 0

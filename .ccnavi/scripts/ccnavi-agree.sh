#!/bin/sh
# ccnavi-agree: 提案を承認し、承認済みチケットを親ブランチにコミットして push する。ユーザが端末で実行する。
#
#   sh .ccnavi/scripts/ccnavi-agree.sh [<識別子>...]
#
# 承認処理自体は ccnavi の `--agree` が行う。承認された提案は todo/ から親チケットのツリー内にある
# .ccnavi/approved（固定）の doing/ へ移動する（コピーは作らない）。承認はチケットの中身を変えず、
# ファイルを移動するだけである（設計 9.2）。この場所はプロジェクトの git が追跡しており、コミットして
# push するまで他のマシンには反映されない。A が承認し、B のマシンで作業を進める流れは、この push によって成立する。
#
# 識別子を並べると、その分だけを承認対象にできる（`--agree <識別子>...`）。同じ親の承認待ちが
# 複数あるとき、特定のものだけを先に承認するために使う。絞り込みは対象を限定するだけで、絞り込まないときに
# 承認されないものは通さない（判定は実行ファイルが行う）。
#
# 実行ファイルはネットワークに出さない（設計 3）ため、コミットと pushはここで行う。実行ファイルが
# 持つのは、どこに置くかと、何を承認してよいかの判定だけである。標準入力が端末かどうかの確認は実行ファイル側で行い、
# この sh 側では行わない。
#
# コミットは対象パスを限定する。`-a` も `add -A` も使わない。親のワークツリーにはユーザや他の
# セッションの未コミット変更があるため、それらを巻き込むと承認コミットに無関係な変更が混入する。
#
# 承認待ちの一覧を表示し、y/N で確認を取ってから承認する。承認が通れば `ccnavi-push-approved.sh` を呼び出し、
# コミットして push する。push が失敗しても承認自体は 0 で終わる（コミットは残り、打ち直せば送れる）。
#
# 統合先が保護ブランチ（main / master / develop / release / release/*）であるか、そのリポジトリの
# 統合先ブランチ（CCNAVI_INTEGRATION_BRANCH → ccnavi-sync.sh の取り込み結果 → origin/HEAD）上にいるときは
# push しない。そこへ直接送るかどうかはユーザが判断するため、ブランチ名を示して停止する。
#
# エージェントからは組み込みの deny（`DENY_TICKET_APPROVAL_CLI`）で止める。
#
# 終了コード: 0 成功（push 失敗含む） / 1 承認が通らなかった / 2 引数または環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-agree.sh [<識別子>...]

  承認待ちのチケットを一覧表示し、y/N で確認を取ってから承認する。
  承認が通れば承認済みチケットをコミットして push する。
  識別子を並べると、その分だけを承認の対象にする（例: ccnavi-agree.sh i0002-01-03）。
  端末からユーザが打つ。エージェントからは呼べない（組み込みの deny で止まる）。
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

# 並べた語は識別子として `--agree` の後ろにそのまま渡す。承認待ちに在るかは実行ファイルが見る。
# ここで断るのは空の語と `-` で始まる語。`--yes` や `--root` が混ざると、端末の y/N を経ない
# 経路や別のワークスペースの承認になってしまうので、この sh からは選択肢を渡さない。
for id in "$@"; do
	case "$id" in
	"" | -*)
		printf 'ccnavi-agree: 識別子でない引数は取りません (%s)。\n' "$id" >&2
		usage >&2
		exit 2
		;;
	esac
done

# ワークスペースルート。ワークツリーの中から呼ばれても、ツリーの一覧はワークスペースルートの側から数える。
root=$(ccnavi_workspace) || {
	printf 'ccnavi-agree: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.shを持つ親を cwdから上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。
# `set --` の右の "$@" は置き換える前の引数（並べた識別子）に展開される。
if bin=$(ccnavi_bin "$root"); then
	skew=$(ccnavi_compat_skew "$root" "$bin") || printf 'ccnavi-agree: %s\n' "$skew" >&2
	set -- "$bin" --root "$root" --agree "$@"
elif [ -f "$root/src/ccnavi/__main__.py" ]; then
	set -- uv run python -m ccnavi --root "$root" --agree "$@"
else
	printf 'ccnavi-agree: ccnavi の実行ファイルが無い（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ のどれにも無い）。build.py で組み立てるか、scripts/ccnavi-setup.sh で配ってください。\n' >&2
	exit 2
fi

# 承認。落ちたらそこで終わり。承認済みチケットが 1 つも書かれていないので、コミットするものも無い。
"$@" || exit 1

# コミットと pushは ccnavi-push-approved.sh に任せる（設計 1.4）。落ちても承認は巻き戻さない。
sh "$(dirname "$0")/ccnavi-push-approved.sh" || :
exit 0

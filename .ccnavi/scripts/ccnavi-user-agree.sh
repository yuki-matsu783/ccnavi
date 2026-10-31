#!/bin/sh
# ccnavi-user-agree: 提案を承認し、承認済みチケットを親ブランチにコミットして push する。ユーザがターミナルで実行する。
#
#   sh .ccnavi/scripts/ccnavi-user-agree.sh [<識別子>...]
#
# ユーザだけが打つ sh は ccnavi-user-*.sh に分けてあり、名前で見分けられる。
#
# 承認処理自体は ccnavi の `user agree`（ユーザ専用の入口。中では `--agree` に読み替わる）が行う。承認された提案は todo/ から親チケットのツリー内にある
# .ccnavi/approved（固定）の doing/ へ移動する（コピーは作らない）。承認はチケットの中身を変えず、
# ファイルを移動するだけである。この場所はプロジェクトの git が追跡しており、コミットして
# push するまで他のマシンには反映されない。A が承認し、B のマシンで作業を進める流れは、この push によって成立する。
#
# 識別子を並べると、その分だけを承認対象にできる（`user agree <識別子>...`）。同じ親の承認待ちが
# 複数あるとき、特定のものだけを先に承認するために使う。絞り込みは対象を限定するだけで、絞り込まないときに
# 承認されないものは通さない（判定は実行ファイルが行う）。
#
# 実行ファイルはネットワークに出さないため、コミットと push はここで行う。実行ファイルが
# 持つのは、どこに置くかと、何を承認してよいかの判定だけである。標準入力がターミナルかどうかの確認は実行ファイル側で行う。
#
# コミットは対象パスを限定する。`-a` も `add -A` も使わない。親のワークツリーにはユーザや他の
# セッションの未コミット変更があるため、それらを巻き込むと承認コミットに無関係な変更が混入する。
#
# 承認先行の一覧を表示し、y/N で確認を取ってから承認する。承認が通れば（一部だけ通って終了コード 4 で終わった
# ときも）`ccnavi-user-push-approved.sh --path <パス>...` を呼び出し、この承認が実際に書いた・消したパス
# （実行ファイルの `--record-writes` の一覧）だけをコミットして push する。同じ置き場にある承認と無関係な変更
# （review/ の変更や、前の失敗で残ったファイル）は巻き込まない。push が失敗しても承認自体は 0 で終わる（コミットは残り、打ち直せば送れる）。
#
# 統合先が保護ブランチ（main / master / develop / release / release/*）であるか、そのリポジトリの
# 統合先ブランチ（そのリポジトリの origin/HEAD）上にいるときは
# push しない。そこへ直接送るかどうかはユーザが判断するため、ブランチ名を示して停止する。
#
# エージェントからは hook が止める（ccnavi-user- で始まる sh の名前と、実行ファイルの `user` の入口）。
#
# 終了コード: 0 成功（push 失敗含む。未送信は文面で言う） / 1 承認が通らなかった（一部だけ通ったときも。置かれたぶんは送る） / 2 引数または環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-agree.sh [<識別子>...]

  承認待ちのチケットを一覧表示し、y/N で確認を取ってから承認する。
  承認が通れば承認済みチケットをコミットして push する。
  識別子を並べると、その分だけを承認の対象にする（例: ccnavi-user-agree.sh i0002-01-03）。
  ターミナルからユーザが実行します。エージェントからは呼べない（hook が止める）。
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

# 並べた語は識別子として `user agree` の後ろにそのまま渡す。承認待ちに在るかは実行ファイルが見る。
# ここで断るのは空の語と `-` で始まる語。`--yes` や `--root` が混ざると、ターミナルの y/N を経ない
# 経路や別のワークスペースの承認になってしまうので、この sh からは選択肢を渡さない。
for id in "$@"; do
	case "$id" in
	"" | -*)
		printf 'ccnavi-user-agree: チケットID以外の引数は指定できません（%s）。\n' "$id" >&2
		usage >&2
		exit 2
		;;
	esac
done

# ワークスペースルート。ワークツリーの中から呼ばれても、ツリーの一覧はワークスペースルートの側から数える。
root=$(ccnavi_workspace) || {
	printf 'ccnavi-user-agree: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 承認が実際に書けたパスの一覧（JSON）の受け皿。実行ファイルは書き込みに入ったときだけ書く。
report=$(mktemp "${TMPDIR:-/tmp}/ccnavi-user-agree.XXXXXX") || {
	printf 'ccnavi-user-agree: 一時ファイルが作れません。\n' >&2
	exit 2
}
# この承認が書いた・消したパスの一覧（1 行 1 つ、ワークスペースルートからの相対）。実行ファイルの
# `--record-writes` は、書き出し先を state の置き場の write-and-push/ の下に限るので、そこに作る。
written_dir="$(ccnavi_state "$root")/write-and-push"
mkdir -p "$written_dir" 2>/dev/null || :
written="$written_dir/agree.$$"
rm -f "$written"
trap 'rm -f "$report" "$written"' EXIT
trap 'rm -f "$report" "$written"; exit 130' INT TERM HUP

# 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。
# `set --` の右の "$@" は置き換える前の引数（並べた識別子）に展開される。
# 起こし方（実行ファイルか、ソースと uv か）は ccnavi-common-write-and-push.sh の ccnavi_wap_use_exe が決める。
if ccnavi_wap_use_exe "$root"; then
	set -- ccnavi_wap_exe --record-writes "$written" --entry sh user agree --placed-out "$report" "$@"
else
	printf 'ccnavi-user-agree: ccnavi の実行ファイルが見つかりません（CCNAVI_ENTRY_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/・PATH のどれにもありません）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。\n' >&2
	exit 2
fi

# 承認。終了コード 0 は全部通った、4 は一部だけ置いて失敗した、それ以外の非 0 は何も置かずに失敗した
# （`n` と答えた、判定で断られた、など）。何も置かれていないときは送らない。コミットされていない
# 承認済みチケットが他にあっても、この承認とは無関係なので巻き込まない。
approve_rc=0
"$@" || approve_rc=$?

# 実行ファイルが書き出した、実際に書けたパスの一覧（JSON）。書けた分、書けなかった分、
# 書かなかった分が分かる形でそのまま見せる。何も置かずに失敗したとき（4 以外の非 0）も、
# 書けなかった理由が入っていれば見せる。
say_report() {
	if [ -s "$report" ]; then
		printf 'ccnavi-user-agree: 承認の結果（placed=書けたパス / written=書いた・消したパス / failed=書けなかった提案 / unwritten=止まったため書かなかった提案 / rejected=承認の対象にしなかった提案 / dirty_parent=承認した親のワークツリーに残る、提案以外の未コミットの変更）: %s\n' \
			"$(cat "$report")" >&2
	fi
}
case "$approve_rc" in
0 | 4) ;;
*)
	say_report
	exit 1
	;;
esac

# コミットと push は ccnavi-user-push-approved.sh に任せる。失敗しても承認は巻き戻さない。
# 渡すのはこの承認が書いた・消したパスだけ。書けなかった分は置かれていないので入らない。
# 一覧が得られなかったときは送らずに止める。置き場の変更を全部送ると、
# 承認と無関係な変更まで承認のコミットに入る。
push_rc=0
if [ -s "$written" ]; then
	set --
	while IFS= read -r path; do
		[ -n "$path" ] || continue
		set -- "$@" --path "$path"
	done <"$written"
	sh "$(dirname "$0")/ccnavi-user-push-approved.sh" "$@" || push_rc=$?
elif [ -f "$written" ]; then
	# 一覧はあるが空。この承認は何も書いていない。
	printf 'コミットして push する承認済みチケットはありません。\n'
else
	printf 'ccnavi-user-agree: この承認で書いたパスの一覧が得られなかったので、コミットも push もしていない（未送信）。承認済みチケットの置き場を確かめてから、sh %s/ccnavi-user-push-approved.sh で送ってください。\n' "$(dirname "$0")" >&2
	push_rc=1
fi

say_report
if [ "$push_rc" -ne 0 ]; then
	# push-approved はツリーごとに結果を言う（送れたツリーは「pushしました」、送れなかったツリーは理由）。
	# 失敗はどれか 1 本でもあれば返るので、全部が未送信とは限らない。
	printf 'ccnavi-user-agree: コミットか push が済んでいないツリーがある（上の push-approved の文面。「pushしました」と出たツリーは送れている）。直してから、上で名指しされたコマンドを再実行してください。\n' >&2
fi

if [ "$approve_rc" -ne 0 ]; then
	printf 'ccnavi-user-agree: 承認の一部だけが通りました。置かれた承認済みチケットは、上の結果のとおりコミット・push を試しています。通らなかった分は原因を直して、もう一度承認してください。\n' >&2
	exit 1
fi
exit 0

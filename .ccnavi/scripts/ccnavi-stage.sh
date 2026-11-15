#!/bin/sh
# ccnavi-stage 書き込みを止められている場所のファイルを、下書きの置き場で直すための支度をする。
#
#   sh .ccnavi/scripts/ccnavi-stage.sh <パス>...
#   sh .ccnavi/scripts/ccnavi-stage.sh --commands <パス>...
#
# 保護対象のファイル（rules.yml・hook・ラッパースクリプトなど）は書けない。直すときは、cwd のツリーの
# wip/handoff/ に元ファイルを cp して、そちらを編集し、できたものを元の場所へ取り込んでもらう。
# cwd が .claude/worktrees/<名前> の中なら、そのワークツリーの wip/handoff/（ブランチに載るので、
# 非対話セッションで push でき、直接作業なら integrate が取り込みのときに外す）。それ以外はワークスペースルートの
# wip/handoff/。
#
# <パス> は元のファイル。cwd からの相対でも絶対でもよく、置き場の根（cwd がワークツリーならそのワークツリー、
# それ以外はワークスペースルート）の下の実在するファイルだけを受け付ける（リンクと、途中のリンクで根の外に出るものは断る）。
# cwd がワークツリーのとき、ワークスペースルートの絶対パスを渡しても、同じ相対パスのワークツリー側のファイルを元にする
# （無ければ断る）。別のワークツリーのファイルと、下書きの置き場の中は断る。
# 下書きは <置き場の根>/wip/handoff/<置き場の根からの相対パス> に置く（元と同じ階層を保つので、違う元のファイルが同じ下書きに落ちない）。
# ただし、相対パスの中の .claude の階層は handoff-.claude に読み替えて置く（深さを問わない。
# .claude/settings.json → wip/handoff/handoff-.claude/settings.json）。Claude Code 本体は .claude という名前の
# 階層を機密パスとして扱い、非対話（claude -p）では編集を許可できないため。取り込み（ccnavi-user-handoff.sh）が
# 読み替えを戻す。読み替える名前の表は ccnavi-handoff-names.txt（実行ファイルに同じ中身を埋め込んである）。
# 表の下書きでの名前（handoff-.claude）は予約した名前で、元のパスにその名前の階層があるファイルは断る。
# 下書きが既にあれば上書きしない（編集中のものを消さないため）。
#
# --commands は、編集が済んだあとに打つ。元と違う下書きに、取り込みの手順を出す（全種類が同じ手順。
# 種類の表は go/internal/tickets/sync/handoff/kinds.go の kinds）。ユーザが ccnavi-user-handoff.sh で下見（検査・差分・ハッシュ）をしてから
# --yes --expect で取り込む。mv で差し替える案内は出さない（検査も差分も --expect も通らず、下書きを作った後に
# 直された元を黙って上書きするため）。
# 元と同じ（まだ編集していない）もの、下書きがまだ無いもの、表の外のもの（取り込めない）は、その旨を標準エラーに出す。
# 複数ファイルを直すときは、すべての下書きを作ってから --commands を打つ。
#
# 打ったあとの進め方（CLAUDE.md）:
#   対話セッション   取り込みの手順をユーザに案内してターンを終える
#   非対話セッション wip/handoff/ の編集済みファイルを push し、ユーザに確認と取り込みを頼んでターンを終える
#
# 本体は実行ファイルの `ccnavi stage`（go/internal/tickets/sync/stage）。パスとリンクの検査・読み替え・コピー・手順の文は
# そこにある。ここは起動役（ワークスペースルートと実行ファイルを探して渡すだけ）。
#
# 終了コード: 0 成功 / 1 前提の未充足（編集済みの下書きが 1 つも無い、など） / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートと実行ファイルの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

root=$(ccnavi_workspace) || {
	printf 'ccnavi-stage: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}
bin=$(ccnavi_bin "$root") || {
	printf 'ccnavi-stage: ccnavi の実行ファイルが見つかりません。実行ファイルを組む（sh scripts/ccnavi-build.sh）か置いてから打ち直してください。\n' >&2
	exit 2
}
exec "$bin" --root "$root" stage -- "$@"

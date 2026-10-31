"""範囲を適用しない置き場。チケットの置き場・下書きの置き場・ELI5 の置き場と、状態の置き場の
出入りの見分け。

パスの文字列だけを見て、ファイルも git も読まない。ticket から分けた。ticket を読まない。
"""

from __future__ import annotations

from . import ticket_model


def is_ticket_place(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """ツリーのルートからの相対パスが、チケットの置き場の下にあるか。

    置き場は提案の置き場（`wip/proposals/`）と承認済みチケットの置き場
    （`.ccnavi/approved/`）。どちらも固定で、env では動かない。
    ここはチケットの範囲の外でも報告しない。報告すると、親が自分のワークツリーに次の子を
    提案する経路と、承認がブランチに乗る経路が使えなくなる。

    外して開くのは提案の `todo/` だけ。`review/` は `ticket_guard.guard_rules`、承認済みチケットは
    自己防衛の組み込みが deny で止め、ルールの deny はチケットより強い。

    前置は `/` の境で切る（`wip/proposalsX/` は置き場ではない）。大文字小文字は範囲の照合と
    同じく、どの機械でも区別しない。

    実行前チェックは `is_unscoped` を通ってここへ来る。実行後チェックとサブエージェント終了時
    チェックは直に呼ぶ（`post_findings._script_writes` / `post._committed_findings` /
    `post_findings.ScopeGuard.finding`、`phase_scope.scope_findings`）。

    実行後チェックから呼ぶときは、後ろに組み込みのルールが無い。 組み込みを足すのは
    `judge` だけで、実行後のルール集合には入らない。だから呼び出しごとの実行後チェックは、置き場を
    そのまま外さずに、内容で外すぶんを決める（`ticket_fields.script_shape`、`post_findings._script_writes`）。
    """
    return any(_under(rel, place) for place in (tickets_rel, approved_rel))


def in_wip(path: str) -> bool:
    """git が出したパスが、途中の作業の置き場（`wip`）の下か。
    大文字小文字と `\\` の区切りを問わない。"""
    folded = path.lower()
    return folded == WIP_ROOT or folded.startswith((WIP_ROOT + "/", WIP_ROOT + "\\"))


def _under(rel: str, place_rel: str) -> bool:
    """ツリーのルートからの相対パスが、その置き場の下にあるか。

    前置は `/` の境で切る（`wip/proposalsX/` は置き場ではない）。大文字小文字は範囲の照合と
    同じく、どの機械でも区別しない。

    `rel` の `\\` は `/` に直さない。呼び手が渡すのは `tree.relative`
    （`os.sep` を `/` に直したパス）か
    git が出したパスで、どちらも区切りは `/`。直すと Linux / macOS で
    `wip\\proposals\\todo\\x.py` という名前のファイル 1 個が置き場の中に見え、範囲の判定から外れる。
    置き場のパス（設定の値）だけは直す。
    """
    base = ticket_model._fold(place_rel.replace("\\", "/").strip("/"))
    return bool(base) and ticket_model._fold(rel).startswith(base + "/")


def leaves_open_state(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """チケットが `finish` / `cancel` / ユーザのレビューで出ていく元（作業中とレビュー待ち）か。

    移動の組を数えるとき、消えた側がここに居たことを求める。求めないと、承認待ちの提案
    （`todo/`）を `review/` に置き直す形が移動として外れる。これは、ユーザの承認を通っていない
    ものをレビュー待ちに見せる形になる。
    """
    return _under(rel, f"{approved_rel}/{ticket_model.DOING}") or _under(
        rel, f"{tickets_rel}/{ticket_model.REVIEW}"
    )


def lands_in_finished_state(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """`finish` と `cancel` がチケットを動かす先（レビュー待ちと閉じた置き場）か。

    実行後チェックが、スクリプトの移動（`doing/` から出ていく）とただの削除を見分けるのに使う。
    行き先をこの 2 つに絞るのは、`doing/` から出したチケットを `todo/` に置き直す形が
    「承認済みチケットを消す」のと同じ結果になるから。承認済みチケットが 1 本も無い
    ワークツリーは範囲を持たず、範囲を持たないツリーはチケットの側から何も言われない。
    """
    return _under(rel, f"{tickets_rel}/{ticket_model.REVIEW}") or _under(
        rel, f"{approved_rel}/{ticket_model.DONE}"
    )


# 下書きの置き場。ツリーのルートからの相対で `wip/handoff/`。名前は固定。設定で動かさない。
# 動かせると、その値を実際のソースの置き場（`src` など）に向けるだけで、承認した範囲を
# 迂回して書ける場所ができる。
# 追跡して push する置き場で、`integrate` が取り込むときに外し（ccnavi-git.sh）、`ready` が
# `wip/` ごと消す。成果物には残らない。
HANDOFF = "wip/handoff"

# 使い捨ての置き場。ワークスペースルート直下の `scratchpad/`。`.gitignore` で追跡から外し、
# 直接作業の途中でユーザに見せる一時的な物を置く。名前は固定。チケットの範囲は適用しない
# （ワークスペースルートはユーザのチェックアウトで、範囲を持たない）。
# コミットされず、片付けでも消えない。
SCRATCH_ROOT = "scratchpad"


def is_handoff_place(rel: str) -> bool:
    """ツリーのルートからの相対パスが、下書きの置き場（`wip/handoff/`）の下にあるか。

    チケットの範囲を適用しない。下書き・再現用のスクリプト・調べた出力を置く場所で
    （.ccnavi/docs/scratchpad.md）、報告すると、範囲を宣言したワークツリーほど手元に何も置けなくなる。
    開けても範囲は広がらない。`wip/` の下なので `ready` の前にまとめて消え、`integrate` も外すので、
    統合先のブランチには乗らない。

    追跡される置き場なので、実行前チェックだけでなく、実行後チェック
    （`post_findings.ScopeGuard.finding`）とサブエージェント終了時チェック
    （`phase_scope.scope_findings`）も、ここを明示的に外す。外さないと、実行前に通った書き込みが
    コミットのあとで範囲の外として報告される。

    名前の大文字小文字は区別しない（`WIP/Handoff/` も同じ置き場。`ready` の片付けも同じ見方）。
    `wip/handoffX/` や、`scratchpad` という名前のファイルは置き場ではない。

    `\\` は `/` に直さない（`is_eli5_place` と同じ理由。`wip\\handoff\\x.py` は置き場ではない）。
    """
    return rel.casefold().startswith(HANDOFF + "/")


# ELI5 の HTML の置き場。依頼につける、変更をやさしく説明した HTML を置く。マージリクエストの
# 差分に載るよう `wip/` の下にコミットする。
# 名前は固定。設定で動かさない（動かせると、その値をソースの置き場に向けるだけで範囲を迂回できる）。
ELI5 = "wip/eli5"

# 途中の作業の置き場。調査や設計の下書きを置く場所で、マージの前に全体を消す。
# 既定のブランチに残す場所はマージリクエストと issue。パスは設定から導かず固定する。
# 読むのは review（片付けの検査）と ops（早めに閉じたあとの案内）。
WIP_ROOT = "wip"


def is_eli5_place(rel: str) -> bool:
    """ツリーのルートからの相対パスが、ELI5 の HTML の置き場（`wip/eli5/`）の下にあるか。

    チケットの範囲を適用しない。ELI5 はレビューの依頼に必ずつける材料で、親の範囲に
    毎回 `wip/eli5/*` を書かせると、書き忘れた親は依頼の手前で止まる。ここは `wip/` の下なので
    `ready` の前にまとめて消え、squash した成果物には残らない。範囲を外すのはこの 1 段だけで、
    `wip/` のほかの場所（`wip/design/` など）と、紛らわしい名前（`wip/eli5x/`）は外さない。

    `scratchpad/` と違って git が追跡する置き場なので、実行後チェック
    （`post_findings.ScopeGuard.finding`）とサブエージェント終了時チェック（`phase_scope.scope_findings`）も、ここを明示的に外す。外さないと、
    実行前に通った書き込みがコミットのあとで範囲の外として報告される。

    名前の大文字小文字は区別しない（`WIP/ELI5/` も同じ置き場。`ready` の片付けも同じ見方）。

    `\\` は `/` に直さない。呼び手のパスは `tree.relative`（`os.sep` を `/` に直したもの）か
    git が出したパスで、どちらも区切りは `/`。直すと Linux / macOS で `wip\\eli5\\evil.py` という
    名前のファイル 1 個が置き場に
    見え、範囲の判定から外れ、しかも `ready` の片付け（`git ls-files -- wip`）の対象にもならない。
    """
    return rel.casefold().startswith(ELI5 + "/")


def is_unscoped(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """チケットの範囲を適用しない場所か。実行前チェック（`judge`）が使う。

    チケットの置き場は、次の提案を書けるようにしておくために外す。外し方は呼び出しごとに違う。
    実行後チェックは内容で決め（`post_findings._script_writes`）、サブエージェント終了時チェックは
    置き場そのものを外す（`phase_scope.scope_findings`）。

    下書きの置き場（`is_handoff_place`）と ELI5 の置き場（`is_eli5_place`）は、
    追跡される置き場なので、実行後チェックとサブエージェント終了時チェックも同じく外す。
    """
    return (
        is_ticket_place(rel, tickets_rel, approved_rel)
        or is_handoff_place(rel)
        or is_eli5_place(rel)
    )

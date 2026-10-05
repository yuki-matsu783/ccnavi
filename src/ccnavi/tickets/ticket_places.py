"""範囲を当てない置き場。チケットの置き場・下書きの置き場・ELI5 の置き場と、状態の置き場の
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

    **実行後チェックから呼ぶときは、後ろに組み込みのルールが無い。** 組み込みを足すのは
    `judge` だけで、実行後のルール集合には入らない。だから呼び出しごとの実行後チェックは、置き場を
    そのまま外さずに、内容で外すぶんを決める（`ticket_fields.script_shape`、`post_findings._script_writes`）。
    """
    return any(_under(rel, place) for place in (tickets_rel, approved_rel))


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


# 下書きと使い捨ての置き場。ツリーのルートの直下 1 段で、名前は固定。設定で動かさない。
# 動かせると、その値を実際のソースの置き場（`src` など）に向けるだけで、承認した範囲を
# 迂回して書ける場所ができる。除外してよい理由が「git が追跡しない」ことにある以上、
# 追跡から外しているワークスペースの `.gitignore` の 1 行と同じ名前に固定するほうが筋が通る。
SCRATCH = "scratchpad"


def is_scratch_place(rel: str) -> bool:
    """ツリーのルートからの相対パスが、下書きの置き場の下にあるか。

    `scratchpad/` は `.gitignore` が追跡から外す置き場で、下書き・再現用のスクリプト・調べた
    出力を置く（docs/claude/scratchpad.md）。チケットの範囲の外でも報告しない。
    報告すると、範囲を宣言したワークツリーほど手元に何も置けなくなり、承認が要る作業だけが
    下書きの場所を失う。開けても範囲は広がらない。ここに書いたものは git が追跡しないので、
    統合先のブランチには 1 バイトも乗らない。

    **名前の大文字小文字は区別する。範囲の照合（`ticket_model._fold`）とは逆にしてある。**
    外してよい理由が「追跡されない」ことにあり、追跡から外しているのは
    `.gitignore` の `/scratchpad/` で、その照合は Linux では区別するため。区別せずに外すと、
    Linux の `SCRATCHPAD/` が「追跡されるのに範囲を当てない場所」になり、承認した範囲の外の
    変更が統合先へ乗る経路ができる。
    区別する側を採れば、どの機械でも除外は追跡から外れる範囲より狭いままで、狭いぶんは
    範囲の判定が止めるだけで済む。

    ルートの直下 1 段だけを見る。`docs/scratchpad/` は普通の作業対象で、`.gitignore` も外さない
    （`/scratchpad/` の先頭の `/` はツリーのルートに掛かる）。`scratchpad` という名前の
    ファイルも置き場ではない（末尾の `/` はディレクトリにしか当たらない）。
    `scratchpadX/` も置き場ではない。

    `\\` は `/` に直さない（`_under` と同じ理由。`scratchpad\\x.py` は置き場ではない）。
    """
    return rel.startswith(SCRATCH + "/")


# ELI5 の HTML の置き場。依頼につける、変更をやさしく説明した HTML を置く。マージリクエストの
# 差分に載るよう `wip/` の下にコミットする。
# 名前は固定。設定で動かさない（動かせると、その値をソースの置き場に向けるだけで範囲を迂回できる）。
ELI5 = "wip/eli5"

# 途中の作業の置き場。調査や設計の下書きを置く場所で、マージの前に丸ごと消す。
# 既定のブランチに残す場所はマージリクエストと issue。パスは設定から導かず固定する。
# 読むのは review（片付けの検査）と ops（早めに閉じたあとの案内）。
WIP_ROOT = "wip"


def is_eli5_place(rel: str) -> bool:
    """ツリーのルートからの相対パスが、ELI5 の HTML の置き場（`wip/eli5/`）の下にあるか。

    チケットの範囲を当てない。ELI5 はレビューの依頼に必ずつける材料で、親の範囲に
    毎回 `wip/eli5/*` を書かせると、書き忘れた親は依頼の手前で止まる。ここは `wip/` の下なので
    `ready` の前に丸ごと消え、squash した成果物には残らない。範囲を外すのはこの 1 段だけで、
    `wip/` のほかの場所（`wip/design/` など）と、紛らわしい名前（`wip/eli5x/`）は外さない。

    `scratchpad/` と違って git が追跡する置き場なので、実行後チェック
    （`post_findings.ScopeGuard.finding`）とサブエージェント終了時チェック（`phase_scope.scope_findings`）も、ここを明示的に外す。外さないと、
    実行前に通った書き込みがコミットのあとで範囲の外として報告される。

    名前の大文字小文字は区別する。依頼の検査（`ccnavi-review.sh`）と `ready` の前提
    （`git ls-files -- wip`）も区別して `wip/` を見るので、区別しない側に広げない。

    `\\` は `/` に直さない。呼び手のパスは `tree.relative`（`os.sep` を `/` に直したもの）か
    git が出したパスで、どちらも区切りは `/`。直すと Linux / macOS で `wip\\eli5\\evil.py` という
    名前のファイル 1 個が置き場に
    見え、範囲の判定から外れ、しかも `ready` の片付け（`git ls-files -- wip`）の対象にもならない。
    """
    return rel.startswith(ELI5 + "/")


def is_unscoped(rel: str, tickets_rel: str, approved_rel: str) -> bool:
    """チケットの範囲を当てない場所か。**実行前チェック（`judge`）だけが使う。**

    実行前は、これから書かれる 1 つのパスを見る。下書きの置き場を外すのはここだけで
    足りる。ここで通せば下書きは書けるので、これが機能の全部になる。

    実行後チェック（`post`）とサブエージェント終了時チェック（`phase_scope.scope_findings`）は
    下書きの置き場を外さない。チケットの置き場の外し方も同じではなく、呼び出しごとの実行後チェックは
    内容で決める（`post_findings._script_writes`）。外し方を揃えないのは、
    **揃える意味がその 2 か所には無い**から。どちらも入力は `git status`
    （`--ignored` を付けない）と `base_sha..HEAD` の差分（追跡ファイルだけ）で、
    追跡から外れている `scratchpad/` はそこに 1 本も現れない。つまり正しく設定された
    リポジトリでは、外しても外さなくても同じ答えになる。

    答えが変わるのは `scratchpad/` が追跡されているとき、すなわち外してよい根拠
    （追跡されないので統合先のブランチへ乗らない）が既に崩れているときだけ。
    そこで外すと、根拠が崩れたことを知らせる唯一の経路を自分でなくすことになる。
    だから外さない。実行前に通ったものが実行後に報告される形は残るが、報告される
    のは「そのリポジトリで `scratchpad/` が追跡されている」ときだけで、それは本当に
    知らせるべきことになる。

    ELI5 の置き場（`is_eli5_place`）も外す。こちらは追跡される置き場なので、実行後チェックと
    サブエージェント終了時チェックも同じく外す。
    """
    return (
        is_ticket_place(rel, tickets_rel, approved_rel)
        or is_scratch_place(rel)
        or is_eli5_place(rel)
    )

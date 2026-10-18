"""ユーザだけが打つ操作の表。何をユーザ専用にしているかを、ここ 1 か所に宣言する。

hook の止め（`phase_forms`）と、実行ファイルの端末要求（`entry/cli*.py`）は、操作を足すたびに
別々の正規表現やオプションの一覧を直していた。この表が正本になり、`phase_forms` が正規表現と
オプションの一覧をここから組み立て、実行ファイルは `terminal()` で、端末を求めるときに表示する
オプション名を引く。操作を足す・外すときは、この表の行を直す。

表は 4 つ。

- `OPS`: 操作。何をする操作か（`what`）と、実行ファイルが端末の前のユーザを求めるときの
  オプションの表示名（`terminal`）を持つ
- `FLAGS`: オプション。実行ファイル（`where` が空）か sh スクリプト（`where` がスクリプトの名前）の
  オプションで、どの止めに入るか（`cli`・`script`）と、所属するグループ（`groups`）を持つ
- `SCRIPTS`・`SUBS`: コマンドの位置で止める sh スクリプトと、実行ファイルのサブコマンド
- `BOARD_COMBOS`: ボードで押した操作用のオプションの組み合わせ。コマンド行に全部並んだら止める

グループ（`groups`）は、オプションを束ねて組み合わせを作るための名前。ボードの組み合わせと、
引数を読み切れない形の判定が使う。

このモジュールは ccnavi のほかのモジュールを読まない。正規表現の文字列を作るだけで、
コマンドもチケットも読まない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Op:
    """ユーザ専用の操作 1 つ。"""

    id: str
    # 文面と、表の読み手のための短い名前。
    what: str
    # 実行ファイルが標準入力に端末を求める操作の、標準エラーに出すオプションの表示名。
    # 空なら求めない。
    terminal: str = ""


@dataclass(frozen=True)
class Flag:
    """オプション 1 つ。"""

    name: str
    op: str
    # 空なら実行ファイルのオプション。sh スクリプトのオプションならスクリプトの名前（拡張子なし）。
    where: str = ""
    # 実行ファイルを直に呼ぶ形（`launcher … <オプション>`）として止める。
    cli: bool = True
    # sh スクリプトのオプションとして、そのスクリプトの呼び出しを止める。
    script: bool = False
    # `--preview` を単独の語として隣に置いた形は、見るだけなので通す。
    preview_exempt: bool = False
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class Script:
    """コマンドの位置で止める sh スクリプト。"""

    # 拡張子なしの名前。前の名前が残っていれば並べる。
    names: tuple[str, ...]
    op: str
    # 空でなければ、1 つ目の引数がこの語のときだけ止める。
    subcommand: str = ""


@dataclass(frozen=True)
class Sub:
    """実行ファイルを直に呼ぶ形のサブコマンド。`<group> [--] <name>`。"""

    groups: tuple[str, ...]
    names: tuple[str, ...]
    op: str


OPS: tuple[Op, ...] = (
    Op("approve", "承認", terminal="--agree"),
    Op("push-approved", "承認済みチケットのコミットと push"),
    Op("reviewed", "レビュー済みの記録", terminal="--reviewed"),
    Op("close-early", "早めに閉じる", terminal="--close-early"),
    Op("decide", "残った指摘の対応方針（ボードの経路）"),
    Op("confirm-yes", "ボードで押した操作の確定（--yes）"),
    Op("forget", "親子チケットの取り込み状態の削除"),
    Op("tidy", "親を閉じたときの片付け"),
    Op("clean-branch", "残った子のブランチの削除"),
    Op(
        "template",
        "テンプレートの取り込み・改名・移動・削除・下書きの保存",
        terminal="--templates --yes",
    ),
    Op("prune", "記録と state の削除", terminal="--prune"),
    Op("state-direct", "チケットの状態とレビューの操作を実行ファイルで直に呼ぶ形"),
    Op("guard-off", "端末要求を切る設定"),
)

_REVIEW = "ccnavi-review"
_SYNC = "ccnavi-sync"
_CLEAN = "ccnavi-clean"

FLAGS: tuple[Flag, ...] = (
    Flag("--yes", "confirm-yes", groups=("yes",)),
    Flag("--agree", "approve", preview_exempt=True, groups=("approval",)),
    # 前の名前。実行ファイルはもう受け付けないが、古い実行ファイルが手元に残っていれば通ってしまう。
    # 止める側にだけ広がる。
    Flag("--approve", "approve", preview_exempt=True, groups=("approval",)),
    Flag("--reviewed", "reviewed", groups=("approval",)),
    Flag("--close-early", "close-early"),
    # 記録を消す形は `phase_forms.prune_form` が別の deny で止める（_CLI_FORMS には入れない）。
    Flag("--prune", "prune", cli=False, preview_exempt=True, groups=("prune",)),
    # `--import` はテンプレートの下書きを取り込む形。取り込みはユーザが差分を読んで決めるので、
    # 下見だけの形（`--yes` なし）も含めてエージェントには打たせない。下書きを作る `--draft` は
    # 効力が無いので止めない。改名・移動・削除も同じ。`--save-draft` も止める（`--body-file` に
    # 任意のパスを渡して、読んだ中身を下見の出力に出せるため。下書きの編集は Write でできる）。
    Flag("--import", "template", groups=("import", "template-write")),
    Flag("--rename-template", "template", groups=("edit", "template-write")),
    Flag("--move-template", "template", groups=("edit", "template-write")),
    Flag("--delete-template", "template", groups=("edit", "template-write")),
    Flag("--save-draft", "template", groups=("edit", "save-draft", "template-write")),
    # 下書きの置き場の中だけを消すので、`--yes` の付かない下見は止めない。
    Flag("--delete-draft", "template", cli=False, groups=("edit", "template-write")),
    # 単独では止めない。ボードの組み合わせの相手になるだけ。
    Flag("--templates", "template", cli=False, groups=("templates",)),
    Flag("--guard-ticket-approval", "guard-off", cli=False, groups=("guard",)),
    Flag("--choices", "decide", where=_REVIEW, cli=False, groups=("choices",)),
    Flag("--digest", "decide", where=_REVIEW, cli=False, groups=("digest",)),
    Flag("--forget", "forget", where=_SYNC, cli=False, script=True, groups=("forget",)),
    Flag("--branch", "clean-branch", where=_CLEAN, cli=False, script=True),
    Flag("--confirmed", "clean-branch", where=_CLEAN, cli=False, script=True),
)

SCRIPTS: tuple[Script, ...] = (
    # `ccnavi-approve.sh` は `ccnavi-agree.sh` の前の名前。古いコピーが残っていても止める。
    Script(("ccnavi-agree", "ccnavi-approve"), "approve"),
    Script(("ccnavi-push-approved",), "push-approved"),
    # 親子のチケットの削除せずに残した取り込み状態を消す、ユーザが打つスクリプト。消すと、
    # 止めていた親子のチケットが取り込み状態の無いものとして今の手元の動きに戻るので、
    # 打つのはユーザ。
    Script((_SYNC,), "forget"),
    # ユーザの判断に使うスクリプト。中で `--close-early` を起こし、最後に承認の push を呼ぶ。
    Script((_REVIEW,), "close-early", subcommand="close-early"),
    # 親を閉じたときに残した子のブランチを消す入口（中身は実行ファイルの `worktree branch`）。
    # ユーザが `!` を付けて打つ。`--confirmed` を単なる語としてエージェントが添えて、
    # 取り消した子のブランチを確認なしで消せないようにする。
    Script((_CLEAN,), "clean-branch"),
)

SUBS: tuple[Sub, ...] = (
    Sub(
        ("ticket", "review"),
        ("start", "finish", "cancel", "record-risk", "prepare", "requested", "confirm", "ready"),
        "state-direct",
    ),
    # 親を閉じたときの片付け。ブランチを `branch -D` で消すので、エージェント（メインも
    # サブエージェントも）には直に打たせない。呼ぶのは sh（`ccnavi-review.sh ready` の
    # `worktree retire`、`ccnavi-sync.sh` の `sync tidy`）の中か、ユーザが `!` を付けて
    # 打つときだけ。sh の中の呼び出しは hook を通らない。取り消した子のブランチを消す
    # `worktree branch … confirmed` は、ユーザが確認したことをユーザ自身が打つことで担保する。
    Sub(("sync",), ("tidy",), "tidy"),
    Sub(("worktree",), ("retire", "branch"), "tidy"),
)

# ボードで押した操作用のオプションの組み合わせ。グループの名前を並べる。全部のグループから
# 1 つずつがコマンド行に並んだら止める。並びの順に見て、最初に揃った組み合わせで文面を決める。
BOARD_COMBOS: tuple[tuple[str, ...], ...] = (
    ("approval", "yes"),
    ("templates", "import", "yes"),
    ("templates", "edit", "yes"),
    ("templates", "save-draft"),
    ("choices", "digest"),
)

# エージェントが sh で打てるスクリプト。止めている間でも通す（phase_forms.exempt）。
AGENT_SCRIPTS: tuple[str, ...] = ("ticket", "review", "git")
# サブエージェントに許さないサブコマンド。AGENT_SCRIPTS の sh に付けた形。メインは打てる。
SUBAGENT_FORBIDDEN: tuple[str, ...] = (
    "start",
    "finish",
    "cancel",
    "record-risk",
    "request",
    "confirm",
    "comment",
    "decide",
    "ready",
    "close-early",
    "push",
)


def op(op_id: str) -> Op:
    """操作を id で引く。無ければ KeyError。"""
    for row in OPS:
        if row.id == op_id:
            return row
    raise KeyError(op_id)


def terminal(op_id: str) -> str:
    """実行ファイルが端末を求める操作の、標準エラーに出すオプションの表示名。"""
    shown = op(op_id).terminal
    if not shown:
        raise KeyError(f"{op_id} は端末を求める操作ではない")
    return shown


def names(*groups: str, where: str | None = "") -> tuple[str, ...]:
    """グループのどれかに入るオプションの名前を、表の順に。

    `where` はオプションの置き場。空は実行ファイル、スクリプトの名前はそのスクリプト、
    None はどこでも。グループにオプションが 1 つも無いのは表の書き間違いなので、空では返さず
    KeyError にする（空のオプションで組み合わせを作ると、何にでも当たる正規表現になる）。
    """
    found: list[str] = []
    for row in FLAGS:
        if where in (None, row.where) and set(groups) & set(row.groups) and row.name not in found:
            found.append(row.name)
    if not found:
        raise KeyError(f"オプションが無いグループ: {groups} (where={where!r})")
    return tuple(found)


def _alt(words: tuple[str, ...]) -> str:
    return "|".join(re.escape(w) for w in words)


def cli_forms(not_preview: str) -> str:
    """実行ファイルを直に呼ぶ形のうち、オプションとサブコマンドの部分。

    `launcher` の後ろに続ける正規表現。

    `not_preview` は、`--preview` が単独の語として隣に無いことを言う後読みの断片。
    `preview_exempt` のオプションにだけ付ける。承認を行う `--yes` は独立したオプションで、
    必ず当てる。
    """
    parts: list[str] = []
    exempt = [r.name for r in FLAGS if r.cli and r.preview_exempt and not r.where]
    for row in FLAGS:
        if not row.cli or row.where:
            continue
        if row.preview_exempt:
            if row.name != exempt[0]:
                continue
            parts.append(rf"(?:{_alt(tuple(exempt))})\b{not_preview}")
        else:
            parts.append(rf"{re.escape(row.name)}\b")
    for sub in SUBS:
        # サブコマンドの前の `--`（`ticket -- finish`）は、実行ファイルがオプションの終わりとして
        # 読み飛ばす。
        parts.append(rf"\b(?:{'|'.join(sub.groups)})\s+(?:--\s+)?(?:{'|'.join(sub.names)})\b")
    return "(?:" + "|".join(parts) + ")"


def script_forms() -> str:
    """コマンドの位置で sh スクリプトを止める正規表現（オプションは大小を区別しない前提）。"""
    head = r"(^|\x00|[;&|]\s*)((sh|bash)(\s+-\S+)*\s+)?\S*"
    parts: list[str] = []
    for script in SCRIPTS:
        stem = "(?:" + "|".join(re.escape(n) for n in script.names) + r")\.sh"
        flags = tuple(
            row.name
            for row in FLAGS
            if row.script and row.where in script.names and row.op == script.op
        )
        if script.subcommand:
            tail = rf"\s+{re.escape(script.subcommand)}\b"
        elif flags:
            tail = rf"\s[^\x00]*(?:{_alt(flags)})\b"
        else:
            tail = r"\b"
        parts.append(head + stem + tail)
    return "|".join(parts)


def _word(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])(?:{_alt(words)})(?![\w-])", re.IGNORECASE)


def board_combos() -> tuple[tuple[re.Pattern[str], ...], ...]:
    """ボードの組み合わせを、グループごとの正規表現にしたもの。"""
    return tuple(
        tuple(_word(names(group, where=None)) for group in combo) for combo in BOARD_COMBOS
    )


def agent_script_forms() -> str:
    """`sh <…>/ccnavi-(ticket|review|git).sh` の `ccnavi-…sh` の部分。"""
    return r"ccnavi-(?:" + "|".join(AGENT_SCRIPTS) + r")\.sh"


def subagent_forbidden_forms() -> str:
    """サブエージェントに許さないサブコマンドの正規表現（コマンドの先頭の `sh` に固定）。"""
    return (
        r"(^|[;&|]\s*)(sh|bash)(\s+-\S+)*\s+\S*"
        + agent_script_forms()
        + r"\s+(?:"
        + "|".join(re.escape(w) for w in SUBAGENT_FORBIDDEN)
        + r")\b"
    )

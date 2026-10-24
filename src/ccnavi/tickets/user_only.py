"""ユーザだけが打つ操作の表。どの操作をユーザ専用にするかを、ここ 1 か所に宣言する。

この表が、hook の止め（`phase_forms`）と実行ファイルの端末要求（`entry/cli*.py`）の正本。
`phase_forms` が正規表現とオプションの一覧をここから組み立て、実行ファイルは `terminal()` で、
端末を求めるときに表示する操作の名前（`user agree` の形）を引く。操作を足す・外すときは、この表の行を直す。

表は 4 つ。

- `OPS`: 操作。何をする操作か（`what`）と、実行ファイルが端末の前のユーザを求めるときに
  表示するコマンド名（`terminal`）を持つ
- `FLAGS`: オプション。実行ファイル（`where` が空）か sh スクリプト（`where` がスクリプトの名前）の
  オプションで、どの止めに入るか（`cli`・`script`）と、所属するグループ（`groups`）を持つ。
- `SCRIPTS`・`SUBS`: コマンドの位置で止める sh スクリプトと、実行ファイルのサブコマンド
- `BOARD_COMBOS`: ボードで押した操作用のオプションの組み合わせ。コマンド行に全部並んだら止める

グループ（`groups`）は、オプションをまとめて組み合わせを作るための名前。ボードの組み合わせと、
引数を読み切れない形の判定が使う。

このモジュールは ccnavi のほかのモジュールを読まない。正規表現の文字列を作るだけで、
コマンドもチケットも読まない。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Op:
    """ユーザ専用の操作 1 つ。"""

    id: str
    # 文面と、表の読み手のための短い名前。
    what: str
    # 実行ファイルが標準入力に端末を求める操作の、標準エラーに出す表示名（`user <操作>` の形）。
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
    # 実行ファイルが `user` の入口の外で受けたら断るオプション（user_only.refused）。
    gated: bool = False
    # 断らない場合。"preview" は `--preview` と一緒のとき、"no-yes" は `--yes` が無いとき。
    # 空なら、`user` の外では必ず断る。
    unless: str = ""


@dataclass(frozen=True)
class Script:
    """コマンドの位置で止める sh スクリプト。"""

    # 拡張子なしの名前。
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
    Op("approve", "承認", terminal="user agree"),
    Op("reviewed", "レビュー済みの記録", terminal="user reviewed"),
    Op("close-early", "早めに閉じる", terminal="user close-early"),
    Op("decide", "残った指摘の対応方針（ボードの経路）"),
    Op("confirm-yes", "ボードで押した操作の確定（--yes）"),
    Op("tidy", "親を閉じたときの片付け"),
    Op("handoff", "保護対象の下書きの取り込み", terminal="user handoff --yes"),
    Op("prune", "記録と state の削除", terminal="user prune"),
    Op("state-direct", "チケットの状態とレビューの操作を実行ファイルで直に呼ぶ形"),
    Op("guard-off", "端末要求を切る設定"),
)

_REVIEW = "ccnavi-review"

FLAGS: tuple[Flag, ...] = (
    Flag("--yes", "confirm-yes", gated=True, groups=("yes",)),
    Flag(
        "--agree",
        "approve",
        preview_exempt=True,
        gated=True,
        unless="preview",
        groups=("approval",),
    ),
    # 見るだけの形（`--reviewed … --accept-unresolved --preview`）は、
    # `ccnavi-review.sh decide <N> --preview` が内部で呼ぶので、`user` の外でも断らない。
    Flag("--reviewed", "reviewed", gated=True, unless="preview", groups=("approval",)),
    Flag("--close-early", "close-early", gated=True),
    # 記録を消す形は `phase_forms.prune_form` が別の deny で止める（_CLI_FORMS には入れない）。
    Flag(
        "--prune",
        "prune",
        cli=False,
        preview_exempt=True,
        gated=True,
        unless="preview",
        groups=("prune",),
    ),
    # 保護対象の下書きの取り込み（`user handoff`）。見るだけの形（`--yes` なし）も含めて
    # ユーザ専用で、エージェントには打たせない（エージェントは `ccnavi-stage.sh --commands` で
    # 下書きの状態を見られる）。
    Flag("--handoff", "handoff", gated=True, groups=("handoff",)),
    Flag("--guard-ticket-approval", "guard-off", cli=False, groups=("guard",)),
    # ボードの経路の印（`--via board`・`--via=board`）。ボードの子プロセスだけが付け、端末を
    # 求めない経路を選ぶ。単独では止めないが、`board` の語と並べば、操作の語や `--yes` が
    # 読めない形（展開で組み立てた `--yes` など）でも止める（BOARD_COMBOS）。
    Flag("--via", "confirm-yes", cli=False, groups=("via",)),
    # 放棄した親の片付けの記録を消す `worktree retire --abandon`。文字どおりの `worktree retire` は
    # SUBS が止める。ここは、引数を読み切れない形の判定（phase_forms._abandon_word）が使う。
    Flag("--abandon", "tidy", cli=False, groups=("abandon",)),
    Flag("--choices", "decide", where=_REVIEW, cli=False, groups=("choices",)),
    Flag("--digest", "decide", where=_REVIEW, cli=False, groups=("digest",)),
)

# 今は行が無い（ユーザ専用の sh は `ccnavi-user-*.sh` の名前で一括して止める。`script_forms`）。
SCRIPTS: tuple[Script, ...] = ()

SUBS: tuple[Sub, ...] = (
    Sub(
        ("ticket", "review"),
        (
            "start",
            "finish",
            "cancel",
            "propose",
            "consolidate",
            "record-risk",
            "prepare",
            "requested",
            "confirm",
            "ready",
        ),
        "state-direct",
    ),
    # 親を閉じたときの片付け。ブランチを `branch -D` で消すので、エージェント（メインも
    # サブエージェントも）には直に打たせない。呼ぶのは sh（`ccnavi-review.sh ready` の
    # `worktree retire`、`ccnavi-sync.sh` の `sync tidy`）の中か、ユーザが `!` を付けて
    # 打つときだけ。sh の中の呼び出しは hook を通らない。取り消した子のブランチを消す
    # `worktree branch … confirmed` は、ユーザが確かめたことをユーザ自身が打つことで守る。
    Sub(("sync",), ("tidy",), "tidy"),
    # 操作記録の書き込み。偽の送信済みを書かせないため、エージェントには直に打たせない。
    # 呼ぶのは sh の中だけ。読む副命令（pending・show）は止めない。
    Sub(("ops",), ("begin", "stage-set", "applied", "done", "retire", "mark-sent"), "state-direct"),
    Sub(("worktree",), ("retire", "branch"), "tidy"),
)

# ボードで押した操作の、オプションの組み合わせ。グループの名前を並べ、どのグループからも
# 1 つずつがコマンド行に並んだら止める。並びの順に見て、最初に揃った組み合わせで文面を決める。
# 名前に依らず止めるので、名前を変えたコピーも止まる。端末を求めない経路は、これが唯一の防御になる。
BOARD_COMBOS: tuple[tuple[str, ...], ...] = (
    ("approval", "yes"),
    ("user", "user-board-verb", "yes"),
    ("choices", "digest"),
    # ボードの経路の印だけで止める。`user` も操作の語も伴わない形（名前を変えたコピーに
    # `--via board` だけを渡す、`--yes` を展開で隠す）も、ボードの経路として止める。
    ("via", "board"),
)


@dataclass(frozen=True)
class Verb:
    """`ccnavi user <操作>` の 1 つ。"""

    # `user` の後ろに続く語。
    words: tuple[str, ...]
    # 読み替えた先の、内部のオプション。最後の 1 つが値を取るなら `value` を立てる。
    internal: tuple[str, ...]
    op: str
    # 内部の最後のオプションが、操作の次の語を値に取る（`user reviewed 3` → `--reviewed 3`）。
    value: bool = False


# 実行ファイルのユーザ専用の入口。読み替えた先は今の内部の形で、argparse の文法は変えない。
VERBS: tuple[Verb, ...] = (
    Verb(("agree",), ("--agree",), "approve"),
    Verb(("reviewed",), ("--reviewed",), "reviewed", value=True),
    Verb(("close-early",), ("--close-early",), "close-early"),
    Verb(("prune",), ("--prune",), "prune"),
    # 続く語（ツリーからの相対パス）は、位置引数としてそのまま渡る。
    Verb(("handoff",), ("--handoff",), "handoff"),
)

# `user` の入口の名前。
USER_WORD = "user"
# sh と拡張が実行ファイルを呼ぶときに、先に付ける値つきのオプション。`user` はこれらを読み飛ばした
# 後の最初の語。実行ファイルの読み飛ばしと hook の正規表現の両方がこの一覧から作る。
WRAPPER_OPTIONS: tuple[str, ...] = ("--root", "--cwd", "--record-writes", "--record-tree")

# 語のグループ。オプションではない語（`user agree --yes` の `user` と `agree`）を、
# ボードの組み合わせに使うためのグループ。
WORD_GROUPS: dict[str, tuple[str, ...]] = {
    "user": (USER_WORD,),
    # 端末を求めない経路（`--yes`）を持つ操作。
    "user-board-verb": ("agree", "reviewed", "handoff"),
    # `--via` の値のうち、端末を求めない経路を選ぶもの。
    "board": ("board",),
}

# ユーザ専用の sh の名前。位置に依らず、コマンド行に名前が出たら止める
# （phase_forms.human_path_form）。
USER_SCRIPT_PREFIX = "ccnavi-user-"

# エージェントが sh で打てるスクリプト。止めている間も通す（phase_forms.exempt）。
AGENT_SCRIPTS: tuple[str, ...] = ("ticket", "review", "git")


def op(op_id: str) -> Op:
    """操作を id で引く。無ければ KeyError。"""
    for row in OPS:
        if row.id == op_id:
            return row
    raise KeyError(op_id)


def terminal(op_id: str) -> str:
    """実行ファイルが端末を求める操作の、標準エラーに出す表示名（`user agree` の形）。"""
    shown = op(op_id).terminal
    if not shown:
        raise KeyError(f"{op_id} は端末を求める操作ではない")
    return shown


def names(*groups: str, where: str | None = "") -> tuple[str, ...]:
    """いずれかのグループに入るオプションの名前を、表の順に返す。

    `where` はオプションの置き場。空は実行ファイル、スクリプトの名前はそのスクリプト、
    None はどこでも。グループにオプションが 1 つも無いのは表の書き間違いなので、空では返さず
    KeyError にする。空のまま組み合わせを作ると、何にでも当たる正規表現になるため。
    """
    found: list[str] = []
    for group in groups:
        found.extend(w for w in WORD_GROUPS.get(group, ()) if w not in found)
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
    # ユーザ専用の sh（`ccnavi-user-*.sh`）は、引数に依らずコマンドの位置で止める。
    # 名前がコマンド行のどこにあっても止める検査は phase_forms.human_path_form が持つ。
    parts.append(head + re.escape(USER_SCRIPT_PREFIX) + r"[\w-]+\.sh\b")
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


# サブエージェントの役割の表（スクリプト × 副命令）。止めるのは状態を動かす・公開する操作で、
# 読み取りは許す。表に無い副命令は、状態を持つスクリプト（ticket・review）では禁止にする。
# git のラッパーは、禁止する副命令だけを並べる（それ以外は wrapguard の検査に任せる）。
# 各行は（スクリプト名の語幹, 方式, 副命令の一覧）。
#   allow: 一覧の副命令だけ許し、それ以外（副命令が無い呼び出しを含む）は禁止
#   deny: 一覧の副命令だけ禁止し、それ以外は許す
#   all: 副命令に関わらず禁止
# `decide <N> --preview` は review の下見で、許す（`allow` の一覧の `decide\s+\S+\s+--preview`）。
SUBAGENT_ROLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ccnavi-ticket", "allow", ("status", "next-id", "--help", "learn")),
    (
        "ccnavi-review",
        "allow",
        ("fetch", "origin", "merged", "--help", r"decide\s+\S+\s+--preview"),
    ),
    ("ccnavi-git", "deny", ("push", "worktree", "integrate", "integrate-done")),
    # 起動の sh は、資料・検査・試験・説明・ヘルプだけ許す。中身は実行ファイルの全副命令に届くため。
    ("ccnavi-launcher", "allow", ("--docs", "--lint", "--test", "--explain", "--help")),
    ("ccnavi-start", "all", ()),
    ("ccnavi-sync", "all", ()),
    ("ccnavi-worktree", "all", ()),
    ("ccnavi-clean", "all", ()),
)

# 呼び出しの前置き。`sh`・`bash`（選択肢付き）、直接実行（`./x.sh`・パス・引用したパス）、
# `exec`・`command`・`source`・`.` を含める。
_CALL_HEAD = (
    r"(^|[;&|]\s*)(?:(?:exec|command|source|\.)\s+)?(?:(?:sh|bash)(?:\s+-\S+)*\s+)?[\"']?[^\s;&|]*?"
)


def subagent_forbidden_forms() -> str:
    """サブエージェントに許さない形の正規表現（`SUBAGENT_ROLES` から組む）。"""
    parts: list[str] = []
    for stem, how, subs in SUBAGENT_ROLES:
        name = re.escape(stem) + r"\.sh[\"']?(?=\s|$|[;&|])"
        listed = "|".join(subs)
        if how == "all":
            parts.append(_CALL_HEAD + name)
        elif how == "allow":
            parts.append(_CALL_HEAD + name + rf"(?!\s+(?:{listed})(?![\w-]))")
        else:
            parts.append(_CALL_HEAD + name + rf"\s+(?:{listed})(?![\w-])")
    return "|".join(parts)


class UserEntryError(Exception):
    """`user` の入口の使い方の誤り（操作が無い・知らない操作）。"""


def _is_wrapper_option(word: str) -> bool:
    return word in WRAPPER_OPTIONS or word.partition("=")[0] in WRAPPER_OPTIONS and "=" in word


def _ascii_lower(word: str) -> str:
    """コマンドの語を比べる形。ASCII の大文字だけを小文字にする。

    `casefold` は使わない。`casefold` は `ſ`（U+017F）を `s` に、`ﬀ`（U+FB00）を `ff` にするので、
    `uſer`・`handoﬀ` が `user`・`handoff` と同じ語になる。hook の正規表現と実行ファイルの読みが
    どこかで食い違うと、片方だけが入口と読む綴りができる。ここで比べるのはパスや名前ではなく
    コマンドの語（argparse と同じく ASCII の綴り）なので、`pathcase.fold` の決まりの外にある。
    """
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in word)


def _loose(word: str) -> str:
    """互換の読み（NFKC と casefold）。ASCII でない綴りが入口の語に化けるかを見るためだけに使う。"""
    return unicodedata.normalize("NFKC", word).casefold()


def rewrite(argv: list[str]) -> tuple[list[str], Verb | None]:
    """`user <操作> …` を内部の形に読み替える。`user` で始まらなければそのまま返す。

    `user` は、ラッパーが先に付ける値つきのオプション（`WRAPPER_OPTIONS`）を読み飛ばした後の
    最初の語。ASCII の大文字小文字は区別しない（`_ascii_lower`）。先頭の `--` は読み飛ばさない
    （名前空間として扱わない）。`user` の位置か操作の位置に ASCII でない語があり、Unicode の
    互換の読み（NFKC と casefold）で `user` や操作の語になるなら、`UserEntryError` で断る
    （`uſer`・`handoﬀ`）。返す 2 つめは、読み替えた操作。argparse の結果には入れず、
    呼び出し側が別の変数で持つ。
    """
    at = 0
    while at < len(argv) and _is_wrapper_option(argv[at]):
        at += 1 if "=" in argv[at] else 2
    if at >= len(argv):
        return argv, None
    if _ascii_lower(argv[at]) != USER_WORD:
        if not argv[at].isascii() and _loose(argv[at]) == USER_WORD:
            raise UserEntryError(f"user の入口は ASCII の綴りで打つ（{argv[at]!r} は受け付けない）")
        return argv, None
    words = [_ascii_lower(w) for w in argv[at + 1 : at + 3]]
    if words and not words[0].isascii():
        raise UserEntryError(f"user の操作は ASCII の綴りで打つ（{words[0]!r} は受け付けない）")
    for verb in VERBS:
        if tuple(words[: len(verb.words)]) != verb.words:
            continue
        rest = argv[at + 1 + len(verb.words) :]
        internal = list(verb.internal)
        if verb.value:
            if not rest or rest[0].startswith("-"):
                raise UserEntryError(f"user {' '.join(verb.words)} には値が要る")
            internal.append(rest[0])
            rest = rest[1:]
        return [*argv[:at], *internal, *rest], verb
    shown = " / ".join(" ".join(v.words) for v in VERBS)
    raise UserEntryError(f"user の操作は {shown} のどれか")


def _unset(value: object) -> bool:
    return value is None or value is False or (isinstance(value, str) and value == "")


def _dest(flag: str) -> str:
    return flag[2:].replace("-", "_")


def refused(args: object) -> str:
    """`user` の外でユーザ専用のオプションが読まれたときの断り文。問題が無ければ空。

    argparse が読んだ結果（`args`）で見る。argv の文字列を探すと、`--keyword=--agree` のような
    他のオプションの値まで断る。
    """
    # 操作に対応するオプションを先に見る。`--yes` は、どの操作にも付くので最後に回す。
    flags = {row.name: row for row in FLAGS if row.gated}
    for name, row in sorted(flags.items(), key=lambda item: item[0] == "--yes"):
        if _unset(getattr(args, _dest(name), None)):
            continue
        if row.unless == "preview" and getattr(args, "preview", False):
            continue
        if row.unless == "no-yes" and _unset(getattr(args, "yes", "")):
            continue
        forms = [" ".join(v.words) for v in VERBS if v.internal[-1] == name]
        if forms:
            text = f"{name} はユーザ専用です。ユーザが `ccnavi user {forms[0]}` の形で打ちます。"
            if row.unless == "preview":
                text += f"見るだけなら {name} --preview を使います。"
            return text
        return f"{name} はユーザ専用です（`ccnavi user` の操作と一緒に、ユーザが打ちます）。"
    return ""


def _quoted_or_word() -> str:
    return r"""(?:"[^"]*"|'[^']*'|\S+)"""


def user_entry_forms(excluding: str = "") -> str:
    """実行ファイルの呼び出しの `user` の入口の部分（`launcher` の後ろに続ける正規表現）。

    ラッパーのオプションを読み飛ばした後の最初の語が `user`。`--` を挟んだ形も止める
    （実行ファイルは断るが、止める側に倒す）。`excluding` は、別の deny が止める操作の名前
    （`prune` は、チケット制御ではなくコアのファイルの保護の設定に従う）。
    """
    value = _quoted_or_word()
    options = "|".join(re.escape(o) for o in WRAPPER_OPTIONS)
    skip = rf"(?:(?:{options})(?:=\S*|\s+{value})\s+)*"
    rest = rf"(?!\s+{re.escape(excluding)}\b)" if excluding else ""
    return rf"\s+{skip}(?:--\s+)?{USER_WORD}\b{rest}"


def user_verb_forms(verb: str) -> str:
    """`user <verb>` の形の正規表現（`launcher` の後ろに続ける。`--` は読み飛ばさない）。"""
    value = _quoted_or_word()
    options = "|".join(re.escape(o) for o in WRAPPER_OPTIONS)
    skip = rf"(?:(?:{options})(?:=\S*|\s+{value})\s+)*"
    return rf"\s+{skip}(?:--\s+)?{USER_WORD}\s+{re.escape(verb)}\b"


def user_script_pattern() -> re.Pattern[str]:
    """ユーザ専用の sh の名前。コマンド行のどこに出ても当てる。"""
    return re.compile(rf"(?<![\w-]){re.escape(USER_SCRIPT_PREFIX)}[\w-]+\.sh", re.IGNORECASE)

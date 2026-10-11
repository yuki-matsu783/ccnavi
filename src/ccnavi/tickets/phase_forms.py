"""ユーザだけが打つコマンドの形の見分け。承認・レビュー済みの記録・ガードの切り替え・記録の片付けを、
エージェントが打った形を止める組み込みのルールと文面。

コマンドの文字列だけを見て、チケットもマーカーも読まない。phase から分けた。phase を読まない。
"""

from __future__ import annotations

import os
import re

from ..infra import settings, shellread
from ..policy import rules, selfguard_shell

# 止めている間でも通す形。状態を動かす・レビューを頼む・合流して片付ける、の 3 本を、
# コマンドの位置で `sh` から呼ぶ形だけ。名前がどこかに含まれるだけでは通さない。
# 連結されたコマンドが 1 つでもこの形でなければ止める。
_EXEMPT_COMMAND = re.compile(r"^(sh|bash)\s+\S*ccnavi-(ticket|review|git)\.sh(\s|$)")
# 止めたときの文に添える、通る形の案内。案内どおりの 1 本に `cd … &&` や `| tail` を
# 付けると、連結の全部が上の形でないので止まる。文が「何を打つか」だけを言うと、
# 付け足した形で打って止まり、案内と拒否が食い違って見える。
EXEMPT_NOTE = (
    "止めている間に通るのは、ccnavi-ticket.sh・ccnavi-review.sh・ccnavi-git.sh を "
    "sh で単独で打つ形だけです。cd や | tail などを前後に付けると、その 1 本も止まります。"
)

# ユーザとモデルに見せる文で「ターン」を初めて使うところに置く表記。LLM の用語で、
# ユーザには定義を添えないと通じない。1 通の中では最初の 1 回だけに使う。
TURN_DEFINED = "ターン（ユーザが指示を出してから Claude が応答を終えるまで）"

# サブエージェントに許さない操作。状態を動かす形・レビューの形・リモートへ送る形を、
# コマンドの位置で。読むだけの `cat` や `--help` は止めない。
# push を含めるのは、リモートに置く枝は親ブランチ 1 本で、それを送るのが親の仕事だから。
# git のラッパースクリプトも子チケットのツリーからの push を拒むが、そちらは cwd のツリーで見る。
# サブエージェントが親のツリーへ cd して打てばラッパースクリプトは通すので、
# サブエージェントかどうかで止めるレイヤーをここに持つ。
_FORBIDDEN_COMMAND = re.compile(
    r"(^|[;&|]\s*)(sh|bash)(\s+-\S+)*\s+\S*ccnavi-(ticket|review|git)\.sh\s+"
    r"(start|finish|cancel|record-risk|request|confirm|comment|decide|ready|close-early|chat"
    r"|push)\b"
)

# シェルとして扱うツール。PowerShell は shellread で読めないので生の文字列に当てる。
SHELL_TOOLS = ("Bash", "PowerShell")

# レビューが済むまで止めるツール。
HELD_TOOLS = ("Agent", *SHELL_TOOLS)

# ccnavi 自身の実行ファイルを、ユーザの判断の経路に使う形。`--agree` `--reviewed`
# `--close-early` と、状態とレビューのサブコマンド。スクリプト 2 本の中身がこれなので、スクリプトを
# 経由せずに打てば止める。CCNAVI_GUARD_TICKET_APPROVAL で切れる。
# 前の名前 `--approve` も同じに止める。実行ファイルはもう受け付けないが、古い実行ファイルが
# 手元に残っていれば通ってしまう。止める側にだけ広がる。
# `--agree --preview` は一覧を見るだけ（承認済みチケットを置かない）ので除く。ただし除外は
# `--agree` の枝にしか掛けない。承認そのものを行う `--yes` は独立した枝で必ず当てる。
# 免除の条件を 1 つにまとめると、同じコマンドに `--preview` を書き足すだけで `--yes` まで
# 免除される。承認を通す形は、免除の理由が何であっても止める。
#
# 免除の範囲はコマンド 1 本まで。Bash なら shellread が `\x00` で切るが、PowerShell は
# 読めないので生の文字列に当たる（judge.screen）。生の文字列には `\x00` が無いので、
# 区切りとして `;` `&` `|` と改行も見る。見ないと、後ろのコマンドに書いた `--preview` が
# 前のコマンドの `--agree` を免除する。
# 語の中の目印（引用がつないだ空白）もまたがない。またぐと、引数の値に書いた
# `ccnavi --agree x "a --preview"` の `--preview` が免除の理由になる。
#
# 免除の理由になるのは、単独の語として現れた `--preview` だけ。 前は生の空白（`--agree` の
# 後ろに必ず 1 つある）、後ろは空白か区切りか行末。これを見ないと、別のフラグの値に書いた
# `--preview` で免除が成立する。`ccnavi --agree --reason=--preview` は、argparse が
# `--reason` の値として受け取るので `--preview` は単独の語にならず、実行ファイルは実際の
# `--agree` を走らせる。hook が見る文字列と、実行ファイルが走らせる枝がそこで食い違う。
# `=` を挟む形だけでなく、`--preview=x` や `x--preview` のように語にくっついた形も免除しない。
# 語の切れ目は生の空白だけで数える。語の中の目印（引用がつないだ空白）は数えない。
# 数えると、引用の中に書いた `"a --preview"` が単独の語に見えて、再び免除が成立する。
_PREVIEW_END = rf"[ \t;&|\r\n{re.escape(shellread.SEP)}]"
_PREVIEW_WORD = rf"[ \t]--preview(?={_PREVIEW_END}|$)"
_NOT_PREVIEW = rf"(?![^{selfguard_shell._NOT_A_WORD};&|\r\n]*{_PREVIEW_WORD})"
_CLI_FORMS = (
    rf"(--yes\b|--(?:agree|approve)\b{_NOT_PREVIEW}|--reviewed\b|--close-early\b"
    r"|\b(ticket|review)\s+"
    r"(start|finish|cancel|record-risk|prepare|requested|confirm|ready)\b)"
)
CODE_TICKET_APPROVAL = "DENY_TICKET_APPROVAL_CLI"
TICKET_APPROVAL_RULE_ID = "builtin-guard-ticket-approval"


def commands(subject: str) -> list[str]:
    """shellread が切ったコマンドのリスト。読めなかった生の文字列なら 1 本。"""
    return [c.strip() for c in subject.split("\x00") if c.strip()]


def exempt(subject: str, degraded: str) -> bool:
    """止めている間でも通してよいか。読み切れなかったコマンドは通さない。"""
    if degraded:
        return False
    parts = commands(subject)
    return bool(parts) and all(_EXEMPT_COMMAND.match(c) for c in parts)


def forbidden(subject: str, unwrapped: str = "") -> bool:
    """サブエージェントに許さない形を含むか。

    unwrapped は shellread が作る、中で実行されるコマンドのレイヤー（`\\x00` でつないだもの）。
    禁止の形はコマンドの先頭の `sh` に固定しているので、`env sh …` や `sh -c '…'` は
    元の形では当たらない。レイヤーにも当てる。止める側にだけ足す当て先で、`exempt` には渡さない。
    """
    return any(_FORBIDDEN_COMMAND.search(c) for c in commands(subject) + commands(unwrapped))


# ユーザの判断の経路のうち、hook のほかに保護が無い形。
# 組み込みの deny（`DENY_TICKET_APPROVAL_CLI`）で、実行ファイルの呼び方によらず止める。
#
# 1. 端末要求を切る形。実行ファイルは `CCNAVI_GUARD_TICKET_APPROVAL` と `--guard-ticket-approval` で
#    端末要求を外す（テストと CI のため）。切れなければ、端末を持たないエージェントは実行ファイルの
#    側で止まる
# 2. ボードの経路の形。`--yes`（承認と残った指摘）は端末を求めないので、ここが唯一の保護になる。
#    `--agree` / `--reviewed` と `--yes` の組、sh の `--choices` と `--digest` の組
#
# 実行ファイルを呼ぶ表記は追い切れない（`uv run -m ccnavi`、名前を変えたコピー、
# `awk` の `system()`、`python -c` に引数のリストで渡す形）。だからコマンドの位置は見ず、
# コマンド行の生の文字列の全体から、引用符と `\` を除いてから文字列を探す
# （`--y""es`・`"--yes"`・`--x\=y` を同じに読む）。外すのは、並んだコマンドが全部、
# 表示・検索・閲覧の道具（名前の完全一致）のときだけ。外す道具を並べ損ねても、止める側になるだけ。
_GUARD_NAME = "CCNAVI_GUARD_TICKET_APPROVAL"
# 変数を読むだけの形（`$X`・`${X}`・`$env:X`・`${env:X}`）。後ろに代入が続けば読むだけではない。
_GUARD_READ = re.compile(
    rf"\$\{{(?:env:)?{_GUARD_NAME}\}}(?!\s*[:+]?=)"
    rf"|\$(?:env:)?{_GUARD_NAME}(?![A-Za-z0-9_}}])(?!\s*[:+]?=)",
    re.IGNORECASE,
)
_GUARD_MENTION = re.compile(rf"(?<![A-Za-z0-9_]){_GUARD_NAME}(?![A-Za-z0-9_])", re.IGNORECASE)
_GUARD_FLAG = re.compile(
    r"--guard-ticket-approval(?:\s*=\s*|\s+)(?!enable(?![\w-]))", re.IGNORECASE
)
_BOARD_FORMS = (
    re.compile(r"(?<![\w-])--(?:agree|approve|reviewed)(?![\w-])", re.IGNORECASE),
    re.compile(r"(?<![\w-])--yes(?![\w-])", re.IGNORECASE),
)
_DECIDE_FORMS = (
    re.compile(r"(?<![\w-])--choices(?![\w-])", re.IGNORECASE),
    re.compile(r"(?<![\w-])--digest(?![\w-])", re.IGNORECASE),
)
# 表示・検索・閲覧だけをする道具。コマンドを実行する機能か変数に書く機能を持つもの（`sed` の `e`、
# `awk` の `system()`、`git` の別名、`xargs`、`find -exec`、`printf -v`）は入れない。
_READERS = frozenset(
    {"echo", "grep", "egrep", "fgrep", "rg", "cat", "less", "more", "head", "tail", "wc"}
)
_SEGMENT = re.compile(r"[;&|\n]+")
_ASSIGNMENT_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")


def _dequoted(subject: str) -> str:
    """引用符とバックスラッシュを除いた文字列。分け書き（`--y""es`）と引用を同じに読む。"""
    return re.sub(
        r"['\"`\\]", "", subject.replace(shellread.SEP, "\n").replace(shellread.WORD_SEP, " ")
    )


def _only_readers(text: str) -> bool:
    """並んだコマンドが全部、表示・検索・閲覧の道具か。"""
    for segment in _SEGMENT.split(text):
        words = segment.split()
        # 前置きの代入（`X=… cmd`）は飛ばす。代入そのものは別に見る
        while words and _ASSIGNMENT_WORD.match(words[0]):
            words = words[1:]
        if not words:
            continue
        name = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name not in _READERS:
            return False
    return True


def _commit_messages(subject: str) -> list[str]:
    """コミットの文面（`git commit` / `ccnavi-git.sh commit` の `-m` / `--message` の値）。

    文面は実行されないので、文字列を探す対象から外す。値の中にコマンド置換があれば実行されるので
    外さない。読み切れないコマンド行なら空（外さない側にする）。
    """
    found: list[str] = []
    for words, _ in shellread.placed(subject):
        names = [w.replace("\\", "/").rsplit("/", 1)[-1] for w in words]
        if "commit" not in words or not ({"git", "ccnavi-git.sh"} & set(names)):
            continue
        for i, word in enumerate(words):
            if word in ("-m", "--message") and i + 1 < len(words):
                value = words[i + 1]
            elif word.startswith("--message="):
                value = word.partition("=")[2]
            elif word.startswith("-m") and len(word) > 2 and not word.startswith("--"):
                value = word[2:]
            else:
                continue
            if "$(" not in value and "`" not in value:
                found.append(value)
    return found


def human_path_form(subject: str) -> tuple[str, str]:
    """hook のほかに保護が無いユーザの判断の形があれば（種類, 見つけた文字列）。無ければ空の組。

    種類は `guard-off`（端末要求を切る）か `board`（ボードの経路）。
    """
    text = _dequoted(subject)
    for message in _commit_messages(subject):
        text = text.replace(_dequoted(message), " ", 1)
    if _only_readers(text):
        return "", ""
    unread = _GUARD_READ.sub("", text)
    mention = _GUARD_MENTION.search(unread)
    if mention:
        return "guard-off", mention.group(0)
    flag = _GUARD_FLAG.search(text)
    if flag:
        return "guard-off", flag.group(0).strip()
    for pair in (_BOARD_FORMS, _DECIDE_FORMS):
        found = [rx.search(text) for rx in pair]
        if all(found):
            return "board", " … ".join(m.group(0) for m in found if m)
    return "", ""


def board_form_message(found: str) -> str:
    """ボードの経路の形で止めた文。"""
    return (
        f"ボードの経路の形（{found}）を、コマンド行に書いています。この形はユーザがボードの"
        "オーバーレイで押したものを拡張が打つためのもので、端末での確かめが無いので、エージェントが"
        "打った場合はここで止めます。承認と残った指摘の対応方針は、ユーザがボードか端末で決めます。"
        "この文字列を探したいだけなら、シェルの grep ではなく Grep ツールを使ってください。"
    )


def guard_off_message(found: str) -> str:
    """端末要求を切る形で止めた文。"""
    return (
        f"ユーザの判断の経路（承認・レビュー済み・早めに閉じる操作）で、端末からの入力を求めないようにする形（{found}）を、"
        "コマンド行に書いています。この変数とフラグは、テストや CI が端末を持たずに実行ファイルを"
        "回すためのもので、エージェントが置くものではありません。承認・レビュー済み・早めに閉じる操作はユーザが"
        "端末かボードで行います。この文字列を探したいだけなら、シェルの grep ではなく Grep ツールを"
        "使ってください。"
    )


# 記録と state を消す `ccnavi --prune`（`--preview` の無い形）。チケット制御と端末要求を切る設定に
# 依らず、組み込みの deny（`DENY_RECORDS_PRUNE`）で止める。実行ファイルの側の
# 端末要求は、擬似端末（`script -qc '…' /dev/null`）でも、チケット制御を切ったワークスペースで
# 端末要求を切る変数を前に並べても抜けられる。しきい値の環境変数を 0 に近づけて並べれば、
# 自分の呼び出しの記録を消せる。
#
# 見るのは human_path_form と同じく、引用符と `\` を除いたコマンド行の生の文字列。実行役の
# コマンド越し（`script -qc`・`bash -c`・`env`）でも同じ文字列が残る。コマンドの切れ目
# （`;` `&` `|` 改行）の中に ccnavi の名前と、単独の語の `--prune` が並べば止める。
# `git fetch --prune` や `ccnavi-git.sh fetch --prune` は ccnavi の名前（`ccnavi` と
# `ccnavi.exe` と実行ファイルの名前が、語の終わりで閉じた形）を持たないので当たらない。
#
# 免除は `--preview` が `--prune` の隣に単独の語として並んだ形だけ。間に引用符があれば
# 免除しない。免除をコマンドの中のどこかの `--preview` にすると、
# `bash -c "ccnavi --prune" x --preview` のように、実行役の引数に置いた `--preview` で免除が
# 成立する。
_PRUNE_WORD = re.compile(r"(?<!\S)--prune(?!\S)")
_PREVIEW_AFTER = re.compile(r"[ \t]+--preview(?![^\s;&|])")
_PREVIEW_BEFORE = re.compile(r"(?<!\S)--preview[ \t]+$")
_PRUNE_SEGMENT = re.compile(r"[^;&|\n]+")
CODE_RECORDS_PRUNE = "DENY_RECORDS_PRUNE"
RECORDS_PRUNE_RULE_ID = "builtin-guard-records-prune"


def _unquoted_marks(subject: str) -> tuple[str, set[int]]:
    """引用符とバックスラッシュを除いた文字列と、除いた場所（除いた後の位置）の組。"""
    text = subject.replace(shellread.SEP, "\n").replace(shellread.WORD_SEP, " ")
    out: list[str] = []
    cuts: set[int] = set()
    for ch in text:
        if ch in "'\"`\\":
            cuts.add(len(out))
            continue
        out.append(ch)
    return "".join(out), cuts


def _prune_names(bin_path: str) -> re.Pattern[str]:
    """ccnavi の名前。語の終わりで閉じた形だけ。"""
    names = [r"ccnavi(?:\.exe)?"]
    base = os.path.basename((bin_path or "").replace("\\", "/"))
    if base:
        names.append(re.escape(base))
    return re.compile(r"(?:" + "|".join(names) + r")(?=\s)", re.IGNORECASE)


def prune_form(subject: str, bin_path: str = "") -> str:
    """記録を消す `ccnavi --prune`（`--preview` の無い形）があれば、見つけた文字列。無ければ空。"""
    text, cuts = _unquoted_marks(subject)
    if "--prune" not in text or _only_readers(text):
        return ""
    names = _prune_names(bin_path)
    for segment in _PRUNE_SEGMENT.finditer(text):
        name = names.search(segment.group(0))
        if not name:
            continue
        for word in _PRUNE_WORD.finditer(text, segment.start() + name.end(), segment.end()):
            if not _previewed(text, cuts, word.start(), word.end()):
                return "--prune"
    return ""


def _previewed(text: str, cuts: set[int], start: int, end: int) -> bool:
    """`--prune`（start から end）の隣に、単独の語の `--preview` が引用をまたがずに並ぶか。"""
    after = _PREVIEW_AFTER.match(text, end)
    if after and not any(end <= i < after.end() for i in cuts):
        return True
    before = _PREVIEW_BEFORE.search(text, 0, start)
    if not before:
        return False
    gap = before.start() + len("--preview")
    return not any(gap <= i <= start for i in cuts)


def prune_message() -> str:
    """`--prune` で止めた文。"""
    return (
        "記録と state を消す 'ccnavi --prune' は、ユーザが端末から打つものです。記録は"
        "「ccnavi が何を判定したか」を後から確かめる元なので、エージェントからは消しません。"
        "何が消える対象かを見るだけなら 'ccnavi --prune --preview' は通ります。"
        "消す必要があれば、理由を添えてユーザに依頼してください。"
    )


def ticket_approval_rule(bin_path: str, root: str) -> rules.Rule:
    """ccnavi の実行ファイルをユーザの判断の経路に使う形を止めるルール。

    承認のスクリプト（`ccnavi-agree.sh`）も同じ形で止める。中身は `--agree` の
    呼び出しと承認済みチケットの push で、打つのは端末に座っているユーザ。
    実行ファイルの側は標準入力が端末であることを求めるので、hook から呼んでも通らないが、
    文字列で止めておけば「なぜ通らないのか」が当たったルールの id で分かる。

    承認の push（`ccnavi-push-approved.sh`）も止める。コミットして push することは
    合意そのものではないが、push は外へ出す操作で、その時機を決めるのはユーザ。

    親子のチケットの取り込み状態を消す `ccnavi-sync.sh --forget` も止める。
    削除せずに残した取り込み状態を消すと、決まらないで止めていた親子のチケット（gone など）が、
    取り込み状態の無いものに戻り、動けるようになる。

    ボードの経路の形（`--yes` の組、sh の `--choices` と `--digest`）と、端末要求を切る形は、
    ここではなく `human_path_form` が止める。実行ファイルのパスに頼らず見るため。
    """
    names = [r"ccnavi(\.exe)?"]
    clause = selfguard_shell.binary_clause(bin_path)
    if clause:
        names.append(clause)
    launcher = r"((uv\s+run\s+)?python[\w.]*\s+-m\s+ccnavi|(\S*[\\/])?(" + "|".join(names) + "))"
    script = (
        # `sh -x ...` のようにシェルに選択肢を付けた形も同じに見る。
        # `ccnavi-approve.sh` は `ccnavi-agree.sh` の前の名前。古いコピーが残っていても止める。
        r"(^|\x00|[;&|]\s*)((sh|bash)(\s+-\S+)*\s+)?\S*ccnavi-(agree|approve|push-approved)\.sh\b"
        # 親子のチケットの削除せずに残した取り込み状態を消す、ユーザが打つスクリプト。消すと、
        # 止めていた親子のチケットが取り込み状態の無いものとして今の手元の動きに戻るので、
        # 打つのはユーザ。
        r"|(^|\x00|[;&|]\s*)((sh|bash)(\s+-\S+)*\s+)?\S*ccnavi-sync\.sh\s[^\x00]*--forget\b"
        # ユーザの判断に使うスクリプト。中で `--reviewed --chat`・
        # `--close-early` を起こし、最後に承認の push を呼ぶ。打つのはユーザ。
        r"|(^|\x00|[;&|]\s*)((sh|bash)(\s+-\S+)*\s+)?\S*ccnavi-review\.sh\s+"
        r"(chat|close-early)\b"
    )
    # 大文字小文字を区別しない。Windows と macOS の既定のファイルシステムは名前の大小を
    # 区別しないので、`SH .ccnavi/scripts/CCNAVI-AGREE.sh` や `CCNAVI.EXE --agree` でも
    # 同じものが走る。区別すると表記を変えるだけで外せる。引数の形（_CLI_FORMS）まで
    # 広がるが、実行ファイルの引数は大小を区別するので、広がるのは止める側だけ
    # （`--PREVIEW` で免除の形になっても、実行ファイルがその引数を受け付けない）。
    expression = (
        rf"(?i)(^|\x00|[;&|]\s*)(&\s*)?{launcher}\s+[^\x00]*{_CLI_FORMS}"
        rf"|{script}"
    )
    rule = rules.Rule(
        id=TICKET_APPROVAL_RULE_ID,
        match="|".join(SHELL_TOOLS),
        regex=expression,
        message=(
            "ccnavi の承認・レビュー済みの受け入れ・チケットの状態の操作は、エージェントが"
            "直接打つものではありません。状態の移動とレビューは "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')}' と "
            f"'{settings.script_command(root, 'ccnavi-review.sh')}' を"
            "使い、承認はユーザが VS Code のボードか "
            f"'{settings.script_command(root, 'ccnavi-agree.sh')}' で、"
            "残った指摘の対応方針はユーザがボードか端末で決めます。"
            "承認済みチケットのコミットと push"
            f"（'{settings.script_command(root, 'ccnavi-push-approved.sh')}'）もユーザが打ちます。"
            "親子のチケットの取り込み状態を消す "
            f"'{settings.script_command(root, 'ccnavi-sync.sh')} --forget' と、ユーザの判断の入口"
            f"（'{settings.script_command(root, 'ccnavi-review.sh')} chat / "
            "close-early'）もユーザが打ちます。"
            "ボードで承認すると、承認済みチケットのコミットと push が端末で実行されます。"
            "エージェントは打ちません。"
            "承認待ちの一覧を見るだけなら 'ccnavi --agree --preview' は通ります。"
        ),
        decision=rules.DENY,
    )
    rule.compiled = re.compile(expression)
    return rule

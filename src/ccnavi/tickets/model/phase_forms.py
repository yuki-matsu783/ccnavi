"""ユーザだけが打つコマンドの形の見分け。承認・レビュー済みの記録・ガードの切り替え・記録の片付けを、
エージェントが打った形を止める組み込みのルールと文面。

コマンドの文字列だけを見て、チケットもマーカーも読まない。phase から分けた。phase を読まない。
"""

from __future__ import annotations

import os
import re

from ...infra import shellread, shellread_words, toolnames
from ...policy import rules, selfguard_shell, texts
from . import user_only

# 何がユーザ専用かは `user_only` の表が正本。この module はその表から正規表現とオプションの一覧を
# 組み立て、コマンド行に適用する。操作を足す・外すときは表の行を直し、ここの正規表現は直さない。

# 止めている間でも通す形。状態を動かす・レビューを頼む・合流して片付ける、の 3 本を、
# コマンドの位置で `sh` から呼ぶ形だけ。名前がどこかに含まれるだけでは通さない。
# 連結されたコマンドが 1 つでもこの形でなければ止める。
_EXEMPT_COMMAND = re.compile(rf"^(sh|bash)\s+\S*{user_only.agent_script_forms()}(\s|$)")
# 止めたときの文に添える、通る形の案内。案内どおりの 1 本に `cd … &&` や `| tail` を
# 付けると、連結の全部が上の形でないので止まる。文が「何を打つか」だけを言うと、
# 付け足した形で打って止まり、案内と拒否が食い違って見える。
EXEMPT_NOTE = (
    "止めている間に通るのは、ccnavi-ticket.sh・ccnavi-review.sh・ccnavi-git.sh を "
    "sh で単独で実行する形だけです。cd や | tail などを前後に付けると、そのコマンドも止まります。"
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
    user_only.subagent_forbidden_forms(),
    # 名前の大小は問わない（`CCNAVI-TICKET.SH`）。大小を区別しないファイルシステムでは
    # 同じ sh が走る。
    re.IGNORECASE,
)

# レビューが済むまで止めるツール。
HELD_TOOLS = (*toolnames.AGENT_TOOLS, *toolnames.SHELL_TOOLS)

# ccnavi 自身の実行ファイルを、ユーザの判断の経路に使う形。`--agree` `--reviewed`
# `--close-early` と、状態とレビューのサブコマンド。スクリプト 2 本の中身がこれなので、スクリプトを
# 経由せずに打てば止める。CCNAVI_GUARD_TICKET_APPROVAL で切れる。
# `--agree --preview` は一覧を見るだけ（承認済みチケットを置かない）ので除く。ただし除外は
# `--agree` の枝にしか掛けない。承認そのものを行う `--yes` は独立した枝で必ず適用する。
# 免除の条件を 1 つにまとめると、同じコマンドに `--preview` を書き足すだけで `--yes` まで
# 免除される。承認を通す形は、免除の理由が何であっても止める。
#
# 免除の範囲はコマンド 1 本まで。Bash なら shellread が `\x00` で切るが、PowerShell は
# 読めないので生の文字列にヒットする（judge.screen）。生の文字列には `\x00` が無いので、
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
#
# 実行ファイルを直に呼ぶ形のオプションとサブコマンドは、表（user_only.FLAGS・SUBS）から組み立てる。
# オプションごとの理由（テンプレートの取り込みを下見も含めて止める理由、親を閉じたときの片付けを
# エージェントに打たせない理由）は、表の行のコメントにある。
_CLI_FORMS = user_only.cli_forms(_NOT_PREVIEW)
CODE_TICKET_APPROVAL = "DENY_TICKET_APPROVAL_CLI"
TICKET_APPROVAL_RULE_ID = "builtin:guard-ticket-approval"


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
    元の形ではヒットしない。レイヤーにも適用する。止める側にだけ足す適用先で、
    `exempt` には渡さない。
    """
    return any(_FORBIDDEN_COMMAND.search(c) for c in commands(subject) + commands(unwrapped))


# ユーザの判断の経路のうち、hook のほかに保護が無い形。
# 組み込みの deny（`DENY_TICKET_APPROVAL_CLI`）で、実行ファイルの呼び方によらず止める。
#
# 1. ターミナル要求を切る形。
# 実行ファイルは `CCNAVI_GUARD_TICKET_APPROVAL` と `--guard-ticket-approval` で
#    ターミナル要求を外す（テストと CI のため）。切れなければ、
#    ターミナルを持たないエージェントは実行ファイルの
#    側で止まる
# 2. ボードの経路の形。`--yes`（承認と残った指摘）はターミナルを求めないので、
# ここが唯一の保護になる。
#    `--agree` / `--reviewed` と `--yes` の組み合わせ、
#    sh の `--choices` と `--digest` の組み合わせ、
#    `user handoff` と `--yes` の組み合わせ（保護対象の下書きの取り込み。
#    ボードの画面だけが `--via board` で呼ぶ）
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
# `CCNAVI_` で始まる名前の途中に展開が混じる形（`env CCNAVI_GUARD_TICKET_${X}APPROVAL=disable`・
# `export "CCNAVI_GUARD_`printf TICKET`_APPROVAL=disable"`）。実行の時点でターミナル要求を切る変数の
# 名前になりうるので、名前を文字どおりに書いた形（_GUARD_MENTION）と同じに止める。前が `$`・`{` の
# 形（`$CCNAVI_ROOT$X`・`${CCNAVI_X}`）は変数を読むだけなので数えない。
_GUARD_EXPANDED_NAME = re.compile(r"(?<![A-Za-z0-9_${])CCNAVI_[A-Za-z0-9_]*[$`]", re.IGNORECASE)
# 展開（`$`・バッククォート）を含む `--` で始まる語（`--y${X}es`・`--y$(echo es)`）。
# `user` の語が同じコマンド行にあれば、入口を読めない呼び方（名前を変えたコピーなど）でも止める。
# 入口の表記の検査（launcher）と組み合わせの検査（BOARD_COMBOS）は、展開で組み立てた語を
# 文字として読めないため。暫定の止めで、`user` と関係の無い語の展開まで止める側に広がる。
_EXPANDED_OPTION = re.compile(r"(?<![^\s\x00;&|(])--[^\s\x00;&|]*[$`]")
# 単独の語の `user`（`user.email` や `--user` は数えない）。
_USER_ALONE = re.compile(
    rf"(?<![^\s\x00;&|(=]){user_only.USER_WORD}(?![^\s\x00;&|)])", re.IGNORECASE
)
_GUARD_FLAG = re.compile(
    r"--guard-ticket-approval(?:\s*=\s*|\s+)(?!enable(?![\w-]))", re.IGNORECASE
)
# ボードの経路のオプションの組み合わせ。組み合わせの中身と並びは表（user_only.BOARD_COMBOS）にある。
_BOARD_COMBOS = user_only.board_combos()
# ユーザ専用の sh（`ccnavi-user-*.sh`）の名前。名前を変えたコピーでも、コマンド行に名前が
# 出れば止める。
_USER_SCRIPT = user_only.user_script_pattern()
# 表示・検索・閲覧だけをする道具。コマンドを実行する機能か変数に書く機能を持つもの（`sed` の `e`、
# `awk` の `system()`、`git` の別名、`xargs`、`find -exec`、`printf -v`）は入れない。
_READERS = frozenset(
    {"echo", "grep", "egrep", "fgrep", "rg", "cat", "less", "more", "head", "tail", "wc"}
)
_SEGMENT = re.compile(r"[;&|\n]+")
_ASSIGNMENT_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")


def _dequoted(subject: str, *, keep_ticks: bool = False) -> str:
    """引用符とバックスラッシュを除いた文字列。分け書き（`--y""es`）と引用を同じに読む。

    `keep_ticks` なら、バッククォート（コマンド置換）は残す。展開の有無を見る検査が使う。
    """
    return re.sub(
        r"['\"\\]" if keep_ticks else r"['\"`\\]",
        "",
        subject.replace(shellread.SEP, "\n").replace(shellread.WORD_SEP, " "),
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
    """hook のほかに保護が無いユーザの判断の形があれば（種類, 見つけた文字列）。
    無ければ空の文字列 2 つ。

    種類は `guard-off`（ターミナル要求を切る）・`user-script`（ユーザ専用の sh）・
    `board`（ボードの経路）・`expanded`（`user` の語と、展開を含む `--` の語が並ぶ）。
    """
    text = _dequoted(subject)
    ticked = _dequoted(subject, keep_ticks=True)
    for message in _commit_messages(subject):
        text = text.replace(_dequoted(message), " ", 1)
        ticked = ticked.replace(_dequoted(message, keep_ticks=True), " ", 1)
    if _only_readers(text):
        return "", ""
    unread = _GUARD_READ.sub("", text)
    mention = _GUARD_MENTION.search(unread)
    if mention:
        return "guard-off", mention.group(0)
    expanded_name = _GUARD_EXPANDED_NAME.search(ticked)
    if expanded_name:
        return "guard-off", expanded_name.group(0)
    flag = _GUARD_FLAG.search(text)
    if flag:
        return "guard-off", flag.group(0).strip()
    script = _USER_SCRIPT.search(text)
    if script:
        return "user-script", script.group(0)
    for combo in _BOARD_COMBOS:
        found = [rx.search(text) for rx in combo]
        if all(found):
            return "board", " … ".join(m.group(0) for m in found if m)
    option = _EXPANDED_OPTION.search(ticked)
    if option and _USER_ALONE.search(ticked):
        word = ticked[option.start() :].split(None, 1)[0]
        return "expanded", word
    return "", ""


def expanded_option_message(root: str, found: str) -> str:
    """`user` の語と、展開を含む `--` の語が並ぶ形で止めた文。"""
    return texts.say(root, "DENY_TICKET_APPROVAL_CLI__EXPANDED", {"found": found})


def board_form_message(root: str, found: str) -> str:
    """ボードの経路の形で止めた文。"""
    # 取り込みの形は、承認と残った指摘の対応方針の文にヒットしない。
    lowered = found.casefold()
    decided = (
        "mirror"
        if "mirror" in lowered
        else "handoff"
        if "handoff" in lowered
        else "via"
        if lowered.startswith("--via")
        else "approval"
    )
    return texts.say(root, "DENY_TICKET_APPROVAL_CLI__BOARD", {"found": found}, {"why": decided})


def user_script_message(root: str, found: str) -> str:
    """ユーザ専用の sh の名前（`ccnavi-user-*.sh`）で止めた文。"""
    return texts.say(root, "DENY_TICKET_APPROVAL_CLI__USER_SCRIPT", {"found": found})


def guard_off_message(root: str, found: str) -> str:
    """ターミナル要求を切る形で止めた文。"""
    return texts.say(root, "DENY_TICKET_APPROVAL_CLI__GUARD_OFF", {"found": found})


# ユーザがターミナルで打つ入口のうち、引数を読み切れない形。human_path_form と組み込みの script 節は
# 文字として並んだ表記で見るので、引数を変数・置換・glob で組み立てると外れる（ADR-0122）。
# 次の 2 つは 2 段目（実行ファイルのターミナル要求）が無く、hook が唯一の保護なので、止める操作を
# 組み立てうるなら止める。組み立てられない形（読むだけの形）は通す。
#
# - `ccnavi-review.sh decide`: `--choices` はボードの経路で、ターミナルを求めない。`--choices` に
#   なりうる語があれば止める。文字どおりの `--preview` があれば通す
#   （sh が `--choices` と一緒に断る）
# - 実行ファイル（launcher を含む）の `--agree` / `--reviewed` と `--yes`: ターミナルを求めない。
#   承認のオプションと `--yes` がどちらも文字か展開でそろいうるときと、ターミナル要求を切る
#   `--guard-ticket-approval` になりうる語があるときに止める。展開を含む語より前に、診断のオプション
#   （`--docs`・`--test` など）か実行ファイルの知らないオプションがあれば通す
#   （承認の経路に進まない）。
#   文字どおりの `--agree --preview` も通す（`--yes` が来ても実行ファイルが断る）
#
# 展開と glob は、引用されていない部分と二重引用符の中（glob は引用されていない部分）だけで
# 数える。単引用符の中はシェルが展開しないので文字どおりに読む。リダイレクトの行き先は
# 入口に渡る引数ではないので数えない。
#
# 語はコマンドの位置から見る（`shellread.placed` と、実行役を外した層）。前に置けるのは
# シェル・busybox・`.`・シェルの選択肢・リダイレクト・代入だけ。`sh -x` や `sh < /dev/null` を
# 挟んでも外れず、`git log --grep ccnavi-sync.sh` のように文字列として出るだけの形にはヒットしない。
# 引数を外から流し込む形（`xargs`、入口を `<` で読ませる形）は、入口の名前が出た時点で止める。
# 標準入力から読むシェル（パイプの先の `sh`・`bash`、`sh -s`、ヒアドキュメント・ヒアストリング）が
# あれば、コマンド行の文字列のどこかに入口の名前があるだけで止める（流し込む文字列の中の語は
# 語として読めない）。走査が止まる形（バッククォート）は生の文字列の語で見る。
_ENTRY_REVIEW = "ccnavi-review.sh"
_ENTRY_EXE = frozenset({"ccnavi", "ccnavi.exe", "ccnavi.__main__", "ccnavi-launcher.sh"})
# 入口の前に置けるコマンド。どれも後ろの語をそのまま実行する。
_ENTRY_PREFIX = frozenset(
    {"sh", "bash", "dash", "zsh", "ksh", "ash", "busybox", ".", "source", "exec", "command"}
)
# 後ろの語を実行する、Python のツールの実行役（`uv run`・`uvx`・`pipx run`・`poetry run`・
# `pipenv run`）。間に置いた選択肢と値は読み分けず、後ろに入口の名前があればコマンドの位置とみなす。
_TOOL_RUNNERS = frozenset({"uv", "uvx", "pipx", "poetry", "pipenv"})
_STDIN_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash"})
_FEEDERS = frozenset({"xargs", "parallel"})
# 展開・置換・glob・find -exec の置き場。どれもシェルか実行役が実行の時点で値を決める。
_OPEN = re.compile(r"[$`*?\[]|\{\}")
_OPEN_PART = re.compile(
    r"\$\{[^}]*\}?|\$\([^)]*\)?|\$[A-Za-z0-9_@*#?$!-]*|`[^`]*`?|\[[^\]]*\]?|\{\}|[*?]"
)
_APPROVAL_FLAGS = user_only.names("approval")
_YES_FLAGS = user_only.names("yes")
_IMPORT_FLAGS = user_only.names("handoff")
_GUARD_FLAGS = user_only.names("guard")
_ABANDON_FLAGS = user_only.names("abandon")
_CHOICES_FLAGS = user_only.names("choices", where="ccnavi-review")
# 実行ファイルの診断のオプション。どれも承認の経路
# （`--agree` / `--reviewed` / `--close-early`）より前に
# 分かれて、承認を行わない（cli._parsed の振り分けの順）。
_EXE_DIAGNOSES = frozenset(
    {
        "--docs",
        "--lint",
        "--test",
        "--test-expectations",
        "--explain",
        "--suggest",
        "--version",
        "--help",
        "-h",
    }
)
# 実行ファイルが受けるオプション（`ccnavi --version --json` の flags と、短い形の `-h` `-r`）。
# ここに無いオプション（`--list` など）は実行ファイルが使い方の誤りとして断り、何も動かさない。
# オプションを足したらここにも足す（tests/ticket/test_ticket.py が flags と比べる）。
EXE_OPTIONS = frozenset(
    {
        "--abandon", "--accept-check", "--accept-done", "--accept-plan",
        "--accept-unresolved", "--actor", "--agent-id", "--agree", "--all", "--approved",
        "--body-file", "--check", "--check-file",
        "--choose-out", "--close-early", "--cwd", "--digest", "--docs", "--draft",
        "--expect",
        "--explain", "--feedback", "--fill-phases", "--flow", "--format", "--guard-core-files",
        "--guard-ticket-approval", "--handoff", "--help", "--issue",
        "--json",
        "--keyword", "--layer", "--learnings", "--limit", "--lint", "--log", "--mirror",
        "--mode", "--mr",
        "--name",
        "--no-issue",
        "--no-refresh",
        "--note", "--order",
        "--path",
        "--permission-mode", "--phase", "--phases", "--placed-out", "--plan", "--plan-order",
        "--preview",
        "--project-home", "--project-phases-file", "--project-risk-file", "--project-rules-file",
        "--projects", "--prune", "--reason", "--record-tree", "--record-writes",
        "--restore-if-deny", "--result", "--reverse", "--reviewed", "--risk", "--root", "--rules",
        "--scope", "--show", "--signal", "--since", "--sort", "--state", "--suggest", "--tag",
        "--target",
        "--template", "--templates",
        "--test",
        "--test-expectations",
        "--text", "--ticket-control", "--tickets", "--title", "--to", "--type", "--until",
        "--verify",
        "--version", "--via", "--with-feedback", "--write", "--yes", "-h", "-r",
    }
)  # fmt: skip
# 単引用符の中と、glob を引用した文字。文字どおりの値として読むため、
# 走査の前に印の文字へ置き換える。
_LITERAL_MARKS = {"$": "\ue024", "`": "\ue060", "*": "\ue02a", "?": "\ue03f", "[": "\ue05b"}
_GLOB_MARKS = ("*", "?", "[")
_UNMARK = str.maketrans({mark: ch for ch, mark in _LITERAL_MARKS.items()})


def _base_name(word: str) -> str:
    """語の最後の要素を小文字で。"""
    return word.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _is_open(word: str) -> bool:
    return bool(_OPEN.search(word))


def _could_be(word: str, flags: tuple[str, ...]) -> bool:
    """語が実行の時点で flags のどれか（`=` で値を付けた形を含む）になりうるか。"""
    if not _is_open(word):
        return any(word == f or word.startswith(f + "=") for f in flags)
    pattern, last = [], 0
    for part in _OPEN_PART.finditer(word):
        pattern.append(re.escape(word[last : part.start()]))
        pattern.append("." if part.group(0) == "?" or part.group(0).startswith("[") else ".*")
        last = part.end()
    pattern.append(re.escape(word[last:]))
    rx = re.compile("".join(pattern), re.IGNORECASE | re.DOTALL)
    return any(rx.fullmatch(f) or rx.fullmatch(f + "=x") for f in flags)


def _drop_redirects(args: list[str]) -> list[str]:
    """リダイレクト（演算子と行き先）を除いた引数。行き先は入口に渡らない。"""
    kept: list[str] = []
    k = 0
    while k < len(args):
        width = shellread_words._redirect_width(args, k)
        if width:
            k += width
            continue
        kept.append(args[k])
        k += 1
    return kept


def _literal_quoted(subject: str) -> str:
    """単引用符の中の展開と glob、二重引用符の中と `\\` の後の glob を、文字どおりの印に置き換える。

    置き換えた語は展開を含む語として数えない。読み切れない形（置換・ヒアドキュメント・
    `$'…'`・閉じない引用符）は置き換えず、そのまま返す（止める側に倒す）。
    """
    if "$(" in subject or "`" in subject or "<<" in subject or "$'" in subject:
        return subject
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(subject):
        ch = subject[i]
        if quote == "'":
            if ch == "'":
                quote = ""
                out.append(ch)
            else:
                out.append(_LITERAL_MARKS.get(ch, ch))
        elif ch == "\\" and i + 1 < len(subject):
            following = subject[i + 1]
            literal = following in _GLOB_MARKS or following in "$`"
            out.append(ch + (_LITERAL_MARKS[following] if literal else following))
            i += 2
            continue
        elif quote == '"':
            if ch == '"':
                quote = ""
            out.append(_LITERAL_MARKS[ch] if ch in _GLOB_MARKS else ch)
        else:
            if ch in "'\"":
                quote = ch
            out.append(ch)
        i += 1
    return subject if quote else "".join(out)


def _entry_kind(word: str, bin_base: str) -> str:
    base = _base_name(word)
    if base == _ENTRY_REVIEW:
        return base
    if base in _ENTRY_EXE or (bin_base and base == bin_base):
        return "ccnavi"
    return ""


def _at_command_position(words: list[str], i: int) -> bool:
    """words[i] がコマンドの位置か。前に置けるのは入口の前置き・選択肢・リダイレクト・代入だけ。"""
    if i and words[i - 1] == "-m":
        return True
    j = 0
    while j < i:
        width = shellread_words._redirect_width(words, j)
        if width:
            j += width
            continue
        word = words[j]
        if _base_name(word) in _TOOL_RUNNERS:
            return True
        if word in ("-o", "+o"):
            j += 2
            continue
        if (
            word[:1] in ("-", "+")
            or _base_name(word) in _ENTRY_PREFIX
            or shellread_words._ASSIGNMENT_WORD.match(word)
        ):
            j += 1
            continue
        return False
    return j == i


def _first_after_wrapper_options(args: list[str]) -> str:
    """ラッパーが先に付ける値つきのオプション（`--root X` など）を読み飛ばした後の最初の語。"""
    at = 0
    while at < len(args):
        word = args[at]
        if word in user_only.WRAPPER_OPTIONS:
            at += 2
        elif word.partition("=")[0] in user_only.WRAPPER_OPTIONS and "=" in word:
            at += 1
        else:
            break
    return args[at] if at < len(args) else ""


def _hidden_args(kind: str, args: list[str]) -> str:
    """入口の引数のうち、止める理由になる語。無ければ空。

    文字として並んだ `--choices` と `--digest` の組み合わせは human_path_form が先に止める。

    処理フロー:
    1. リダイレクトを外す。
    2. `review`（ボードの入口）は、`decide` の語があれば、`--preview` か展開のない形を通し、
       それ以外は選択肢の旗になりうる語を返す。
    3. 展開を含む語が無ければ空を返す。
    4. ラッパーの後ろの最初の語が `user` になりうるなら、それを返す。
    5. 展開の前の文字どおりの旗を見る。`EXE_OPTIONS` に無い旗か診断の旗があれば、入口は動かないので
       空を返す。全部 `EXE_OPTIONS` なら 6・7 へ。`--agree` と `--preview` が両方あれば空を返す。
    6. 承認を隠す旗（`_GUARD_FLAGS`）、worktree の放棄（`_abandon_word`）の順に見て返す。
    7. 承認・取り込みの旗と `--yes` が揃っていれば、その旗を返す。
    """
    args = _drop_redirects(args)
    if kind == _ENTRY_REVIEW:
        sub = args[0] if args else ""
        if "decide" in [a.lower() for a in args] or (_is_open(sub) and _could_be(sub, ("decide",))):
            if "--preview" in args or not any(_is_open(a) for a in args):
                return ""
            return next((a for a in args if _is_open(a) and _could_be(a, _CHOICES_FLAGS)), "")
        return ""
    if not any(_is_open(a) for a in args):
        return ""
    # ラッパーのオプションを読み飛ばした後の最初の語が、`user` になりうるか。
    first = _first_after_wrapper_options(args)
    if first and _is_open(first) and _could_be(first, (user_only.USER_WORD,)):
        return first
    # 展開を含む語より前の、文字どおりのオプション（`--` の後はオプションではない）。
    head: list[str] = []
    for arg in args:
        if _is_open(arg) or arg == "--":
            break
        head.append(arg)
    flags = {a.partition("=")[0] for a in head if a.startswith("-") and a != "-"}
    if flags & _EXE_DIAGNOSES or flags - EXE_OPTIONS:
        return ""
    if {"--agree", "--preview"} <= flags:
        return ""
    opened = [a for a in args if _is_open(a)]
    guard = [a for a in opened if _could_be(a, _GUARD_FLAGS)]
    if guard:
        return guard[0]
    abandon = _abandon_word(args, opened)
    if abandon:
        return abandon
    # 下書きの取り込み（`--handoff` と `--yes`）も、承認と同じく、
    # 組み合わせを読めない形にして隠せる。
    if any(_could_be(a, _APPROVAL_FLAGS + _IMPORT_FLAGS) for a in args) and any(
        _could_be(a, _YES_FLAGS) for a in args
    ):
        return next(a for a in opened if _could_be(a, _APPROVAL_FLAGS + _IMPORT_FLAGS + _YES_FLAGS))
    return ""


def _abandon_word(args: list[str], opened: list[str]) -> str:
    """`worktree retire --abandon`（放棄した親の記録を消す操作）を組み立てうる語。無ければ空。

    文字どおりの `worktree retire` は `_CLI_FORMS` が止める。ここは、サブコマンドの `retire` か
    `--abandon` を変数・glob で組み立てて外す形を止める。
    """
    if "worktree" not in args:
        return ""
    for arg in opened:
        if _could_be(arg, _ABANDON_FLAGS):
            return arg
    verb = args.index("worktree") + 1
    if verb < len(args) and _is_open(args[verb]) and _could_be(args[verb], ("retire",)):
        return args[verb]
    return ""


def _marked_suffix(words: list[str], placed: list[tuple[str, list[str]]]) -> list[str]:
    """層の語が外側のコマンドの後ろ側と同じなら、単引用の印の残った外側の語を返す。

    実行役を外した層（`sh <入口> '$X'` の `<入口> $X`）は引用を外して組み直してあるので、
    単引用の中の `$X` も展開に見える。外側のコマンドの語には印が残っているので、そちらを使う。
    `sh -c '…'` の文字列を読み直した層は外側の語と並びが合わないので、そのまま（止める側）。
    """
    for _, outer in placed:
        tail = outer[len(outer) - len(words) :]
        if len(outer) > len(words) and [w.translate(_UNMARK) for w in tail] == words:
            return tail
    return words


def _entry_commands(
    subject: str, inner: list[tuple[str, str]]
) -> tuple[list[tuple[str, list[str]]], bool]:
    """入口を探す（実行役の名前, 語のリスト）と、コマンドの位置を見るか。

    読めなければ生の文字列の語で、位置は見ない（読めなければ止める側に倒す）。
    """
    placed = [("", words) for words, _ in shellread.placed(_literal_quoted(subject))]
    if placed:
        layered = [
            (
                runner,
                _marked_suffix(
                    [w.replace(shellread.WORD_SEP, " ") for w in layer.split(" ") if w], placed
                ),
            )
            for runner, layer in inner
            if layer
        ]
        return placed + layered, True
    text = subject.replace(shellread.SEP, "\n").replace(shellread.WORD_SEP, " ")
    text = re.sub(r"['\"\\]", "", text)
    if _only_readers(text):
        return [], False
    return [("", segment.split()) for segment in _SEGMENT.split(text)], False


def hidden_user_entry(
    subject: str, inner: list[tuple[str, str]] | None = None, bin_path: str = ""
) -> tuple[str, str]:
    """ユーザがターミナルで打つ入口に、読み切れない引数を渡す形があれば（入口, 見つけた語）。

    無ければ空の組み合わせ。

    inner は shellread が作る、実行役のコマンドを外した層の（実行役の名前, 層）。`sh -c '…'` の中や
    `xargs` が実行するコマンドがここに並ぶ。
    組み込みの script 節（`_SCRIPT_FORMS`）がヒットする形は、
    同じルールの文面で止まるように空を返す。
    """
    inner = list(inner or [])
    entry, word = _hidden_entry(subject, inner, bin_path)
    return entry, word.translate(_UNMARK)


def _hidden_entry(subject: str, inner: list[tuple[str, str]], bin_path: str) -> tuple[str, str]:
    """`hidden_user_entry` の本体。見つけた（入口, 隠れた語）、無ければ空の組。

    処理フロー:
    1. `_entry_commands` で、実行されるコマンドの並びと、位置を見るかを得る。
    2. 語ごとに `_entry_kind` で入口かを見る。実行か並べて数える入口の名前は `named` に覚える。
    3. 入口のファイルを `<` で読ませているなら、その形を返す。
    4. コマンドの位置に無い入口は飛ばす（位置を見るとき）。
    5. 入口を外から流し込む実行役（`_FEEDERS`、`xargs` など）がいれば、その名で返す。
    6. `_hidden_args` で入口の後ろの語を見て、隠れた語があれば返す。
    7. 標準入力から読むシェルと入口の名前が両方揃えば、`STDIN_SHELL` を返す。
    """
    bin_base = _base_name(bin_path) if bin_path else ""
    commands_, positional = _entry_commands(subject, inner)
    stdin_shell = False
    named = ""
    for runner, words in commands_:
        stdin_shell = stdin_shell or (positional and shellread_words.reads_stdin(words))
        for i, word in enumerate(words):
            kind = _entry_kind(word, bin_base)
            if not kind:
                continue
            # 標準入力から読むシェルと並べて数える名前。sh の入口はファイルとして渡すだけで中身が
            # 流れ込む（`cat …ccnavi-review.sh | sh -s`）。実行ファイルの名前は、実行される語として
            # 立つときだけ数える（`cd /home/user/ccnavi`・`sh -s -- ccnavi` は数えない）。
            if (
                kind != "ccnavi"
                or _base_name(word) == "ccnavi-launcher.sh"
                or not positional
                or _executed_entry(words[: i + 1], bin_base)
            ):
                named = named or kind
            # 入口のファイルを標準入力から読ませる
            if i and words[i - 1].endswith("<"):
                return kind, "< " + word
            if positional and not _at_command_position(words, i):
                continue
            # 引数を外から流し込む実行役（`xargs`）が入口を実行する。読めない形なら名前で止める
            feeder = runner if runner in _FEEDERS else ""
            if not positional:
                feeder = next((w for w in words[:i] if _base_name(w) in _FEEDERS), "")
            if feeder:
                return kind, feeder
            found = _hidden_args(kind, words[i + 1 :])
            if found:
                return kind, found
    if stdin_shell:
        named = named or _named_in_text(subject, bin_base)
    if stdin_shell and named:
        return named, STDIN_SHELL
    return "", ""


STDIN_SHELL = "標準入力から読むシェル"
_NAMED_SCRIPTS = frozenset({_ENTRY_REVIEW, "ccnavi-launcher.sh"})
# 流し込む文字列を、コマンドの切れ目と引用符で切る。引用した文字列は 1 本のコマンド行として読む。
_STRING_SEGMENT = re.compile(r"[;&|\n()`'\"]+")
# 実行される語の前に置けるもの（入口の前置きのほか、後ろの語を実行するコマンド）。
_EXEC_PREFIX = _ENTRY_PREFIX | {"env", "nice", "nohup", "time", "sudo", "timeout"}


def _named_in_text(subject: str, bin_base: str) -> str:
    """コマンド行の文字列の中で、入口の名前が実行される語として現れれば、その入口。

    標準入力から読むシェルへ流し込む文字列（`echo "…" | sh`・ヒアドキュメント・ヒアストリング）の
    中は語として読めないので、文字列をコマンドの切れ目と引用符で切り、それぞれの頭の語を見る。
    頭の前置き（シェル・`env`・`uv run` など）と代入と選択肢は飛ばす。パスの一部（`cd /x/ccnavi`）・
    コメント・引数（`sh -s -- ccnavi`）として出るだけの名前は数えない。
    """
    for segment in _STRING_SEGMENT.split(subject.replace(shellread.SEP, "\n")):
        found = _executed_entry(segment.split(), bin_base)
        if found:
            return found
    return ""


def _executed_entry(words: list[str], bin_base: str) -> str:
    """語の並びの頭（前置きを飛ばした先）が入口なら、その入口。

    処理フロー:
    1. 代入・ツールの実行役・シェル・`env` などの前置きを飛ばす。シェルは印を付ける。
    2. シェルの選択肢で `-s` か `-c` を含むものにヒットしたら、後ろは引数か文字列なので空を返す。
    3. 残った頭の語が名前付きの入口（`_NAMED_SCRIPTS`）なら、その名前を返す。
       ただし `ccnavi-launcher.sh` は `ccnavi` と名付ける。
    4. 標準入力のシェルの後ろでなく、実行ファイルの名前（`_ENTRY_EXE` か `bin_base`）なら `ccnavi`。
    """
    shell = False
    k = 0
    while k < len(words):
        word = words[k]
        base = _base_name(word)
        if shellread_words._ASSIGNMENT_WORD.match(word):
            k += 1
            continue
        if base in _TOOL_RUNNERS or (
            base == "run" and k and _base_name(words[k - 1]) in _TOOL_RUNNERS
        ):
            k += 1
            continue
        if base in _STDIN_SHELLS:
            shell = True
            k += 1
            continue
        if (
            base in _EXEC_PREFIX
            or base == "-m"
            or base.startswith("python")
            or base in ("py", "py.exe")
        ):
            k += 1
            continue
        if word[:1] in ("-", "+"):
            # シェルの選択肢。`-s` / `-c` の後ろは引数か文字列で、実行される語ではない。
            if shell and not word.startswith("--") and set(word[1:]) & {"s", "c"}:
                return ""
            k += 1
            continue
        break
    if k >= len(words):
        return ""
    base = _base_name(words[k])
    if base in _NAMED_SCRIPTS:
        return "ccnavi" if base == "ccnavi-launcher.sh" else base
    if not shell and (base in _ENTRY_EXE or (bin_base and base == bin_base)):
        return "ccnavi"
    return ""


def hidden_entry_message(root: str, entry: str, found: str) -> str:
    """ユーザがターミナルで打つ入口を、エージェントが打った形で止めた文。"""
    values = {"entry": entry, "found": found}
    if found == STDIN_SHELL:
        return texts.say(root, "DENY_TICKET_APPROVAL_CLI__HIDDEN_STDIN", {"entry": entry})
    if found in _FEEDERS or found.startswith("< "):
        return texts.say(root, "DENY_TICKET_APPROVAL_CLI__HIDDEN_FEED", values)
    kind = "review" if entry == _ENTRY_REVIEW else "exe" if entry == "ccnavi" else "other"
    return texts.say(
        root,
        "DENY_TICKET_APPROVAL_CLI__HIDDEN_ENTRY",
        {"entry": entry, "found": found},
        {"what": kind, "next": kind},
    )


# 記録と state を消す `ccnavi --prune`（`--preview` の無い形）。
# チケット制御とターミナル要求を切る設定に
# 依らず、組み込みの deny（`DENY_RECORDS_PRUNE`）で止める。実行ファイルの側の
# ターミナル要求は、擬似ターミナル（`script -qc '…' /dev/null`）でも、
# チケット制御を切ったワークスペースで
# ターミナル要求を切る変数を前に並べても抜けられる。しきい値の環境変数を 0 に近づけて並べれば、
# 自分の呼び出しの記録を消せる。
#
# 見るのは human_path_form と同じく、引用符と `\` を除いたコマンド行の生の文字列。実行役の
# コマンド越し（`script -qc`・`bash -c`・`env`）でも同じ文字列が残る。コマンドの切れ目
# （`;` `&` `|` 改行）の中に ccnavi の名前と、単独の語の `--prune` が並べば止める。
# `git fetch --prune` や `ccnavi-git.sh fetch --prune` は ccnavi の名前（`ccnavi` と
# `ccnavi.exe` と実行ファイルの名前が、語の終わりで閉じた形）を持たないのでヒットしない。
#
# 免除は `--preview` が `--prune` の隣に単独の語として並んだ形だけ。間に引用符があれば
# 免除しない。免除をコマンドの中のどこかの `--preview` にすると、
# `bash -c "ccnavi --prune" x --preview` のように、実行役の引数に置いた `--preview` で免除が
# 成立する。
_PRUNE_FLAG = user_only.names("prune")[0]
_PRUNE_WORD = re.compile(rf"(?<!\S){re.escape(_PRUNE_FLAG)}(?!\S)")
_PREVIEW_AFTER = re.compile(r"[ \t]+--preview(?![^\s;&|])")
_PREVIEW_BEFORE = re.compile(r"(?<!\S)--preview[ \t]+$")
_PRUNE_SEGMENT = re.compile(r"[^;&|\n]+")
CODE_RECORDS_PRUNE = "DENY_RECORDS_PRUNE"
RECORDS_PRUNE_RULE_ID = "builtin:guard-records-prune"


def _unquoted_marks(subject: str) -> tuple[str, set[int]]:
    """引用符とバックスラッシュを除いた文字列と、除いた場所（除いた後の位置）の組み合わせ。"""
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
    if _PRUNE_FLAG not in text or _only_readers(text):
        return ""
    names = _prune_names(bin_path)
    for segment in _PRUNE_SEGMENT.finditer(text):
        name = names.search(segment.group(0))
        if not name:
            continue
        for word in _PRUNE_WORD.finditer(text, segment.start() + name.end(), segment.end()):
            if not _previewed(text, cuts, word.start(), word.end()):
                return _PRUNE_FLAG
    return ""


# `ccnavi user prune`。入口の位置の規則は他の `user` の操作と同じで、止める設定だけが違う。
_USER_PRUNE = re.compile(user_only.user_verb_forms("prune"), re.IGNORECASE)


def user_prune_form(subject: str, bin_path: str = "") -> str:
    """`ccnavi user prune` があれば、見つけた文字列。無ければ空。"""
    text, _ = _unquoted_marks(subject)
    if "prune" not in text.casefold() or _only_readers(text):
        return ""
    names = _prune_names(bin_path)
    for segment in _PRUNE_SEGMENT.finditer(text):
        name = names.search(segment.group(0))
        if not name:
            continue
        found = _USER_PRUNE.match(segment.group(0), name.end())
        if found:
            return found.group(0).strip()
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


def prune_message(root: str) -> str:
    """`--prune` で止めた文。"""
    return texts.say(root, "DENY_RECORDS_PRUNE", {})


# Python から実行ファイルの中身を走らせる表記（ticket_approval_rule の launcher）。
# - 実行役: `uv run`・`poetry run`・`pipenv run`。間に置いた選択肢と値は読み分けない
# - インタプリタ: `python`・`python3.12`・`py`（Windows の起動役）と `.exe`
# - インタプリタの選択肢: `-u`・`-I`・`-OO`・`-3`・`-W x`・`-Wx`・`-X utf8`・`-Xutf8`
# - 入口: `-m ccnavi`・`-mccnavi`・`-um ccnavi`・`-m ccnavi.__main__`・配布物の入口 `main.py`
# 拾いきれない表記（`python -c`・名前を変えたコピー）は、human_path_form と hidden_user_entry が
# 名前に依らない検査で見る。
_PY_RUNNER = r"(?:\S*[\\/])?(?:uv|poetry|pipenv)(?:\.exe)?\s+run\b(?:\s+[^\s\x00;&|]+)*?"
_PY_INTERPRETER = r"(?:\S*[\\/])?(?:py|python[\w.]*)(?:\.exe)?"
_PY_OPTIONS = r"(?:\s+(?:-[WX]\s*[^\s\x00;&|-]\S*|-[A-Za-z0-9.]+))*?"
_PY_END = r"(?=[\s\x00;&|]|$)"
_PY_ENTRY = (
    rf"(?:\s+-[A-Za-z]*m\s*ccnavi(?:\.__main__)?{_PY_END}"
    rf"|\s+(?:\S*[\\/])?main\.py{_PY_END})"
)

# 承認の sh・放棄した親の記録を消す入口（worktree retire --abandon）・ユーザの判断の入口の sh を、
# コマンドの位置の表記で止める節。
# ticket_approval_rule が使い、hidden_user_entry は同じ形をこの節に任せる（文面を 1 つにするため）。
# `sh -x ...` のようにシェルに選択肢を付けた形も同じに見る。
# 対象のスクリプトは表（user_only.SCRIPTS）にある。
_SCRIPT_FORMS = user_only.script_forms()


def ticket_approval_rule(bin_path: str, root: str) -> rules.Rule:
    """ccnavi の実行ファイルをユーザの判断の経路に使う形を止めるルール。

    承認のスクリプト（`ccnavi-user-agree.sh`）も同じ形で止める。中身は `--agree` の
    呼び出しと承認済みチケットの push で、打つのはターミナルに座っているユーザ。
    実行ファイルの側は標準入力がターミナルであることを求めるので、hook から呼んでも通らないが、
    文字列で止めておけば「なぜ通らないのか」がヒットしたルールの id で分かる。

    承認の push（`ccnavi-user-push-approved.sh`）も止める。コミットして push することは
    合意そのものではないが、push は外へ出す操作で、その時機を決めるのはユーザ。

    放棄した親の片付けの記録を消す `ccnavi worktree retire --abandon <親>` も止める
    （`worktree retire` の形が止める）。消すと、`ccnavi-sync.sh` が知らせていた片付け待ちの親が
    見えなくなるので、打つのはユーザ。

    ボードの経路の形（`--yes` の組み合わせ、sh の `--choices` と `--digest`）と、
    ターミナル要求を切る形は、
    ここではなく `human_path_form` が止める。実行ファイルのパスに頼らず見るため。
    """
    names = [r"ccnavi(\.exe)?"]
    clause = selfguard_shell.binary_clause(bin_path)
    if clause:
        names.append(clause)
    # 実行ファイルを直に呼ぶ形と、起動の sh（`sh .ccnavi/scripts/ccnavi-launcher.sh …`）。
    names.append(r"ccnavi-launcher\.sh")
    launcher = (
        rf"({_PY_RUNNER}{_PY_OPTIONS}{_PY_ENTRY}"
        rf"|(?:{_PY_RUNNER}\s+)?{_PY_INTERPRETER}{_PY_OPTIONS}{_PY_ENTRY}"
        rf"|(?:{_PY_RUNNER}\s+)?((sh|bash)(\s+-\S+)*\s+)?(\S*[\\/])?(" + "|".join(names) + "))"
    )
    script = _SCRIPT_FORMS
    # 大文字小文字を区別しない。Windows と macOS の既定のファイルシステムは名前の大小を
    # 区別しないので、`SH .ccnavi/scripts/CCNAVI-AGREE.sh` や `CCNAVI.EXE --agree` でも
    # 同じものが走る。区別すると表記を変えるだけで外せる。引数の形（_CLI_FORMS）まで
    # 広がるが、実行ファイルの引数は大小を区別するので、広がるのは止める側だけ
    # （`--PREVIEW` で免除の形になっても、実行ファイルがその引数を受け付けない）。
    # `ccnavi user <操作>` は、ユーザ専用の入口。`prune` は別の deny（prune_form）が止める。
    user_entry = user_only.user_entry_forms(excluding="prune")
    expression = (
        rf"(?i)(^|\x00|[;&|]\s*)(&\s*)?{launcher}\s+[^\x00]*{_CLI_FORMS}"
        rf"|(^|\x00|[;&|]\s*)(&\s*)?{launcher}{user_entry}"
        rf"|{script}"
    )
    rule = rules.Rule(
        id=TICKET_APPROVAL_RULE_ID,
        match="|".join(toolnames.SHELL_TOOLS),
        regex=expression,
        root=root,
        **texts.spec("RULE_GUARD_TICKET_APPROVAL"),
        decision=rules.DENY,
    )
    rule.compiled = re.compile(expression)
    return rule

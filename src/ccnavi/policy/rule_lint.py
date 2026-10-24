"""ルールファイルの中身の検査。`--lint` と、保護対象の下書きの取り込み（`user handoff`）が呼ぶ。

組み上がらないルール、止めないモード、どのツールにもヒットしない match、広すぎる allow などを
名指しする。レイヤーのルールファイルも同じ読みで見る。提案（`suggest`）も候補をここに通す。
入口は `file_problems`（ファイルのパスを受ける）。
"""

from __future__ import annotations

import os
import re

from ..infra import hookio
from . import ctxfile, rules, subjects
from .rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

# `.ccnavi/scripts/` のヒット。直前の `./` と `../` の連なりは、相対の書き方なので手前にたどる。
_SCRIPTS = re.compile(r"(?:\.{1,2}/)*\.ccnavi/scripts/")
# その手前の 1 文字がこれなら、ヒットはパスの途中（絶対パス・`$VAR/`・`{root}/`・`~/`・
# ディレクトリ名の続き）。空白・引用符・括弧・`=`・`:` や語頭は、パスの始まりとして相対に数える。
_PATH_MID = frozenset("/\\}~$._-")


def _has_relative_scripts(text: str) -> bool:
    """文面に、cwd に左右される相対パスの `.ccnavi/scripts/` があるか。

    ヒットの直前（`./` と `../` の連なりを除いた位置）の 1 文字を見る。`/` `\\` `}` `~` `$`
    `.` 英数字 `_` `-` ならパスの途中（絶対パス、`{root}/`、`$VAR/`、`~/` など）なので言わない。
    語頭や、空白・引用符・括弧・`=`・`:` の直後なら相対パスの始まりとして言う。
    """
    for hit in _SCRIPTS.finditer(text):
        before = text[hit.start() - 1] if hit.start() > 0 else ""
        if before and (before.isascii() and before.isalnum() or before in _PATH_MID):
            continue
        return True
    return False


def file_problems(
    path: str, root: str, home: str = "", layer: bool = False, project: bool = False
) -> list[Problem]:
    """ルールファイルを、判定が読むのと同じ読み方で読んで検証する。

    rules.load をそのまま呼ぶ。別の読み方をすると、検証は通ったのに実運用で
    失敗するという、検証があるぶんかえって危ない形になる。

    `home` は、ルールが指すファイル（additionalContextFile）を探す起点。レイヤーの
    ルールならそのレイヤーの git プロジェクトルート。省けばワークスペースルート、
    それも無ければルールファイルの隣。

    `layer` は共通レイヤーより後ろのレイヤーかどうか。
    レイヤーは共通レイヤーに足すものなので、`deny` や
    `allow` が空でも穴にはならない（空のレイヤー = 何も足さない）。共通レイヤーで確かめている
    「空のガードは入っていないのと同じ」の問いを、レイヤーにまで広げると、allow を
    1 件だけ足したレイヤーが毎回 error を出し続けることになる。

    `project` はプロジェクトのレイヤーかどうか。ターンの終わりのルール（`match: Stop`）は
    プロジェクトのレイヤーから読まない（共通レイヤーと自身のレイヤーだけ）ので、そこに書いたものを言う。

    処理フロー:
    1. `rules.load` で読む。読めなければ、その 1 件だけを error で返して終わる
    2. ファイル全体の問い（`layer` でないときだけ）: `deny` が空なら error、`allow` が空なら warn
    3. ルールを順に見る。id の重複を warn にし、`_rule_problems` でルール 1 件ぶんを検める
    """
    home = home or root or os.path.dirname(os.path.abspath(path))
    try:
        rule_set, problems = rules.load(path, root)
    except (OSError, ValueError) as exc:
        # block モードではこれがそのまま全ツール呼び出しの拒否になり、
        # このファイルを直すための呼び出しも止まる。いちばん重い error。
        return [Problem(SEVERITY_ERROR, "(rules)", f"ルールを読めない: {exc}")]

    problems = list(problems)

    # ファイル全体の問い。レイヤーは共通レイヤーに足すものなので、空でも穴にはならない。
    if not rule_set.deny and not layer:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "(rules)",
                "`deny` が空。何も止めないガードは、入れていないのと"
                "同じなのに、入っているように見える",
            )
        )

    if not rule_set.allow and not layer:
        # allow が 1 件も無いと、どの呼び出しも ccnavi の判定を受けずに
        # 権限モードへ渡る。判定は動いているので error ではないが、外から見ると
        # ガードが何も言わない状態と区別が付かない。
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(rules)",
                "`allow` が空。どのルールも言及しない呼び出しは、"
                "すべて Claude Code の権限モードに従うことになる",
            )
        )

    # ルールごとの検査。id の重複もここで見る。
    seen: set[str] = set()
    for rule in rule_set.all():
        name = rule.id
        if rule.id in seen:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    "id が重複している。どちらのルールがヒットしたかを記録から辿れない",
                )
            )
        seen.add(rule.id)
        problems.extend(_rule_problems(rule, name, home, project))

    return problems


def _rule_problems(rule: rules.Rule, name: str, home: str, project: bool = False) -> list[Problem]:
    """ルール 1 件の中身。ヒットしない match、届かない message、指すファイル、広い allow。

    処理フロー:
    1. `_inert` で、match のツールのうち対象を取り出せないものを warn にする
    2. `_stop_problems` で `match: Stop` の書き方を見る
    3. `message` を `deny` 以外に書いていれば error（届かない欄）
    4. `message` と `additionalContext` と `additionalContextOnce` の文面を、`_has_relative_scripts`
       と `rules.SCRIPT_PLACEHOLDER` で見る（相対の sh、`{script:<名前>}` の名前と有無）
    5. `additionalContextFile` と `additionalContextOnceFile` の場所を `ctxfile` で見る
       （ワークスペースの外、無い、上限超え）
    6. `_every_problems` `_except_problems` `_when_problems` `_catch_all_deny_problems` に委ねる
    7. 刻みの無い allow（`every <= 1`）に文脈があれば、`_broad` で広さの理由を言う（warn）
    """
    problems: list[Problem] = []
    # match のツールのうち、判定が対象を取り出せないもの。
    for tool in _inert(rule.match):
        problems.append(
            Problem(
                SEVERITY_WARN, name, f"match の {tool} には適用する対象がありません。何も止まらない"
            )
        )
    problems.extend(_stop_problems(rule, name, project))

    if rule.message and rule.decision != rules.DENY:
        # ask の文面はユーザの確認ダイアログにしか出ず、allow の文面はどこにも出ない。
        # 書いたユーザは「モデルに届く」と思って書くので、届かない欄を残さない。
        # ルールは働いているので判定は変わらない。直すまで CI が失敗するだけ。
        where = (
            "ユーザの確認ダイアログにしか出ない"
            if rule.decision == rules.ASK
            else "どこにも届かない"
        )
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                f"{rule.decision} に message がある。{where}ので、モデルに渡す文は "
                "additionalContext に書き、message は削除してください",
            )
        )

    # 文面の sh は `{root}` から書く。相対の `.ccnavi/scripts/...` は、cwd がプロジェクトの中
    # だと見つからない（.ccnavi/docs/projects.md「ルールの文面にshを書くとき」）。
    # 絶対パスは cwd に左右されないので言わない。
    # 判定は変わらず、案内を受けたモデルの実行が失敗するだけなので warn。
    # 見る欄は、モデルに渡る文面の 3 つ。`...File` は rules.yml の外のファイルを指すので見ない。
    for field_name, text in (
        ("message", rule.message),
        ("additionalContext", rule.additional_context),
        ("additionalContextOnce", rule.additional_context_once),
    ):
        if _has_relative_scripts(text):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"{field_name} の `.ccnavi/scripts/` に `{{root}}` が付いていない。cwd が"
                    "プロジェクトの中だと相対パスの sh が見つからない。"
                    "`{root}/.ccnavi/scripts/...` と書いてください",
                )
            )

    # `{script:<名前>}` の差し込み。形が不正なら置き換わらず、スクリプトが無ければ置き換わっても
    # 実行できない。どちらも案内を受けたモデルの実行が失敗するだけなので warn。
    for field_name, text in (
        ("message", rule.message),
        ("additionalContext", rule.additional_context),
        ("additionalContextOnce", rule.additional_context_once),
    ):
        for hit in rules.SCRIPT_PLACEHOLDER.finditer(text):
            script = hit.group(1)
            if not rules.SCRIPT_NAME.fullmatch(script):
                why = (
                    f"`{hit.group(0)}` の名前が不正（使える字は英数字と `.` `_` `-`）。"
                    "置き換わらない"
                )
            elif not os.path.isfile(os.path.join(home, ".ccnavi", "scripts", script)):
                why = (
                    f"`{hit.group(0)}` の `.ccnavi/scripts/{script}` が無い。"
                    "置き換わっても実行できない"
                )
            else:
                continue
            problems.append(Problem(SEVERITY_WARN, name, f"{field_name} の {why}"))

    # ルールが指すファイルは、ルートの中を指していて、いま在って、上限に収まるか。
    # 無いのは warn。作るまで何も足さないだけで、判定は変わらない。
    for key, rel in (
        ("additionalContextFile", rule.additional_context_file),
        ("additionalContextOnceFile", rule.additional_context_once_file),
    ):
        if not rel:
            continue
        why = ctxfile.bad_path(rel)
        if why:
            problems.append(Problem(SEVERITY_ERROR, name, f"{key} の {rel}: {why}"))
            continue
        full = ctxfile.locate([home], rel)
        if not full:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"{key} の {rel} がありません。"
                    "ワークツリーにもルートにも無ければ何も追加しません。",
                )
            )
        elif ctxfile.over_limit(full):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"{key} の {rel} は {ctxfile.MAX_CHARS} 文字を超える。"
                    "先頭だけを渡し、切り詰めたことを末尾に追記します。",
                )
            )

    problems.extend(_every_problems(rule, name))
    problems.extend(_except_problems(rule, name))
    problems.extend(_when_problems(rule, name))
    problems.extend(_catch_all_deny_problems(rule, name))

    # once の文は文脈ごとに 1 度しか積まれず、every > 1 は刻んだ回にしか積まれないので、
    # どちらも広さの報告から外す。読めない every は 1（毎回渡る）になっているので、この式は
    # 読めない every も拾う（rules.readable_every）。
    if (
        (rule.additional_context or rule.additional_context_file)
        and rule.decision == rules.ALLOW
        and rule.every <= 1
    ):
        why = _broad(rule)
        if why:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"広い allow に additionalContext がある（{why}）。"
                    "ヒットするたびに同じ文がコンテキストに積まれる。"
                    "狭いルールに分けて書いてください",
                )
            )
    return problems


def _stop_problems(rule: rules.Rule, name: str, project: bool = False) -> list[Problem]:
    """`match: Stop` のルール（ターンの終わり N 回に 1 度止めて文を渡す）の書き方。

    見るのは 4 つ。どれも判定は変えないので warn。

    1. `deny` / `ask` に置いた。Stop で見るのは allow だけなので、`Stop` の部分は何も起きない
       （`Bash|Stop` なら `Bash` の部分はふつうに働く）
    2. プロジェクトのレイヤーに置いた。ターンの終わりには共通レイヤーと自身のレイヤーしか読まない
    3. 適用する先の `(stop)` にヒットしない glob / regex。何も起きない
    4. `every` が 2 より小さい。実行時にも使わない（ターンの終わりのたびに止まらないように）
    """
    wants = [want.strip() for want in rule.match.split("|") if want.strip()]
    if rules.STOP_MATCH not in wants:
        return []
    others = [w for w in wants if w != rules.STOP_MATCH]
    if rule.decision != rules.ALLOW:
        rest = f"（{'|'.join(others)} の部分はふつうに判定される）" if others else ""
        return [
            Problem(
                SEVERITY_WARN,
                name,
                f"{rule.decision} の match に {rules.STOP_MATCH} がある。ターンの終わりに見るのは "
                f"allow のルールだけなので、{rules.STOP_MATCH} の部分は何も起きない{rest}。"
                f"{rules.STOP_MATCH} は allow の別のルールに分けて置いてください",
            )
        ]
    if project:
        return [
            Problem(
                SEVERITY_WARN,
                name,
                f"プロジェクトのレイヤーの {rules.STOP_MATCH} のルールは使われない。"
                "ターンの終わりには共通レイヤーと自身のレイヤーしか読まない"
                "（外のリポジトリの 1 行でメインのターンを止めさせないため）",
            )
        ]
    problems: list[Problem] = []
    if rule.built() and not rule.hits(rules.STOP_SUBJECT):
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{rules.STOP_MATCH} のルールは呼び出しの文字列ではなく、"
                f"固定の {rules.STOP_SUBJECT} に適用する。"
                "この glob / regex / when はそれにヒットしないので何も起きない。"
                'glob: "*" と書いてください',
            )
        )
    if rule.every < 2:
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{rules.STOP_MATCH} のルールに every がありません（1 として扱う）ので使われない。"
                "使えばターンが終わるたびに止めることになるので、"
                "every: 10 のように間隔を空けてください",
            )
        )
    return problems


# 適用する文字列にコマンド列の全体を使うツール。except はこの全体に適用するので、
# コマンドのどこかに除く語を足すだけでルールが外れる。
_COMMAND_TOOLS = ("Bash", "PowerShell")

# 「何にでもヒットする」を見る文字列。空の文字列と、パスにもコマンドにも出ない制御文字の 1 字。
# どちらにもヒットする式は、適用する語を持たずに先頭でヒットしている。
_ANYTHING_PROBES = ("", "\x02")

# 「空でない文字列のどれにでもヒットする」を見る文字列。共通の文字を持たない 3 つで、
# パスにもコマンドにも出ない制御文字、記号 1 字、英字 1 字。判定は空の対象を見ないので、
# deny の本体はこれで見る（`.` も拾う）。英字を入れるのは、`[^\w./ -]` のような
# 記号だけにヒットする式を「何にでもヒットする」と言わないため。
# except と when の個別条件は空の文字列も見る _ANYTHING_PROBES のまま。
_NONEMPTY_PROBES = ("\x02", "~", "a")


def _except_problems(rule: rules.Rule, name: str) -> list[Problem]:
    """`except:` の書き方。

    書式の誤りは読み込み（rules._excepts）が error にするので、ここは意味の側を見る。

    見るのは 2 つで、どちらも判定は書いたとおりに動いているので warn。

    1. 何にでもヒットする except。ルールが何にもヒットしなくなり、書いてあるのに何も止めない
    2. Bash / PowerShell を見る deny / ask の except。適用するのはコマンド列の全体なので、
       `git push; echo wip/handoff/` のように除く語を足すだけで外れる
    """
    if not rule.excepts:
        return []
    problems: list[Problem] = []
    for i, exclusion in enumerate(rule.excepts):
        compiled = exclusion.compiled
        if compiled is not None and all(compiled.search(p) for p in _ANYTHING_PROBES):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"except[{i}] は何にでもヒットする。このルールはどの呼び出しにもヒットしない",
                )
            )
    tools = [want.strip() for want in rule.match.split("|")]
    shells = [t for t in _COMMAND_TOOLS if t in tools]
    if shells and rule.decision in (rules.DENY, rules.ASK):
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{'|'.join(shells)} の except はコマンド列の全体にヒットする。コマンドのどこかに"
                "除く語を追加するだけでこのルールが外れるので、except は語の位置まで狭く書くか、"
                "ルールを分けてください",
            )
        )
    return problems


# 組み合わせ条件のノードを書き方だけから見積もった値。
# 個別条件は「何にでもヒットする」と言えるときだけ ALWAYS で、
# それ以外は分からない（UNKNOWN）。何にもヒットしない個別条件は書き方からは言えないので作らない。
_ALWAYS, _NEVER, _UNKNOWN = "always", "never", "unknown"


def _constant(node: rules.Condition) -> str:
    """そのノードが、どの文字列にもヒットする（ALWAYS）・どれにもヒットしない（NEVER）と言えるか。"""
    if node.is_leaf():
        compiled = node.compiled
        if compiled is not None and all(compiled.search(p) for p in _ANYTHING_PROBES):
            return _ALWAYS
        return _UNKNOWN
    values = [_constant(c) for c in node.children]
    if node.kind == rules.WHEN_NOT:
        return {_ALWAYS: _NEVER, _NEVER: _ALWAYS}.get(values[0], _UNKNOWN)
    if node.kind == rules.WHEN_ALL:
        if _NEVER in values:
            return _NEVER
        return _ALWAYS if all(v == _ALWAYS for v in values) else _UNKNOWN
    if _ALWAYS in values:
        return _ALWAYS
    return _NEVER if all(v == _NEVER for v in values) else _UNKNOWN


def _catch_all_deny_problems(rule: rules.Rule, name: str) -> list[Problem]:
    """glob / regex 1 つの deny の本体が、何にでもヒットする。

    regex `''` や `.*` のような本体は、match のツールを丸ごと止める。狭く書いたつもりの式が
    適用する語を持たずにヒットしている形なので warn で言う。ツールを丸ごと止めると決めた書き方は
    `glob: "*"` ちょうどで書く（no-powershell のように）。それは言わない。

    判定は空の対象を見ない（Rule.matches）ので、`.` のように空でない文字列のどれにでもヒットする
    本体も同じ（_NONEMPTY_PROBES）。except を持つ deny は、本体が何にでもヒットしても except で
    除いたものは止めないので、「丸ごと止める」とは言わず、除いたもの以外を止めると言う。

    組み合わせ条件（when）の全体が何にでもヒットする形は _when_problems が言う。
    対象は deny で、ただし `{!root}` を組み立てられずに止める側へ倒した deny は、
    読み込みが別に言う。allow の広さは _broad が言う。
    """
    if rule.decision != rules.DENY or rule.when is not None or rule.degraded_message:
        return []
    if rule.glob == "*" and not rule.regex:
        return []
    compiled = rule.compiled
    if compiled is None or not all(compiled.search(p) for p in _NONEMPTY_PROBES):
        return []
    kind = "glob" if rule.glob else "regex"
    if rule.excepts:
        what = "except で除いたもの以外は、match のツールの呼び出しをすべて止める"
    else:
        what = "match のツールを丸ごと止める"
    return [
        Problem(
            SEVERITY_WARN,
            name,
            f"本体の {kind} は何にでもヒットする。{what}。ツールごと止めるつもりなら "
            '`glob: "*"` と書き、そうでなければ適用する語を書いてください',
        )
    ]


def _when_problems(rule: rules.Rule, name: str) -> list[Problem]:
    """`when:`（組み合わせ条件）の書き方。

    書式の誤り（知らないキー・空のリスト・上限）は読み込み（rules._when）が error にするので、
    ここは意味の側を見る。どれも判定は書いたとおりに動いているので warn。

    1. 何にでもヒットする・何にもヒットしないノード。`not` の下が何にでもヒットする個別条件だけ、
       `any` の中に何にでもヒットする個別条件、のような形で、そのノードは書いても条件を絞らない。
       いちばん外側の 1 つだけを言う（中を言っても直す場所は同じ）。
       組み合わせ条件の全体なら、ルールがどの呼び出しにもヒットする・ヒットしないことになる
    2. Bash / PowerShell を見る deny / ask の `not`。適用するのはコマンド列の全体なので、
       除く語をコマンドのどこかに足すだけで外れる（except と同じ理由）
    """
    tree = rule.when
    if tree is None:
        return []
    problems: list[Problem] = []

    # 何にでもヒットする・何にもヒットしないノードを外側から見て言う。
    # 外側が中立（どちらとも言えない）のときだけ、中の子を見る。
    def look(node: rules.Condition) -> None:
        value = _constant(node)
        if value == _UNKNOWN:
            for c in node.children:
                look(c)
            return
        where = node.where if node.is_leaf() else f"{node.where} の {node.kind}"
        said = "何にでもヒットする" if value == _ALWAYS else "何にもヒットしない"
        whole = (
            "。このルールはどの呼び出しにもヒットする"
            if value == _ALWAYS
            else "。このルールはどの呼び出しにもヒットしない"
        )
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{where} は{said}{whole if node is tree else '。条件を絞っていない'}",
            )
        )

    look(tree)

    # 同じ個別条件が肯定と否定で並ぶ `all` / `any` を見る。
    def operands(node: rules.Condition) -> list[rules.Condition]:
        """同じ演算子の入れ子（`all` の中の `all`）を 1 つに開いた子。"""
        out: list[rules.Condition] = []
        for c in node.children:
            out.extend(operands(c) if c.kind == node.kind else [c])
        return out

    for node in tree.walk():
        if node.kind not in (rules.WHEN_ALL, rules.WHEN_ANY):
            continue
        items = operands(node)
        plain = {c.key() for c in items if c.kind != rules.WHEN_NOT}
        negated = {c.children[0].key() for c in items if c.kind == rules.WHEN_NOT}
        if not plain & negated:
            continue
        result = "何にもヒットしない" if node.kind == rules.WHEN_ALL else "何にでもヒットする"
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{node.where} の {node.kind} に、同じ個別条件が肯定と否定で並んでいる。"
                f"互いに打ち消し合うので、この {node.kind} は{result}",
            )
        )

    tools = [want.strip() for want in rule.match.split("|")]
    shells = [t for t in _COMMAND_TOOLS if t in tools]
    has_not = any(n.kind == rules.WHEN_NOT for n in tree.walk())
    if shells and has_not and rule.decision in (rules.DENY, rules.ASK):
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{'|'.join(shells)} の when の not はコマンド列の全体にヒットする。"
                "コマンドのどこかに"
                "除く語を追加するだけでこのルールが外れるので、not の下は語の位置まで狭く書くか、"
                "ルールを分けてください",
            )
        )
    return problems


def _every_problems(rule: rules.Rule, name: str) -> list[Problem]:
    """`every`（渡す回の刻み）の値と、刻んだ先に渡すものがあるか。

    読めない値は error。判定は 1（毎回渡す）として扱って通すので、何も言わないと、書いたユーザは
    刻んだつもりでいるのに、実際は毎回渡ることになる。`every: 1` は既定値を明示しただけなので
    何も言わない。
    """
    if rule.every_written is None:
        return []
    if not rules.readable_every(rule.every_written):
        return [
            Problem(
                SEVERITY_ERROR,
                name,
                f"every の {rule.every_written!r} は間隔として読めない。"
                "1 以上の整数で書いてください。このままだと間隔を空けず、毎回渡る",
            )
        ]
    # 刻むのは渡す回で、渡すものが無ければ刻んでも何も起きない。文が無くても
    # 本文（...File）は渡るので、4 つのどれか 1 つでもあれば報告しない。
    if not (
        rule.additional_context
        or rule.additional_context_file
        or rule.additional_context_once
        or rule.additional_context_once_file
    ):
        return [
            Problem(
                SEVERITY_WARN,
                name,
                "every があるのに渡すものが無いので、間隔を空けても何も渡らない。"
                "additionalContext（または additionalContextOnce・…File）を書くか、"
                "every を削除してください",
            )
        ]
    return []


def _broad(rule: rules.Rule) -> str:
    """allow のルールが広いと言える理由。無ければ空。

    additionalContext はヒットした回ごとにモデルへ渡るので、広い allow に書くと
    ls のたびに同じ文が積まれる。「広い」の判定は 2 つで、どちらも書き方から
    機械的に言えるものに限る。ヒットする頻度は記録を見ないと分からない。

    1. 何にでもヒットする。翻訳後の式が、適用する語を含まない文字列にもヒットする。
       組み合わせ条件（when）は、書き方から何にでもヒットすると言えるとき（_constant）
    2. 選択肢が 3 つ以上。`(ls|cat|sed)` のような書き方は、それだけ多くの
       コマンドに同じ文を添えることになる
    """
    if rule.when is not None and rule.compiled is None:
        # 組み合わせ条件は 1 つの文字列で試すと、`not: {regex: '^rm'}` のように "x" にはヒットするが
        # 何にでもヒットするわけではない形を取り違える。書き方から見積もる（_constant）。
        if _constant(rule.when) == _ALWAYS:
            return "何にでもヒットする"
    elif rule.built() and rule.body_hits("x"):
        return "何にでもヒットする"
    if _alternatives(rule.regex) >= 3:
        return "選択肢が 3 つ以上"
    return ""


def _alternatives(regex: str) -> int:
    """正規表現の選択肢の数。文字クラスの中と、エスケープされた `|` は数えない。"""
    if not regex:
        return 0
    count, in_class, escaped = 1, False, False
    for ch in regex:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "|":
            count += 1
    return count


def _inert(match: str) -> list[str]:
    """match に並んだツール名のうち、判定が対象を取り出せないものを返す。

    ルールを適用する文字列を選ぶのは subjects.subject_of で、そこが知らないツール名では
    対象が空になり、rule.matches は必ず False を返す。つまりそのルールは
    書いてあるのに何も止めない。守っているつもりの穴なので warn で言う。

    ツール名の一覧を持たずに subject_of を実際に呼んで確かめている。一覧をコピーすると、
    判定側が扱うツールを増やしたときにこちらが気づかないうちに古くなり、正しいルールを
    誤って報告するようになる。差し込む欄の名前だけは subjects の表から借りる。
    """
    # 対象を持つツールなら何かしら返る値を入れておく。返るかどうかだけを見る。
    probe_input = {field: "x" for field in subjects.SUBJECT_FIELDS.values()}
    inert: list[str] = []
    for want in match.split("|"):
        tool = want.strip()
        if not tool or tool == rules.STOP_MATCH:
            # ターンの終わりに適用する名前。適用する先は events.stop_rules_nudge が持つ。
            continue
        probe = hookio.Input(tool_name=tool, tool_input=probe_input)
        if not subjects.subject_of(probe):
            inert.append(tool)
    return inert

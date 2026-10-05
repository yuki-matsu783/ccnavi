"""`--lint` のうち、ルールファイルの中身の検査。

組み上がらないルール、止めないモード、どのツールにも当たらない match、広すぎる allow などを
名指しする。レイヤーのルールファイルも同じ読みで見る（`lint_layers`）。提案（`suggest`）も候補を
ここに通す。
"""

from __future__ import annotations

import os
import re

from ..hook import judge
from ..infra import hookio
from ..policy import ctxfile, rules
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

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


def _rules(
    path: str, root: str, home: str = "", layer: bool = False, project: bool = False
) -> list[Problem]:
    """ルールファイルを、判定が読むのと同じ読み方で読んで検証する。

    rules.load をそのまま呼ぶ。別の読み方をすると、検証は通ったのに実運用で
    落ちるという、検証があるぶんかえって危ない形になる。

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
    """
    home = home or root or os.path.dirname(os.path.abspath(path))
    try:
        rule_set, problems = rules.load(path, root)
    except (OSError, ValueError) as exc:
        # block モードではこれがそのまま全ツール呼び出しの拒否になり、
        # このファイルを直すための呼び出しも止まる。いちばん重い error。
        return [Problem(SEVERITY_ERROR, "(rules)", f"ルールを読めない: {exc}")]

    problems = list(problems)

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

    seen: set[str] = set()
    for rule in rule_set.all():
        # id を欠いたルールは名指しできないので、当たった中身で呼ぶ。
        name = rule.id or f"(id 無し: {rule.decision} {rule.match} {rule.glob or rule.regex})"
        if not rule.id:
            problems.append(
                Problem(SEVERITY_WARN, name, "id が無い。記録や報告でこのルールを名指しできない")
            )
        elif rule.id in seen:
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
    """ルール 1 件の中身。当たらない match、届かない message、指すファイル、広い allow。"""
    problems: list[Problem] = []
    for tool in _inert(rule.match):
        problems.append(
            Problem(SEVERITY_WARN, name, f"match の {tool} には当てる対象が無い。何も止まらない")
        )
    problems.extend(_stop_problems(rule, name, project))

    if rule.message and rule.decision != rules.DENY:
        # ask の文面はユーザの確認ダイアログにしか出ず、allow の文面はどこにも出ない。
        # 書いたユーザは「モデルに届く」と思って書くので、届かない欄を残さない。
        # ルールは働いているので判定は変わらない。直すまで CI が落ちるだけ。
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
                "additionalContext に書き、message は消してください",
            )
        )

    # 文面の sh は `{root}` から書く。相対の `.ccnavi/scripts/...` は、cwd がプロジェクトの中
    # だと見つからない（docs/claude/projects.md「ルールの文面にshを書くとき」）。
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
                    f"{key} の {rel} が無い。ワークツリーにもルートにも無ければ何も足さない",
                )
            )
        elif ctxfile.over_limit(full):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"{key} の {rel} は {ctxfile.MAX_CHARS} 文字を超える。"
                    "先頭だけが渡り、切り詰めたことを末尾に書き足す",
                )
            )

    problems.extend(_every_problems(rule, name))

    # once の文は文脈ごとに 1 度しか積まれず、every > 1 は刻んだ回にしか積まれないので、
    # どちらも広さを報告しない。読めない every は 1（毎回渡る）になっているので、この式は
    # 読めない every を見逃さない（rules.readable_every）。
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
    3. 当てる先の `(stop)` に当たらない glob / regex。何も起きない
    4. `every` が 2 より小さい。実行時にも使わない（ターンの終わりのたびに止まらないように）
    """
    wants = [want.strip() for want in rule.match.split("|") if want.strip()]
    if rules.STOP_MATCH not in wants:
        return []
    others = [w for w in wants if w != rules.STOP_MATCH]
    if rule.decision != rules.ALLOW:
        rest = f"（{'|'.join(others)} の部分はふつうに効く）" if others else ""
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
    if rule.compiled is not None and not rule.compiled.search(rules.STOP_SUBJECT):
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{rules.STOP_MATCH} のルールは呼び出しの文字列ではなく、"
                f"固定の {rules.STOP_SUBJECT} に当てる。"
                "この glob / regex はそれに当たらないので何も起きない。"
                'glob: "*" と書いてください',
            )
        )
    if rule.every < 2:
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{rules.STOP_MATCH} のルールに every が無い（1 として扱う）ので使われない。"
                "使えばターンが終わるたびに止めることになるので、"
                "every: 10 のように間隔を空けてください",
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
                "every を消してください",
            )
        ]
    return []


def _broad(rule: rules.Rule) -> str:
    """allow のルールが広いと言える理由。無ければ空。

    additionalContext は当たった回ごとにモデルへ渡るので、広い allow に書くと
    ls のたびに同じ文が積まれる。「広い」の判定は 2 つで、どちらも書き方から
    機械的に言えるものに限る。当たる頻度は記録を見ないと分からないので、
    ここでは扱わない。

    1. 何にでも当たる。翻訳後の式が、当てる語を含まない文字列にも当たる
    2. 選択肢が 3 つ以上。`(ls|cat|sed)` のような書き方は、それだけ多くの
       コマンドに同じ文を添えることになる
    """
    if rule.compiled is not None and rule.compiled.search("x"):
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

    ルールを当てる文字列を選ぶのは judge.subject_of で、そこが知らないツール名では
    対象が空になり、rule.matches は必ず False を返す。つまりそのルールは
    書いてあるのに何も止めない。守っているつもりの穴なので warn で言う。

    ツール名の一覧を持たずに subject_of を実際に呼んで確かめている。一覧を書き写すと、
    判定側が扱うツールを増やしたときにこちらが気づかないうちに古くなり、正しいルールを
    誤って報告するようになる。差し込む欄の名前だけは judge の表から借りる。
    """
    # 対象を持つツールなら何かしら返る値を入れておく。返るかどうかだけを見る。
    probe_input = {field: "x" for field in judge.SUBJECT_FIELDS.values()}
    inert: list[str] = []
    for want in match.split("|"):
        tool = want.strip()
        if not tool or tool == rules.STOP_MATCH:
            # ターンの終わりに当てる名前。当てる先は judge ではなく events.stop_rules_nudge が持つ。
            continue
        probe = hookio.Input(tool_name=tool, tool_input=probe_input)
        if not judge.subject_of(probe):
            inert.append(tool)
    return inert

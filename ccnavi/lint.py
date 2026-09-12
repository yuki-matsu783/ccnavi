"""設定とルールの検証。判定を行わずに、防御を無効化しうる記述だけを報告する。

判定の経路は、苦情を言うために設定を読むわけではない。判定のついでに気づいたことを
標準エラーへ落としているだけなので、判定が走らない場面――ルールを書き換えた直後、
CI、入れたばかりのプロジェクト――で不備を見つける手段が無い。ここがその経路になる。

急ぐ理由は事故の側にある。ルールファイルが読めないと、block モードでは
すべてのツール呼び出しが拒否側に倒れる。そのファイルを直すための呼び出しも
止まるので、壊れてから気づいたのでは直せない。だから壊れる前に言う側が要る。

深刻度の分け方は 1 つの原則で決めてある。

* error はガードが働かない、あるいは働きすぎて全部を止める記述。放っておくと
  防御が消えるか、セッションが死ぬ。CI が落とす対象はここだけでよい
* warn は判定そのものは動くが、書いた人が意図した防御が効いていない記述。
  直さなくても今日は何も壊れないが、守っているつもりの穴が開いている

## 設計からの読み替え

ccnavi.md 付録 D.2「診断コマンド」の `--lint` と D.4「設定lintの検証項目」は、
config.yaml という 1 枚の設定ファイルに、ツールの許可・保護対象ディレクトリ・
禁止コマンドがまとめて書かれている前提で書かれている。現在の形はそうではない。
設定は `.claude/settings.json` の env が運ぶ環境変数、防御の中身はルールファイルで、
パスの列挙という考え方そのものが無い。そこで D.4 の 9 項目を次のように読み替えた。

| D.4 の項目 | 現在の形での読み替え | 深刻度 |
|---|---|---|
| 3 正規表現がコンパイル可能か | regex / pattern を組み立てられるか | error |
| 6 tools セクションが存在するか | ルールが 1 件でも組み上がるか | error |
| 1,2,4 禁止値・`..`・絶対パス | 該当する列挙が無い。代わりに版番号と必須欄 | error |
| 5 max_* が既定より緩くないか | 呼び出しを止めないモードと読めない値 | warn |
| 8 重複していないか | id の欠落と重複 | warn |
| 7,9 保護対象・immutable の漏れ | どのツールにも当たらない match | warn |

読み替えても変わらないのは、CI で走らせて error だけを落とす対象にするという
D.4 の使い方のほうで、終了コードはそれに合わせてある。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from typing import TextIO

from . import (
    approval,
    ctxfile,
    gitcmd,
    gitstate,
    hookio,
    judge,
    modes,
    phasetypes,
    review,
    risk,
    rules,
    selfguard,
    settings,
    tree,
)
from . import ticket as ticket_mod
from .modes import EXIT_ERROR, EXIT_OK
from .rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

# Claude Code の設定ファイル。ccnavi 自身はこのファイルを読まない。ここに書かれた
# env は Claude Code がプロセスに渡し、ccnavi はそれを環境変数として受け取る。
# 検証だけがこのファイルを直接見る。作業ツリーの中にあってエージェントが書き換えられる
# ファイルに、防御を無効化する値が書かれていないかを問えるのはここだけだから。
PROJECT_SETTINGS = os.path.join(".claude", "settings.json")

# `--lint --json` の形の版。欄を足すだけなら上げない。欄の意味や名前を変えたら上げ、
# 読む側（VS Code 拡張）は違う版を「読めない」として扱う。
LINT_VERSION = 1


def report(
    stdout: TextIO,
    root: str,
    conf: settings.Settings,
    notes: list[str],
    flag: str,
    restore_if_deny_flag: str = "",
    guard_core_files_flag: str = "",
    guard_ticket_approval: str = "",
    as_json: bool = False,
) -> int:
    """検証の結果を書き、error が 1 件でもあれば非ゼロを返す。

    as_json なら README「lint の JSON」の形で 1 つの JSON を書く。VS Code 拡張が
    プロジェクトごとの warn を拾うための形で、人向けの文面は書き換えてよいが、
    この JSON の形は拡張との契約になる。

    書き先は標準出力にしてある。この経路は hook の payload を読まないので、
    判定を運ぶための標準出力が空いている。CI がそのまま拾える側に出す。
    """
    # モードの解決は判定と同じ関数に任せる。ここで別に書くと、検証は通ったのに
    # 実運用では違うモードになる、という一番まずい形になる。苦情を標準エラーへ
    # 書く作りなので、受け皿を渡して拾い、こちらで深刻度を付け直す。
    complaints = io.StringIO()
    mode = modes.resolve_mode(complaints, flag, conf)

    # 自動復元も同じ理由で同じ関数に任せる。受け皿を分けるのは、苦情の出所を
    # 取り違えないため。どちらの設定について言われたのかが混ざると、
    # 直しに行く先が決まらない。
    said = io.StringIO()
    restore_if_deny = selfguard.resolve(
        said, restore_if_deny_flag, conf.restore_if_deny, settings.RESTORE_IF_DENY_ENV
    )
    guard_core_files = selfguard.resolve(
        said,
        guard_core_files_flag,
        conf.guard_core_files,
        settings.GUARD_CORE_FILES_ENV,
    )

    problems = check(root, conf, notes, mode, complaints.getvalue())
    problems += [
        Problem(SEVERITY_WARN, "(restore)", line.removeprefix("ccnavi: "))
        for line in said.getvalue().splitlines()
    ]
    # この門に dry-run は無い。書いた人は「止めずに報告する」つもりでいるのに、
    # 実際は enable と同じに止める。設定ファイルを読んだだけでは、その食い違いが
    # どこにも現れない。warn ではなく error にするのは、直すまで意味が変わらない
    # ――つまり、直さないと設定ファイルが嘘をつき続ける――ため。
    declared = (conf.guard_ticket_approval_declared or "").strip().lower()
    if declared and declared not in selfguard.GATE_SETTINGS:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "(ticket)",
                f"{settings.GUARD_TICKET_APPROVAL_ENV}={declared}。この門は "
                f"{' か '.join(selfguard.GATE_SETTINGS)} しか取らない"
                "（承認は通れば済んでしまうので、止めずに報告する段が無い）。"
                f"今は {selfguard.ENABLE} として動いている",
            )
        )
    # チケット制御も同じ 2 値。切ったつもりの綴り違いは enable として動いているので、
    # 書いた人が「切れている」と思い続けないよう error にする。
    declared = (conf.ticket_control_declared or "").strip().lower()
    if declared and declared not in selfguard.GATE_SETTINGS:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "(ticket)",
                f"{settings.TICKET_CONTROL_ENV}={declared}。"
                f"{' か '.join(selfguard.GATE_SETTINGS)} しか取らない。"
                f"今は {selfguard.ENABLE} として動いている",
            )
        )
    if conf.tickets_enabled and guard_ticket_approval == selfguard.DISABLE:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{settings.GUARD_TICKET_APPROVAL_ENV}=disable。"
                "エージェントが ccnavi の実行ファイルを直接打って、"
                "承認・レビュー済みの受け入れ・状態の移動を行える",
            )
        )

    errors = sum(1 for p in problems if p.severity == SEVERITY_ERROR)
    warns = len(problems) - errors
    if as_json:
        payload = {
            "version": LINT_VERSION,
            "root": root,
            "rules": conf.rules,
            "mode": mode,
            "ticket_control": conf.ticket_control or selfguard.ENABLE,
            "projects": [t.name for t in tree.projects(conf.projects)],
            "problems": [
                {"severity": p.severity, "where": p.rule, "detail": p.detail} for p in problems
            ],
            "errors": errors,
            "warns": warns,
        }
        stdout.write(json.dumps(payload, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_ERROR if errors else EXIT_OK

    stdout.write("ccnavi: 設定を検証する\n")
    stdout.write(f"  ルール: {conf.rules}\n")
    stdout.write(f"  deny の場所を戻す: {restore_if_deny}\n")
    stdout.write(f"  中核ファイルを守る: {guard_core_files}\n")
    stdout.write(f"  チケット制御: {conf.ticket_control or selfguard.ENABLE}\n")
    if conf.tickets_enabled:
        stdout.write(f"  チケットの承認の経路を守る: {guard_ticket_approval or selfguard.ENABLE}\n")
    # 環境変数はこの起動が受け取ったものであって、セッションが受け取るものではない。
    # 端末から叩いた検証と hook から届く環境は別物なので、どちらを見た結果なのかを
    # 名乗らせる。名乗らないと、通った検証が別の設定についての報告になる。
    stdout.write(f"  モード: {mode}（この起動の環境から解決したもの）\n")

    for problem in problems:
        stdout.write(f"{problem}\n")

    stdout.write(f"error {errors} 件、warn {warns} 件\n")
    return EXIT_ERROR if errors else EXIT_OK


def check(
    root: str, conf: settings.Settings, notes: list[str], mode: str, complaints: str
) -> list[Problem]:
    """防御を無効化しうる記述を数え上げる。

    notes は設定の解決が出した苦情、complaints はモードの解決が出した苦情。
    どちらも深刻度を持たない文字列で届くので、ここで付ける。
    """
    problems: list[Problem] = []

    # 上書き設定を読み飛ばしても、値は既定に落ちてガードは弱まらない。
    # ただし人が設定したつもりの値がどこにも効いていない状態にはなる。
    for note in notes:
        problems.append(Problem(SEVERITY_WARN, "(settings)", note))

    for line in complaints.splitlines():
        # 判定の側は "ccnavi: " を付けて標準エラーへ書く。ここでは深刻度が
        # 頭に付くので、その前置きは落とす。
        problems.append(Problem(SEVERITY_WARN, "(mode)", line.removeprefix("ccnavi: ")))

    if mode == modes.DISABLE:
        problems.append(Problem(SEVERITY_WARN, "(mode)", f"{modes.DISABLE} なので何も判定しない"))
    elif mode == modes.DRY_RUN:
        # warn は導入の途中では正しい状態なので error にはしない。それでも
        # 言う。ルールが揃っているのに 1 件も止まらない状態は、外から見ると
        # ガードが効いている状態と区別が付かない。
        problems.append(
            Problem(
                SEVERITY_WARN, "(mode)", f"{modes.DRY_RUN} なので判定しても呼び出しに手を出さない"
            )
        )

    problems.extend(_project_settings(root))
    problems.extend(_after(root))
    problems.extend(_rules(conf.rules, root))
    problems.extend(_phases(conf))
    problems.extend(_risk(conf))
    problems.extend(_ticket(conf, root))
    problems.extend(_projects(conf, root))
    return problems


def _risk(conf: settings.Settings) -> list[Problem]:
    """リスクの配点が読めるか。無いのは不備ではない（組み込みの配点）。"""
    if not conf.risk:
        return []
    _, notes = risk.load(conf.risk)
    return [Problem(p.severity, "(risk)", f"{p.rule}: {p.detail}") for p in notes]


def _phases(conf: settings.Settings) -> list[Problem]:
    """フェーズの種類の定義が読めるか。無いのは不備ではない（番号だけの挙動）。"""
    if not conf.phases:
        return []
    _, notes = phasetypes.load(conf.phases)
    return [Problem(p.severity, "(phases)", f"{p.rule}: {p.detail}") for p in notes]


def _phase_types(conf: settings.Settings):
    if not conf.phases:
        return None
    types, _ = phasetypes.load(conf.phases)
    return types


def _ticket(conf: settings.Settings, root: str = "") -> list[Problem]:
    """チケットと写しと作業ツリーが噛み合っているかを見る（REQ-TKT-25）。

    判定に効くのは写しの側だけなので、ここで問うのは「効いている範囲は何か」と
    「作業ツリーと提案がそれと一致しているか」。一致していない状態は壊れては
    いないが、書いた人は書いたとおりに効いていると思っている。
    検証はその思い違いを名指しする場所になる。
    """
    problems: list[Problem] = []
    # 退役した名前ごとに、代わりに何を書くのかを言う。まとめて 1 つの文面にすると、
    # 置き場の話しかしない案内が、置き場とは関係ない名前にも付く。
    replacements = {
        "CCNAVI_GUARD_CLI": f"{settings.GUARD_TICKET_APPROVAL_ENV} に改名した",
    }
    default = f"提案は {settings.TICKETS_ENV}、写しは {settings.APPROVED_ENV} で指す"
    for name in conf.retired:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{name} はもう効かない。{replacements.get(name, default)}",
            )
        )
    if conf.approved_blank:
        # 以前は空文字が「チケット制御を使わない」の宣言だった。今は置き場のパスで
        # しかなく、空は既定の置き場に戻る。切りたかった人には今の書き方を言う。
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{settings.APPROVED_ENV} が空文字。空で切ることはもうできず、"
                f"既定の置き場で動いている。切るなら {settings.TICKET_CONTROL_ENV}=disable",
            )
        )
    if not conf.tickets_enabled:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{settings.TICKET_CONTROL_ENV}=disable。チケットの範囲・ゲート・"
                "サブエージェントの制限は効かない",
            )
        )
        return problems
    root = root or os.getcwd()
    if not _any_copies(conf, root) and not tree_has_tickets(root, conf.tickets):
        # 写しも提案も無い状態は不備ではない。チケットによる制御は任意で、
        # 使っていないプロジェクトにここで苦情を返すと、その 1 行が常態になって
        # 他の報告ごと読まれなくなる。
        return problems

    problems.extend(_approved_guarded(conf, root))

    copies, notes = approval.scan(conf, root)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    closed, _ = approval.scan(conf, root, closed=True)
    index = approval.by_id(copies)
    done = {t.ticket for t in closed}

    proposals, complaints = ticket_mod.scan(root, conf.tickets, conf.projects)
    problems.extend(complaints)

    types = _phase_types(conf)
    for t in copies:
        if not t.is_child and t.has_plan and types is None:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    "(phases)",
                    f"{t.ticket} は計画を持つのにフェーズの種類の定義（{conf.phases}）が読めない",
                )
            )

    problems.extend(_proposal_problems(proposals, index, done, types))

    worktrees = tree.worktrees(root, conf.projects)
    problems.extend(_worktree_problems(root, conf, worktrees, index, copies))
    names = {t.name for t in worktrees}
    if any(not t.is_child for t in copies):
        problems.extend(_review_token(root))
    problems.extend(_ticket_hooks(root))
    for stray in _stray_claude_dirs(root, names):
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{stray} は作業ツリーでも main でもないのに .claude/ を持つ。"
                "cd 1 回で別のワークスペースルートに見える",
            )
        )
    return problems


def _any_copies(conf: settings.Settings, root: str) -> bool:
    """どこかのツリーに写しの置き場があるか。"""
    return any(
        os.path.isdir(settings.approved_dir(conf, t.root)) for t in approval.trees(conf, root)
    )


def _approved_guarded(conf: settings.Settings, root: str) -> list[Problem]:
    """写しの置き場が守られているか。

    守るのは組み込み（`builtin-guard-approved-tickets`）で、ルールファイルには
    書かせない。書かせると消せることになる。組み立てられるかだけをここで見る。
    綴りから当てる形を作れなければ、その 1 本は足されず、エージェントが写しを
    書けて承認の意味が無くなる。
    """
    if selfguard.approved_clause(conf.approved):
        return []
    return [
        Problem(
            SEVERITY_ERROR,
            "(ticket)",
            f"写しの置き場の綴り（{settings.APPROVED_ENV}={conf.approved}）から"
            "守るルールを組み立てられない。エージェントが写しを書けるので、承認の意味が無い",
        )
    ]


def _proposal_problems(proposals: list, index: dict, done: set[str], types) -> list[Problem]:
    """提案の側。未承認、承認で落ちるもの、先行が閉じていない doing、同じ識別子の重複。"""
    problems: list[Problem] = []
    # 提案と写しを合わせた池。親子の制約は、親が同じ束で提案されている形も含めて見る。
    pool = dict(index)
    for t in proposals:
        pool.setdefault(t.ticket, t)
    seen: dict[str, list[str]] = {}
    for t in proposals:
        seen.setdefault(t.ticket, []).append(f"{t.tree or '(main)'}:{t.state}")
        if t.ticket not in index and t.ticket not in done and t.state != ticket_mod.CANCELLED:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は未承認（{t.tree or '(main)'} の {t.state}/）。"
                    "'ccnavi --approve' を通すまで範囲は効かない",
                )
            )
        if t.state not in ticket_mod.CLOSED:
            # 承認で落ちるものを、承認の前に名指しする。人が端末で初めて知るより早く。
            for p in approval.validate(t, pool, types):
                problems.append(Problem(p.severity, "(ticket)", f"{t.ticket}: {p.detail}"))
        if t.state == ticket_mod.DOING:
            waiting = [p for p in t.predecessors if p not in done]
            if waiting:
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        "(ticket)",
                        f"{t.ticket} は先行 {', '.join(waiting)} が閉じていないのに doing/ にある",
                    )
                )
    for ticket_id, places in seen.items():
        if len(places) > 1:
            where = ", ".join(places)
            problems.append(
                Problem(SEVERITY_ERROR, "(ticket)", f"{ticket_id} が複数の場所にある: {where}")
            )
    return problems


def _worktree_problems(
    root: str, conf: settings.Settings, worktrees: list, index: dict, copies: list
) -> list[Problem]:
    """作業ツリーの側。チケットの無いツリー、迷い込んだ写し、切り元の食い違い、ツリーの無い写し。"""
    problems: list[Problem] = []
    for t in worktrees:
        if t.name not in index:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"作業ツリー {t.name} にチケットが無い。そこへの書き込みはルールだけで判定する",
                )
            )
        bound = index.get(t.name)
        if bound is not None:
            parent = index.get(bound.parent) if bound.is_child else None
            owner = parent.project if parent is not None else bound.project
            if owner != t.project:
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        "(ticket)",
                        f"作業ツリー {t.name} の切り元（{t.project or 'ワークスペース'}）が写しの "
                        f"project（{owner or 'ワークスペース'}）と違う。そこへの書き込みは止まる。"
                        "写しが指すリポジトリから切り直す",
                    )
                )
    names = {t.name for t in worktrees}
    for t in copies:
        if t.ticket not in names:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は承認済みだが作業ツリー "
                    f"{tree.worktree_path(root, t.ticket)} が無い。作るまで効かない",
                )
            )
    return problems


def _projects(conf: settings.Settings, root: str) -> list[Problem]:
    """プロジェクトの置き場が噛み合っているか（REQ-MLT-16）。

    置き場が無いのは不備ではない。あるなら、ワークスペースの git で無視されていること、
    各プロジェクトのルールが読めること、プロジェクトが `.claude/` を持たないことを見る。
    どれも error にしない。判定は動いていて、読めないプロジェクトは組み込みの既定に落ちる。
    """
    problems: list[Problem] = []
    found = tree.projects(conf.projects)
    if not found:
        return problems
    rel = os.path.relpath(conf.projects, root).replace(os.sep, "/")
    if _ignored(root, rel) is False:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(projects)",
                f"{rel}/ がワークスペースの git で無視されていない。プロジェクトは自分の git を"
                "持つので、ワークスペースの `.gitignore` に入れる",
            )
        )
    for p in found:
        where = f"(projects/{p.name})"
        path = settings.project_rules_path(conf, tree.project_root(conf.projects, p.name))
        if not os.path.isfile(path):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    where,
                    f"ルール {conf.project_rules} が無い。このプロジェクトへの書き込みは組み込みの"
                    "既定で判定し、Bash の合成からは外れる",
                )
            )
        else:
            for c in _rules(path, root, home=p.root):
                problems.append(Problem(c.severity, f"{where} {c.rule}".rstrip(), c.detail))
        if os.path.isdir(os.path.join(p.root, ".claude")):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    where,
                    ".claude/ を持つ。Claude Code がそこのスキルを読み、cd 1 回で別のルートに"
                    f"見える。プロジェクトの設定は {conf.project_rules} に置く",
                )
            )
    return problems


def _ignored(root: str, rel: str) -> bool | None:
    """このパスをワークスペースの git が無視しているか。git が無ければ None。"""
    done = gitcmd.run(root, ["check-ignore", "-q", rel], gitstate.TIMEOUT_SECONDS)
    if done.failure:
        return None
    if done.code == 0:
        return True
    return False if done.code == 1 else None


def _review_token(root: str) -> list[Problem]:
    """sh がリモートを読み書きできる形か。gh / glab か、curl とホストに合うトークン。"""
    rc, out = gitcmd.output(root, ["remote", "get-url", "origin"], 5)
    url = out.strip() if rc == 0 else ""
    if not url:
        return [Problem(SEVERITY_WARN, "(ticket)", "origin が無い。レビューの依頼と確認は動かない")]
    problem = review.transport_problem(url)
    if problem:
        return [Problem(SEVERITY_WARN, "(ticket)", f"{problem}。レビューの依頼と確認は動かない")]
    return []


TICKET_SCRIPTS = ("ccnavi-ticket.sh", "ccnavi-review.sh", "ccnavi-git.sh")


def _ticket_hooks(root: str) -> list[Problem]:
    """親子の運用に要る hook の登録とスクリプトが揃っているか。"""
    problems = []
    for event, what in (
        (hookio.SUBAGENT_START, "サブエージェントに開いている子の一覧を渡せない"),
        (hookio.SUBAGENT_STOP, "範囲外の変更を残したサブエージェントを差し戻せない"),
    ):
        if _registered(root, event) is False:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(project)",
                    f"{PROJECT_SETTINGS} の {event} に ccnavi が登録されていない。{what}",
                )
            )
    for name in TICKET_SCRIPTS:
        path = os.path.join(root, ".claude", "scripts", name)
        if not os.path.isfile(path):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(project)",
                    f".claude/scripts/{name} が無い。ゲートの中で通る形が無くなる",
                )
            )
    return problems


def tree_has_tickets(root: str, tickets_rel: str) -> bool:
    """main か作業ツリーのどこかに提案の置き場があるか。"""
    for t in [tree.main_tree(root), *tree.worktrees(root)]:
        if os.path.isdir(os.path.join(t.root, tickets_rel.replace("/", os.sep))):
            return True
    return False


def _stray_claude_dirs(root: str, worktree_names: set[str]) -> list[str]:
    """リポジトリの中で `.claude/` を持つ、作業ツリーでも main でもないディレクトリ。

    浅くしか見ない。2 段まで。深く歩くと大きなリポジトリで検証が待たされる。
    """
    found = []
    skip = {".git", ".claude", "node_modules", ".venv", "dist", "build"}
    try:
        first = sorted(os.listdir(root))
    except OSError:
        return found
    for name in first:
        top = os.path.join(root, name)
        if name in skip or not os.path.isdir(top):
            continue
        candidates = [top]
        with contextlib.suppress(OSError):
            candidates += [os.path.join(top, n) for n in sorted(os.listdir(top))]
        for candidate in candidates:
            if os.path.isdir(os.path.join(candidate, ".claude")):
                found.append(os.path.relpath(candidate, root))
    return found


def _after(root: str) -> list[Problem]:
    """実行後の監視が実際に動く形になっているかを見る。

    どちらも error にしない。実行前の判定は動いているので、防御が消えている
    わけではない。それでも言う。宣言した保護領域が、引数に現れない書き込みに
    対しては 1 つも守られていない状態は、外から見ると守られている状態と
    区別が付かない。

    見に行き方は判定と同じ。別の見方をすると、検証は通ったのに実運用では
    何も見えない、という一番まずい形になる。
    """
    registered = _registered(root)
    if registered is None:
        # 設定ファイルが無い、あるいは読めない。どこに登録されているかを
        # 言えないので、登録についても、その監視が見る先についても黙る。
        # 読めないこと自体は _project_settings が言う。
        return []

    if not registered:
        # 登録されていないなら、見る先の話はしない。走らない監視に対して
        # 「見えない」と言っても、直す先が 2 つあるように読めるだけになる。
        return [
            Problem(
                SEVERITY_WARN,
                "(project)",
                f"{PROJECT_SETTINGS} の PostToolUse に ccnavi が登録されていない。"
                "実行後の監視は走らないので、ビルドの副作用やスクリプトが内部で開いた"
                "ファイルによる保護領域の変更は誰も見ていない",
            )
        ]

    problems: list[Problem] = []
    top = gitstate.top_level(root)
    _, unreadable = gitstate.read(top)
    if unreadable:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(project)",
                f"作業ツリーを読めない（{unreadable}）ので実行後の監視は何も検知しない。"
                "実行前の判定はこれまでどおり動く",
            )
        )
    return problems


def _registered(root: str, event: str = hookio.POST_TOOL_USE) -> bool | None:
    """そのイベントに ccnavi が登録されているか。設定ファイルが無ければ None。

    コマンド文字列に名前が含まれるかどうかで見る。実行ファイルの置き場も
    呼び出し方もプロジェクトごとに違うので、綴りを決め打ちにはできない。

    無いときに「登録されていない」と言い切らないのは、hook をここ以外
    （利用者ごとの設定）に書くことができ、そちらはこの検証から見えないため。
    見えないものを「無い」と報告すると、正しい設定に苦情を出すことになる。
    """
    try:
        with open(os.path.join(root, PROJECT_SETTINGS), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # 読めないことは _project_settings が言う。ここで二重には言わない。
        return None

    hooks = data.get("hooks") if isinstance(data, dict) else None
    entries = hooks.get(event) if isinstance(hooks, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
            command = hook.get("command") if isinstance(hook, dict) else None
            # 大文字小文字を無視する。実行ファイルの位置を環境変数から取る形
            # （`${CCNAVI_BIN_PATH}`）が普通にあり、そこでは名前が大文字で書かれる。
            # 区別すると、正しく登録されている設定に「登録されていない」と言う。
            if isinstance(command, str) and "ccnavi" in command.lower():
                return True
    return False


def _project_settings(root: str) -> list[Problem]:
    """`.claude/settings.json` の env に、防御を無効化する値が無いかを見る。

    このファイルは作業ツリーの中にあり、エージェントが書き換えられ、書いた値は
    次のセッションから効く。ccnavi は環境変数しか読まないので、ここに書かれた
    off が「人がセッションを起動するときに渡した off」と同じ顔をして届く。
    判定の側にはその 2 つを見分ける手段が無いから、書かれていることを
    見つけられる場所はここしかない。
    """
    path = os.path.join(root, PROJECT_SETTINGS)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        # 無いのは異常ではない。ccnavi は環境変数だけでも動く。
        return []
    except (OSError, ValueError) as exc:
        return [Problem(SEVERITY_WARN, "(project)", f"{PROJECT_SETTINGS} を読めない: {exc}")]

    env = data.get("env") if isinstance(data, dict) else None
    if not isinstance(env, dict):
        return []

    problems: list[Problem] = []
    declared = env.get(settings.MODE_ENV)
    if isinstance(declared, str) and declared:
        normalized = declared.lower()
        if normalized == modes.DISABLE:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    "(project)",
                    f"{PROJECT_SETTINGS} の env が {settings.MODE_ENV}={modes.DISABLE} を"
                    "宣言している。"
                    "監視される側が書けるファイルから監視を止めている。"
                    "止めるならセッションを起動する側の環境から渡す",
                )
            )
        elif normalized not in (modes.DRY_RUN, modes.ENABLE):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(project)",
                    f"{PROJECT_SETTINGS} の env の {settings.MODE_ENV}={declared!r} は"
                    f"モードとして読めない。{modes.ENABLE} に落ちる",
                )
            )
    return problems


def _rules(path: str, root: str = "", home: str = "") -> list[Problem]:
    """ルールファイルを、判定が読むのと同じ読み方で読んで検証する。

    rules.load をそのまま呼ぶ。別の読み方をすると、検証は通ったのに実運用で
    落ちるという、検証があるぶんかえって危ない形になる。

    `home` は、ルールが指すファイル（additionalContextFile）を探す起点。プロジェクトの
    ルールならそのプロジェクトのルート。省けばワークスペースルート、それも無ければ
    ルールファイルの隣。
    """
    home = home or root or os.path.dirname(os.path.abspath(path))
    try:
        rule_set, problems = rules.load(path, root)
    except (OSError, ValueError) as exc:
        # block モードではこれがそのまま全ツール呼び出しの拒否になり、
        # このファイルを直すための呼び出しも止まる。いちばん重い error。
        return [Problem(SEVERITY_ERROR, "(rules)", f"ルールを読めない: {exc}")]

    problems = list(problems)

    if not rule_set.deny:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "(rules)",
                "`deny` が空。何も止めないガードは、入っていないガードと"
                "同じでありながら、入っているように見える",
            )
        )

    if not rule_set.allow:
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
                Problem(SEVERITY_WARN, name, "id が無い。記録も報告もこのルールを名指しできない")
            )
        elif rule.id in seen:
            problems.append(
                Problem(
                    SEVERITY_WARN, name, "id が重複している。記録からどちらが当たったか辿れない"
                )
            )
        seen.add(rule.id)
        problems.extend(_rule_problems(rule, name, home))

    return problems


def _rule_problems(rule: rules.Rule, name: str, home: str) -> list[Problem]:
    """ルール 1 件の中身。当たらない match、届かない message、指すファイル、広い allow。"""
    problems: list[Problem] = []
    for tool in _inert(rule.match):
        problems.append(
            Problem(SEVERITY_WARN, name, f"match の {tool} には当てる対象が無い。何も止まらない")
        )

    if rule.message and rule.decision != rules.DENY:
        # ask の文面は人の確認ダイアログにしか出ず、allow の文面はどこにも出ない。
        # 書いた人は「モデルに届く」と思って書くので、届かない欄を残さない。
        # ルールは効いているので判定は変わらない。直すまで CI が落ちるだけ。
        where = (
            "人の確認ダイアログにしか出ない" if rule.decision == rules.ASK else "どこにも届かない"
        )
        problems.append(
            Problem(
                SEVERITY_ERROR,
                name,
                f"{rule.decision} に message がある。{where}ので、モデルに渡す文は "
                "additionalContext に書き、message は消す",
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
                    f"{key} の {rel} が無い。作業ツリーにもルートにも無ければ何も足さない",
                )
            )
        elif ctxfile.over_limit(full):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"{key} の {rel} は {ctxfile.MAX_CHARS} 文字を超える。"
                    "先頭だけが届き、切ったことを末尾に添える",
                )
            )

    # once の文は文脈ごとに 1 度しか積まれないので、広さは咎めない。
    if (rule.additional_context or rule.additional_context_file) and rule.decision == rules.ALLOW:
        why = _broad(rule)
        if why:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"広い allow に additionalContext がある（{why}）。"
                    "当たるたびに同じ文がコンテキストに積まれる。狭いルールに分けて書く",
                )
            )
    return problems


def _broad(rule: rules.Rule) -> str:
    """allow のルールが広いと言える理由。無ければ空。

    additionalContext は当たった回ごとにモデルへ渡るので、広い allow に書くと
    ls のたびに同じ文が積まれる。「広い」の判定は 2 つで、どちらも書き方から
    機械的に言えるものに限る。当たる頻度は記録を見ないと分からないので、
    ここでは扱わない。

    1. 何にでも当たる。翻訳後の式が、当てる語を含まない文字列にも当たる
    2. 選択肢が 3 つ以上。`(ls|cat|sed)` のような並びは、それだけ多くの
       コマンドに同じ文を添えることになる
    """
    if rule.compiled is not None and rule.compiled.search("x"):
        return "何にでも当たる"
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

    ツール名の一覧を持たずに subject_of を実際に呼んで確かめている。一覧を写すと、
    判定側が扱うツールを増やしたときにこちらが黙って古くなり、正しいルールを
    誤って咎めるようになる。差し込む欄の名前だけは judge の表から借りる。
    """
    # 対象を持つツールなら何かしら返る値を入れておく。返るかどうかだけを見る。
    probe_input = {field: "x" for field in judge.SUBJECT_FIELDS.values()}
    inert: list[str] = []
    for want in match.split("|"):
        tool = want.strip()
        if not tool:
            continue
        probe = hookio.Input(tool_name=tool, tool_input=probe_input)
        if not judge.subject_of(probe):
            inert.append(tool)
    return inert

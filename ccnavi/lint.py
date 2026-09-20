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
    phase,
    phasetypes,
    review,
    risk,
    ruleload,
    rules,
    selfguard,
    settings,
    tree,
)
from . import ticket as ticket_mod
from .modes import EXIT_ERROR, EXIT_OK
from .rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, Problem

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
    # 確認できる者が居ないモードの門。2 値しか取らないので、受け皿も分ける。
    unwatched_said = io.StringIO()
    guard_unwatched = selfguard.resolve(
        unwatched_said,
        "",
        conf.guard_unwatched,
        settings.GUARD_UNWATCHED_ENV,
        selfguard.GATE_SETTINGS,
    )

    problems = check(root, conf, notes, mode, complaints.getvalue())
    problems += [
        Problem(SEVERITY_WARN, "(restore)", line.removeprefix("ccnavi: "))
        for line in said.getvalue().splitlines()
    ]
    problems += [
        Problem(SEVERITY_WARN, "(unwatched)", line.removeprefix("ccnavi: "))
        for line in unwatched_said.getvalue().splitlines()
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
    # 切ってあること自体は設定として正しい。それでも言うのは、切れている状態が
    # 外から見て「ルールが揃っている状態」と区別が付かないため。
    if guard_unwatched == selfguard.DISABLE:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(unwatched)",
                f"{settings.GUARD_UNWATCHED_ENV}=disable。"
                f"{' と '.join(judge.PERMISSION_NO_JUDGE)} では、"
                "ルールがどこも言及しない呼び出しを止めない",
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
    warns = sum(1 for p in problems if p.severity == SEVERITY_WARN)
    # info は数えるが、終了コードには効かない。層をまたいだ重複のように「そう
    # 書いてあるとおりに効いているが、書いた人が知りたいはずのこと」が入る。
    infos = sum(1 for p in problems if p.severity == SEVERITY_INFO)
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
            "infos": infos,
        }
        stdout.write(json.dumps(payload, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_ERROR if errors else EXIT_OK

    stdout.write("ccnavi: 設定を検証する\n")
    stdout.write(f"  ルール: {conf.rules}\n")
    stdout.write(f"  deny の場所を戻す: {restore_if_deny}\n")
    stdout.write(f"  コアファイルを守る: {guard_core_files}\n")
    stdout.write(f"  確認できる者が居ないモードで守る: {guard_unwatched}\n")
    stdout.write(f"  チケット制御: {conf.ticket_control or selfguard.ENABLE}\n")
    if conf.tickets_enabled:
        stdout.write(f"  チケットの承認の経路を守る: {guard_ticket_approval or selfguard.ENABLE}\n")
    # 環境変数はこの起動が受け取ったものであって、セッションが受け取るものではない。
    # 端末から叩いた検証と hook から届く環境は別物なので、どちらを見た結果なのかを
    # 名乗らせる。名乗らないと、通った検証が別の設定についての報告になる。
    stdout.write(f"  モード: {mode}（この起動の環境から解決したもの）\n")

    for problem in problems:
        stdout.write(f"{problem}\n")

    stdout.write(f"error {errors} 件、warn {warns} 件、info {infos} 件\n")
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
    problems.extend(_risk(conf, root))
    problems.extend(_ticket(conf, root))
    problems.extend(_scratch(conf, root))
    problems.extend(_projects(conf, root))
    # 層の読み込みは判定と同じ経路（ruleload.survey）を通る。読めない層の苦情は
    # そこが書く標準エラーにも出るので、受け皿へ逃がして二重に言わない。
    problems.extend(_layers(io.StringIO(), conf, root))
    problems.extend(_layer_configs(conf, root))
    problems.extend(_worktree_layers(conf, root))
    problems.extend(_ticket_places(conf, root))
    return problems


def _risk(conf: settings.Settings, root: str = "") -> list[Problem]:
    """共通層のリスクの配点が読めるか。無いのは不備ではない（組み込みの配点）。

    `script:` が指す先が在ることも見る。走らせるときは「測れなかった」で重い側に
    倒れるが、そこで気づくのは子を閉じる瞬間になる（設計 §11.4.2）。
    """
    if not conf.risk:
        return []
    definition, notes = risk.load(conf.risk)
    problems = [Problem(p.severity, "(risk)", f"{p.rule}: {p.detail}") for p in notes]
    if not definition.fallback:
        risk.mark_layer(definition, settings.LAYER_COMMON, root)
        problems += [
            Problem(p.severity, "(risk)", f"{p.rule}: {p.detail}")
            for p in risk.script_problems(definition)
        ]
    return problems


def _phases(conf: settings.Settings) -> list[Problem]:
    """フェーズの種類の定義が読めるか。無いのは不備ではない（番号だけの挙動）。"""
    if not conf.phases:
        return []
    _, notes = phasetypes.load(conf.phases)
    return [Problem(p.severity, "(phases)", f"{p.rule}: {p.detail}") for p in notes]


def _copy_problems(
    root: str,
    conf: settings.Settings,
    copies: list[ticket_mod.Ticket],
    index: dict[str, ticket_mod.Ticket],
    closed: list[ticket_mod.Ticket],
) -> list[Problem]:
    """作業中の承認済みチケットを検査する（ADR-0058）。

    置き場を動かして承認する運びでは `--approve` を通らないので、承認のときにしか
    当たらなかった検査が誰にも当たらない。判定は `blocked` の分だけを止めるが、
    止まる場所は書き込みのときで、そこで初めて知るのは遅い。ここで全部言う。

    severity は 3 通りに分かれる。

    - `blocked` は判定が止める理由なので error
    - フェーズの順序は warn。狂っていても範囲の決まり方には効かず、判定も止めない
      （`approval.blocking_problems`）。承認のときは error だが、承認済みのものに当てるのは
      「その順で始めた」という記録で、いま止める根拠にはならない
    - 計画の形と、種類の定義が読めないことは `validate` が付けた severity のまま（error）。
      判定は止めないが、承認の画面を通っていれば起きない形なので、置き場を動かして
      承認した分の壊れを CI で止める。範囲の超過だけは `validate` も warn
    """
    from . import phase as phase_mod

    problems: list[Problem] = []
    pool = dict(index)
    for t in closed:
        pool.setdefault(t.ticket, t)
    resolve = _types_resolver(conf, root)
    for t in copies:
        if t.blocked:
            problems.append(Problem(SEVERITY_ERROR, "(ticket)", f"{t.ticket}: {t.blocked}"))
            continue
        types = resolve(approval.project_of(t, pool))
        complaints, overflow = approval.validate(t, pool, types)
        for p in complaints + overflow:
            problems.append(Problem(p.severity, "(ticket)", f"{t.ticket}: {p.detail}"))
        parent = pool.get(t.parent) if t.is_child else None
        if parent is not None:
            for p in phase_mod.order_problems(root, conf, t, parent, types):
                # 承認のときは error。承認済みのものに当てるのは「その順で始めた」という
                # 記録で、いま止める根拠にはならない。
                problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {p.detail}"))
    return problems


def _types_resolver(conf: settings.Settings, root: str):
    """`project:` から、そのチケットに効く種類を引く（設計 §11.4.1）。

    承認の対象の中でもチケットごとに層が違いうるので、1 つに決めずに引く形で渡す。
    読み込みは 1 層 1 回。
    """
    cache: dict[str, dict | None] = {}

    def resolve(project: str = ""):
        if project not in cache:
            cache[project] = phase.load_types(conf, root, project)
        return cache[project]

    return resolve


def _ticket(conf: settings.Settings, root: str = "") -> list[Problem]:
    """チケットと承認済みチケットとワークツリーが噛み合っているかを見る（REQ-TKT-25）。

    判定に効くのは承認済みチケットの側だけなので、ここで問うのは「効いている範囲は何か」と
    「ワークツリーと提案がそれと一致しているか」。一致していない状態は壊れては
    いないが、書いた人は書いたとおりに効いていると思っている。
    検証はその思い違いを名指しする場所になる。
    """
    problems: list[Problem] = []
    if not conf.tickets_enabled:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{settings.TICKET_CONTROL_ENV}=disable。チケットの範囲・フェーズの HITL ポイント・"
                "サブエージェントの制限は効かない",
            )
        )
        return problems
    root = root or os.getcwd()
    problems.extend(_legacy_tickets(conf, root))
    if not _any_copies(conf, root) and not tree_has_tickets(root, conf.tickets):
        # 承認済みチケットも提案も無い状態は不備ではない。チケットによる制御は任意で、
        # 使っていないプロジェクトにここで苦情を返すと、その 1 行が常態になって
        # 他の報告ごと読まれなくなる。
        return problems

    problems.extend(_approved_guarded(conf, root))

    copies, notes = approval.scan(conf, root)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    # 閉じた承認済みチケットの苦情も拾う。判定は閉じたものを読まないが、読めないファイルが
    # 置き場に残っていること自体は書いた人の思い違いで、黙ると他の機械へそのまま届く。
    closed, notes = approval.scan(conf, root, closed=True)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    review, notes = approval.scan_review(conf, root)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    index = approval.by_id(copies)
    done = {t.ticket for t in closed + review}

    proposals, complaints = ticket_mod.scan(root, conf.tickets, conf.projects)
    problems.extend(complaints)

    problems.extend(_copy_problems(root, conf, copies, index, closed))

    resolve = _types_resolver(conf, root)
    for t in copies:
        if not t.is_child and t.has_plan and resolve(t.project) is None:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    "(phases)",
                    f"{t.ticket} は計画を持つのにフェーズの種類の定義"
                    f"（{conf.phases} と {t.project or '自身'} の層）が読めない",
                )
            )

    # ツリーの名前から、そのツリーがどのリポジトリのものかを引く表。ワークスペースなら空、
    # プロジェクトならその名前で、ワークツリーは元リポジトリのほうに付く（tree.Tree）。
    # チケットは自分の置かれたツリーの名前しか持たないので、ここで作って渡す。
    repo_of = {
        t.name or "(ワークスペースルート)": t.project for t in tree.all_trees(root, conf.projects)
    }
    problems.extend(_proposal_problems(proposals, index, closed, done, repo_of))
    problems.extend(_approval_problems(root, conf, proposals, copies, closed, review))

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
                f"{stray} はワークツリーでもワークスペースルートでもないのに .claude/ を持つ。"
                "cd 1 回で別のワークスペースルートに見える",
            )
        )
    return problems


def _any_copies(conf: settings.Settings, root: str) -> bool:
    """どこかのツリーに承認済みチケットの置き場があるか。"""
    return any(
        os.path.isdir(settings.approved_dir(conf, t.root)) for t in approval.trees(conf, root)
    )


def _approved_guarded(conf: settings.Settings, root: str) -> list[Problem]:
    """承認済みチケットの置き場が守られているか。

    置き場は ccnavi ディレクトリ（`.ccnavi/`）の下にあり、守るのは組み込みの 1 本
    （`builtin-guard-project-home`）。ルールファイルには書かせない（書かせると消せる）。
    ここで見るのは、置き場が本当に ccnavi ディレクトリの下にあるか。外に向けると、
    その 1 本が当たらず、エージェントが承認済みチケットを書けて承認の意味が無くなる。
    """
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("\\", "/").strip("/")
    approved = (conf.approved or settings.DEFAULT_APPROVED).replace("\\", "/").strip("/")
    if home and (approved == home or approved.startswith(home + "/")):
        return []
    return [
        Problem(
            SEVERITY_ERROR,
            "(ticket)",
            f"承認済みチケットの置き場（{settings.APPROVED_ENV}={conf.approved}）が"
            f"ccnavi ディレクトリ（{settings.PROJECT_HOME_ENV}={conf.project_home}）の外にある。"
            "組み込みが守らないので、エージェントが承認済みチケットを書けて承認の意味が無い",
        )
    ]


def _approval_problems(
    root: str,
    conf: settings.Settings,
    proposals: list,
    copies: list,
    closed: list,
    review: list,
) -> list[Problem]:
    """承認で落ちるものを、承認の前に名指しする。人が端末で初めて知るより早く。

    **承認と同じ関数を通す**（`approval.candidates`）。以前はここだけが `approval.validate`
    を当てていて、順序で落ちる子（前のフェーズが閉じていない）・計画に無い番号・`project:`
    の食い違い・改版の検査を見ていなかった。同じ事実を数える経路が 2 本あると、片方が
    黙って弱くなる。`--approve --preview --verify` と同じ答えをここでも言う。

    範囲の超過は承認では落ちないが、判定で止まるので同じく名指しする（warn）。

    **「まだ承認できない」だけは warn に落とす。** 前のフェーズが閉じていない子
    （`rules.KIND_NOT_YET`）は、書いた側に直すものが無く、前が閉じれば同じ提案が通る。
    `--lint` はワークスペース全体を見る道具で、その終了コードは VS Code の設定画面が
    保存してよいかの判断にも使われる（`phases-panel.ts`）。ここを error にすると、
    編集と関わりのない提案 1 本で、設定の保存も CI も止まる。承認そのものは落とす
    （`approval.candidates` の側は error のまま）ので、緩むのは報告の重さだけ。
    """
    pending, revisions = approval.waiting(proposals, copies, closed, review)
    if not pending and not revisions:
        return []
    batch, rejected, _pool = approval.candidates(root, conf, pending, revisions, copies)
    problems: list[Problem] = []
    for cand in batch:
        for p in cand.complaints + cand.overflow:
            problems.append(_said(cand.ticket.ticket, p))
    for t, complaints in rejected:
        for p in complaints:
            problems.append(_said(t.ticket, p))
    return problems


def _said(ticket: str, problem) -> Problem:
    """承認の苦情 1 件を、`--lint` の言い方に直す。「まだ承認できない」は warn へ落とす。"""
    severity = SEVERITY_WARN if problem.kind == rules.KIND_NOT_YET else problem.severity
    return Problem(severity, "(ticket)", f"{ticket}: {problem.detail}")


def _proposal_problems(
    proposals: list,
    index: dict,
    closed: list,
    done: set[str],
    repo_of: dict[str, str],
) -> list[Problem]:
    """提案の側。承認待ち、先行が閉じていない着手済み、同じ識別子の重複。

    承認で落ちるものは `_approval_problems` が言う（承認と同じ関数を通す）。

    `todo/` に在るものは全部承認待ち。同じ識別子がどこかの置き場（作業中・レビュー待ち・
    閉じた）に在れば、親の改版でない限り書き損じなので名指しする。

    `closed` は `done/` の承認済みチケット。`proposals` は `todo/` と `review/`。作業中と
    レビュー待ちと閉じたの 3 つを横断して数えないと、`doing/` と `done/` に同じ識別子が
    在る形（動かす途中で止まった跡）を CI が見逃し、状態の操作が「複数の場所にある」で
    止まって初めて知ることになる。

    **同じリポジトリの中ではツリーごとに数える。** 承認済みチケットは git に入れて運ぶので、
    切ったワークツリーの数だけ写しができる。しかもワークツリーはそれぞれ別のコミットを指すので、
    古いほうで `doing`・新しいほうで `done` になるのも普通の形。ツリーをまたいだ食い違いまで
    咎めると、ワークツリーを 2 本持つだけで閉じたチケットが全部 error になり、`--lint` が
    常に非ゼロで終わる。捕まえたいのは 1 つのツリーの中で 2 つの状態に在る形だけ。

    **リポジトリをまたいだら、状態が何であれ咎める。** プロジェクトは自分の git を持つので
    （設計 §11）、そこに同じ識別子が在るのは写しではなく別物の衝突。識別子は人が選ぶ短い
    連番で、プロジェクトが独立に振ればぶつかる。コミットの遅れでは説明が付かないから、
    ツリーごとの免除を当ててはいけない。
    """
    problems: list[Problem] = []

    # ツリーと状態は分けて持つ。同じ識別子が別のツリーに在るのは普通で（承認済みチケットは
    # git に入れて運ぶので、切ったワークツリーの数だけ写しができる）、しかもワークツリーは
    # それぞれ別のコミットを指すから、古いほうが doing・新しいほうが done になるのも普通。
    # 咎めるのは 1 つのツリーの中で 2 つの状態に在る形だけ。
    def place(t, state: str) -> tuple[str, str, str]:
        at = t.tree or "(ワークスペースルート)"
        return (repo_of.get(at, at), at, state)

    seen: dict[str, list[tuple[str, str, str]]] = {}
    for t in index.values():
        seen.setdefault(t.ticket, []).append(place(t, t.state or ticket_mod.DOING))
    for t in closed:
        seen.setdefault(t.ticket, []).append(place(t, t.state or ticket_mod.DONE))
    for t in proposals:
        seen.setdefault(t.ticket, []).append(place(t, t.state))
        if t.state != ticket_mod.TODO:
            continue
        if t.ticket in index:
            current = index[t.ticket]
            if not t.is_child and t.has_plan and approval._plan_differs(t, current):
                continue  # 親の改版。承認待ちに入る
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は承認済み（{current.tree or '(ワークスペースルート)'} の "
                    f"{current.state or ticket_mod.DOING}/）なのに todo/ にも在る。"
                    "計画の改版でなければ todo/ の側を消す",
                )
            )
            continue
        if t.ticket in done:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は閉じたかレビュー待ちなのに todo/ にも在る。"
                    "承認の対象にならない。再開は人が承認済みチケットを戻す",
                )
            )
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{t.ticket} は承認待ち（{t.tree or '(ワークスペースルート)'} の todo/）。"
                "'ccnavi --approve' を通すまで範囲は効かない",
            )
        )
    for t in index.values():
        if t.started_at:
            waiting = [p for p in t.predecessors if p not in done]
            if waiting:
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        "(ticket)",
                        f"{t.ticket} は先行 {', '.join(waiting)} が閉じていないのに着手している",
                    )
                )
    for ticket_id, places in seen.items():
        # 別のリポジトリに同じ識別子が在るのは、写しではなく**別物の衝突**。識別子は人が
        # 選ぶ短い連番なので、プロジェクトが独立に振れば普通にぶつかる。コミットの遅れでは
        # 説明できないので、状態が何であれ咎める。
        repos = sorted({repo for repo, _, _ in places})
        if len(repos) > 1:
            where = ", ".join(
                # ツリーの名前がリポジトリの名前と同じなら（プロジェクトの元ツリー）、
                # 2 度書かない。切ったワークツリーなら「どのリポジトリのどのツリーか」を出す。
                f"{repo or '(ワークスペース)'}:{state}"
                if at == repo
                else f"{repo or '(ワークスペース)'}/{at}:{state}"
                for repo, at, state in sorted(places)
            )
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    "(ticket)",
                    f"{ticket_id} が複数のリポジトリにある: {where}。"
                    "識別子はリポジトリごとに一意にする",
                )
            )
            continue
        for at in sorted({where for _, where, _ in places}):
            states = [state for _, where, state in places if where == at]
            distinct = sorted(set(states))
            # `todo/` に在るのは親の改版の途中なので、承認済みチケットと並んでいてよい。
            if len(distinct) > 1 and ticket_mod.TODO not in distinct:
                found = distinct
            elif len(states) > 1 and len(distinct) < len(states):
                found = states
            else:
                continue
            where = ", ".join(f"{at}:{state}" for state in found)
            problems.append(
                Problem(SEVERITY_ERROR, "(ticket)", f"{ticket_id} が複数の場所にある: {where}")
            )
    return problems


def _worktree_problems(
    root: str, conf: settings.Settings, worktrees: list, index: dict, copies: list
) -> list[Problem]:
    """ワークツリーの側。チケットの無いツリー、迷い込んだ承認済みチケット、元リポジトリの食い違い、ツリーの無い承認済みチケット。"""
    problems: list[Problem] = []
    for t in worktrees:
        if t.name not in index:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"ワークツリー {t.name} にチケットが無い。"
                    "そこへの書き込みはルールだけで判定する",
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
                        f"ワークツリー {t.name} の元リポジトリ（{t.project or 'ワークスペース'}）が"
                        "承認済みチケットの "
                        f"project（{owner or 'ワークスペース'}）と違う。そこへの書き込みは止まる。"
                        "承認済みチケットが指すリポジトリから切り直す",
                    )
                )
    names = {t.name for t in worktrees}
    for t in copies:
        if t.ticket not in names:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は承認済みだがワークツリー "
                    f"{tree.worktree_path(root, t.ticket)} が無い。作るまで効かない",
                )
            )
    return problems


def layer_where(name: str) -> str:
    """その層の苦情の出どころの綴り。VS Code 拡張がこの前置きでプロジェクトを引く。"""
    if name == ruleload.LAYER_COMMON:
        return "(rules)"
    if name == ruleload.LAYER_SELF:
        return "(self)"
    return f"(projects/{name})"


def _layers(stderr: TextIO, conf: settings.Settings, root: str) -> list[Problem]:
    """層が噛み合っているか（設計 §11.9、REQ-MLT-16）。

    見るのは 2 つ。層のファイルが読めることと、層をまたいだ重複と同名の衝突。
    `.ccnavi/config/` が無いことは言わない。
    無いのは正常（無い層 = 空）で、言うと本当に言うべきものが埋もれる。

    共通層は `_rules` が別に見ているので、ここでは層の 2 つ目以降だけを回す。
    """
    problems: list[Problem] = []
    for view in ruleload.survey(stderr, conf, root)[1:]:
        where = layer_where(view.name)
        if view.unreadable:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    where,
                    f"{view.path} を読めない ({view.unreadable})。この層は空として扱っている。"
                    "共通層だけで判定しているので、ここに書いた宣言は 1 件も効いていない",
                )
            )
            continue
        if view.missing:
            continue
        from_file = _rules(view.path, root, home=_layer_home(conf, root, view.name), layer=True)
        for c in from_file:
            problems.append(Problem(c.severity, f"{where} {c.rule}".rstrip(), c.detail))
        # `survey` は層のファイルの苦情も `problems` に入れている。`_rules` が同じファイルを
        # 読んで言ったものは数えない。数えると同じ苦情が 2 度並び、件数も水増しされる。
        told = {(c.severity, c.rule, c.detail) for c in from_file}
        for c in view.problems:
            if (c.severity, c.rule, c.detail) in told:
                continue
            problems.append(Problem(c.severity, f"{where} {c.rule}".rstrip(), c.detail))
    return problems


def _layer_configs(conf: settings.Settings, root: str) -> list[Problem]:
    """各層の phases / risk が、共通層と合成できるか（設計 §11.4.1、§11.4.2）。

    見るのは合成したあとの姿。同 `id` で中身が違う、`title` が層をまたいで重なる、
    `levels` が逆転する、`script:` が層の外を指すか指す先が無い、を error で言い、
    全欄一致で捨てた重複を info で言う。共通層自身の苦情は `_phases` / `_risk` が
    別に言うので、ここでは層の側だけを数える。

    `.ccnavi/config/` が無いことは言わない。無いのは正常（無い層 = 空）。
    """
    problems: list[Problem] = []
    names = [ruleload.LAYER_SELF]
    names += [
        p.name for p in tree.projects(conf.projects) if not settings.is_reserved_layer_name(p.name)
    ]
    for name in names:
        where = layer_where(name)
        project = "" if name == ruleload.LAYER_SELF else name
        _, notes = phase.layer_types(conf, root, project)
        for p in notes:
            problems.append(Problem(p.severity, f"{where} (phases) {p.rule}".rstrip(), p.detail))
        definition, notes = risk.layer_definition(conf, root, project)
        for p in [*notes, *risk.script_problems(definition, name)]:
            problems.append(Problem(p.severity, f"{where} (risk) {p.rule}".rstrip(), p.detail))
    return problems


def _worktree_layers(conf: settings.Settings, root: str) -> list[Problem]:
    """ワークツリーの ccnavi ディレクトリに、元リポジトリに無いファイルがあるか（設計 §11.6）。

    判定が読むのは元リポジトリに checkout されている版だけ（REQ-MLT-04）。
    ワークツリーの `.ccnavi/` に足したファイルは、そのブランチが統合されるまで効かない。
    効かないものを書いた人は、書いたとおりに効いていると思ったまま進む。統合の前に
    気づけるように、ここで名前を挙げる。

    足したファイルを咎めているのではない。設定を育てる場所はワークツリーでよく、
    そこから統合する道も普通の道。言うのは「今はまだ効いていない」という 1 点だけ。

    中身の違いは見ない。同じ綴りのファイルが両方に在れば、それは編集で、git の
    差分が拾う。ここが拾うのは、元リポジトリに無くて差分にも出ない新しい綴りのほう。
    """
    problems: list[Problem] = []
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("/", os.sep)
    for work in tree.worktrees(root, conf.projects):
        origin = tree.project_root(conf.projects, work.project) if work.project else root
        for rel in _files_under(os.path.join(work.root, home)):
            if os.path.exists(os.path.join(origin, home, rel.replace("/", os.sep))):
                continue
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    f"({tree.WORKTREES_DIR.replace(os.sep, '/')}/{work.name})",
                    f"{conf.project_home}/{rel} はワークツリーにしかない。判定が読むのは"
                    "元リポジトリの版なので、このファイルは統合されるまで"
                    "効かない",
                )
            )
    return problems


def _files_under(base: str) -> list[str]:
    """base の下のファイルを、base からの相対（"/" 区切り）で並べる。順は綴り順。"""
    found = []
    for parent, _, names in os.walk(base):
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(parent, name), base)
            found.append(rel.replace(os.sep, "/"))
    return sorted(found)


def _layer_home(conf: settings.Settings, root: str, name: str) -> str:
    if name == ruleload.LAYER_SELF:
        return root
    return tree.project_root(conf.projects, name)


def _scratch(conf: settings.Settings, root: str) -> list[Problem]:
    """下書きの置き場が、そのリポジトリの git に追跡されていないか（REQ-TKT-44）。

    実行前の判定はチケットの範囲を `scratchpad/` に当てない（`ticket.is_scratch_place`）。外して
    よい根拠は「git が追跡しないので統合先のブランチに乗らない」ことの 1 つだけ。

    **この警告は穴を塞ぐものではない。** 根拠が崩れた場合は、実行後の監視と
    サブエージェント終了時の検査が `scratchpad/` の変更を範囲外として報告する（`is_unscoped` の
    説明）。ここが言うのは、その報告が出はじめる前に人が気づけるようにするため。

    問うのは 2 つ。**追跡されているファイルが既にあるか**（`git ls-files`）と、これから
    書くものが追跡されるか（`git check-ignore`）。前者だけでは、まだ何も置いていない
    リポジトリで見逃す。後者だけでは、`/{SCRATCH}/` を足す前から追跡されていたファイルを
    見逃す（`.gitignore` は既に追跡されているファイルには効かない）。

    見るのはワークスペースと各プロジェクトのそれぞれの git。プロジェクトは自分の
    `.gitignore` を持つので、ワークスペース側の 1 行は届かない。ワークツリーは見ない。
    あちらはブランチごとに中身が変わるうえ、実行時に監視が拾うので、ここで数え上げると
    同じことを 2 度言うことになる。

    チケット制御が切れているときは言わない。そのとき範囲の判定自体が動かない。

    git が無ければ何も言わない。無いものを「入っていない」と報告すると、正しい設定に
    苦情を出すことになる。
    """
    if not conf.tickets_enabled:
        return []
    problems: list[Problem] = []
    # 名乗るのは `(scratch)` の側。プロジェクトのぶんも `(projects/<名前>)` とは名乗らない。
    # あちらはその層の設定についての苦情で、ここは追跡の話。同じ名札にすると、
    # 「層について何も言わない」ことを見ているテストや読み手に、別の話が混ざる。
    where = [("(scratch)", root)]
    where += [
        (f"(scratch/{p.name})", tree.project_root(conf.projects, p.name))
        for p in tree.projects(conf.projects)
    ]
    place = ticket_mod.SCRATCH
    for name, home in where:
        tracked = _tracked(home, place)
        ignored = _ignored(home, place + "/")
        if tracked:
            why = f"`{place}/` に追跡されているファイルがある（{tracked}）"
        elif ignored is False:
            why = f"`{place}/` がこのリポジトリの git で無視されていない"
        else:
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{why}。実行前の判定はチケットの範囲をここに当てないので、追跡されて"
                "いると、承認した範囲の外のものがコミットに乗りうる。そうなったぶんは"
                "実行後の監視とサブエージェント終了時の検査が範囲外として報告するので、"
                "下書きがそのたびに咎められることになる。"
                f"このリポジトリの `.gitignore` に `/{place}/` を足して追跡から外すか"
                "（プロジェクトのリポジトリに運用の痕跡を残したくないなら"
                f"`.git/info/exclude` でもよい）、`{place}/` を使わずにチケットの範囲の"
                "中で作業する",
            )
        )
    return problems


def _tracked(root: str, rel: str) -> str:
    """その置き場の下で git が追跡しているファイル 1 本。無ければ空。

    `.gitignore` は既に追跡されているファイルには効かないので、無視の設定だけを見ても
    「追跡されていない」は言えない。索引に何が入っているかを直接問う。
    """
    rc, out = gitcmd.output(root, ["ls-files", "--", rel + "/"], gitstate.TIMEOUT_SECONDS)
    if rc != 0:
        return ""
    first = out.strip().splitlines()
    return first[0] if first else ""


def _projects(conf: settings.Settings, root: str) -> list[Problem]:
    """プロジェクトの置き場が噛み合っているか（REQ-MLT-16）。

    置き場が無いのは不備ではない。あるなら、ワークスペースの git で無視されていること、
    予約名（`common` / `self`、綴り違いも含む）を使っていないこと、プロジェクトが
    `.claude/` を持たないことを見る。層の中身は
    `_layers` が見る。
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
        if settings.is_reserved_layer_name(p.name):
            reserved = " と ".join(f"`{name}`" for name in settings.RESERVED_LAYER_NAMES)
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    where,
                    f"{reserved} は層の名札に予約してある（`{settings.LAYER_COMMON}` は共通層、"
                    f"`{settings.LAYER_SELF}` はワークスペース自身の層）。このプロジェクトは"
                    f"層として数えていない（id の `{p.name}:` がどちらの層の話か決まらない。"
                    "綴りの大文字小文字は問わない）。ここに置いた宣言は 1 件も効いておらず、"
                    "このプロジェクトを行き先にするパスを持つツール（Read / Grep / Glob / Write / "
                    "Edit / NotebookEdit）は共通層だけで判定している。"
                    "別の名前に変える",
                )
            )
        if os.path.isdir(os.path.join(p.root, ".claude")):
            # 文面の先頭「.claude/ を持つ」は VS Code 拡張（vscode-extension/ccnavi-board の
            # projects-render.ts）が同じ事象の説明と重ねないために見ている。変えるならそちらも直す
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    where,
                    ".claude/ を持つ。Claude Code がそこのスキルを読み、cd 1 回で別のルートに"
                    f"見える。プロジェクトの設定は {conf.project_home}/ に置く",
                )
            )
    return problems


def _ticket_places(conf: settings.Settings, root: str) -> list[Problem]:
    """走査されないチケットの置き場が残っていないか（REQ-MLT-16）。

    提案の置き場はどのツリーでも同じ相対（`wip/proposals/`）で、プロジェクト向けはその
    プロジェクトのツリーに置く（設計 §11.5、REQ-MLT-14）。ワークスペースの
    `wip/<名前>/proposals/` は、名前が `projects/` に在っても在らなくても走査されない。走査
    されない置き場は、提案があっても画面にもボードにも出ない。黙って消えるのが
    いちばん困るので名指しし、名前が在るなら正しい置き場を案内する。error にはしない。
    判定は動いている。
    """
    parts = [p for p in conf.tickets.split("/") if p]
    if len(parts) < 2:
        # 名前を挟む位置が置き場の綴りから決められない。言えないことは言わない。
        return []
    head, tail = parts[0], parts[1:]
    known = {p.name for p in tree.projects(conf.projects)}
    where = os.path.relpath(conf.projects, root).replace(os.sep, "/") if conf.projects else "(無し)"
    problems: list[Problem] = []
    try:
        names = sorted(os.listdir(os.path.join(root, head)))
    except OSError:
        return []
    for name in names:
        if name == tail[0]:
            continue
        place = os.path.join(root, head, name, *tail)
        if not os.path.isdir(place):
            continue
        rel = "/".join([head, name, *tail])
        if name in known:
            proper = "/".join([where, name, *parts])
            why = f"プロジェクト向けの提案はそのプロジェクトの側 {proper}/ に置く"
        else:
            why = (
                f"{name} が {where} のプロジェクトとして数えられていない"
                "（`.git` がまだ無いか、名前が違う）"
            )
        problems.append(
            Problem(SEVERITY_WARN, "(projects)", f"{rel}/ の提案は走査されていない。{why}")
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
        path = os.path.join(root, ".ccnavi", "scripts", name)
        if not os.path.isfile(path):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(project)",
                    f".ccnavi/scripts/{name} が無い。止まっている間に通る形が無くなる",
                )
            )
    return problems


def _legacy_tickets(conf: settings.Settings, root: str) -> list[Problem]:
    """旧の置き場に取り残されたチケットを名指しする（ADR-0054、ADR-0055）。

    旧の置き場は 3 つ。提案の `wip/tickets/`（ADR-0054 まで）、承認済みチケットの
    `.ccnavi/tickets/`（直下と `closed/`。ADR-0055 まで）、いまの提案の置き場の
    `doing/` `done/` `cancelled/`（ADR-0055 まで）。どれも走査されないので、残っていると
    「承認待ちは無い」「開いているチケットは無い」で通る。黙って通る向きなので、ここで言う。

    見るのは置き場の有無ではなく、**中に残っているチケット**。置き場の有無だけで決めると、
    新しい置き場を 1 つ作った時点で、旧の置き場に残ったものが永久に見えなくなる
    （移し忘れがいちばん起きるのはこの形）。

    数えないのは、同じ識別子がいまの置き場のどこか（`todo/` `review/` `doing/` `done/`）に
    在るもの。写し終えた分か、閉じたことの記録として残っているだけの分で、害が無い。

    綴りを設定で決めた人には、その置き場の旧の綴りは言わない。自分の綴りで動かしている
    ので、既定の話は関係が無い。
    """
    known = {t.ticket for t in approval._everything(conf, root)}
    proposals, _ = ticket_mod.scan_all(root, conf.tickets, conf.projects)
    known |= {t.ticket for t in proposals}
    problems: list[Problem] = []
    if conf.tickets == settings.DEFAULT_TICKETS:
        stale = _stale_by_tree(conf, root, settings.LEGACY_TICKETS, ticket_mod.LEGACY_STATES, known)
        if stale:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"提案の置き場の既定が `{settings.LEGACY_TICKETS}` から "
                    f"`{settings.DEFAULT_TICKETS}` に変わった。"
                    f"旧の置き場に残っていて走査されないチケットがある: {'、'.join(stale)}。"
                    f"承認待ちなら `{settings.DEFAULT_TICKETS}/todo/` へ移し、済んだものは消す"
                    f"（`{settings.TICKETS_ENV}={settings.LEGACY_TICKETS}` を "
                    "`.claude/settings.json` の env に足せば旧の綴りのまま動くが、"
                    "状態の置き場は今の形（todo / review）で読む）",
                )
            )
    old_states = tuple(s for s in ticket_mod.LEGACY_STATES if s not in ticket_mod.STATES)
    stale = _stale_by_tree(conf, root, conf.tickets, old_states, known)
    if stale:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"提案の置き場の状態は `todo` と `review` の 2 つになった（ADR-0055）。"
                f"旧の状態の置き場（{' / '.join(old_states)}）に残っていて走査されないチケットが"
                f"ある: {'、'.join(stale)}。閉じたものは消し、途中のものは人が承認済みチケットの"
                f"置き場（{conf.approved}/doing/）へ戻す",
            )
        )
    if conf.approved == settings.DEFAULT_APPROVED:
        stale = _stale_by_tree(
            conf, root, settings.LEGACY_APPROVED, ("", approval.LEGACY_CLOSED_DIR), known
        )
        if stale:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"承認済みチケットの置き場の既定が `{settings.LEGACY_APPROVED}` から "
                    f"`{settings.DEFAULT_APPROVED}` に変わった（ADR-0055）。"
                    f"旧の置き場に残っていて読まれない承認済みチケットがある: {'、'.join(stale)}。"
                    f"開いていたものは `{settings.DEFAULT_APPROVED}/doing/` へ、閉じたものは "
                    f"`{settings.DEFAULT_APPROVED}/done/` へ、`phases/` はそのまま "
                    f"`{settings.DEFAULT_APPROVED}/phases/` へ移す（git mv）",
                )
            )
    return problems


def _stale_by_tree(
    conf: settings.Settings, root: str, place_rel: str, states: tuple, known: set[str]
) -> list[str]:
    """ツリーごとに、旧の置き場に在っていまの置き場から見えない識別子を並べる。"""
    stale = []
    for t in approval.trees(conf, root):
        left = _left_behind(t.root, place_rel, states, known)
        if left:
            stale.append(f"{t.name or '(ワークスペースルート)'}（{', '.join(left)}）")
    return stale


def _left_behind(tree_root: str, place_rel: str, states: tuple, known: set[str]) -> list[str]:
    """このツリーの旧の置き場に在って、いまの置き場から見えないチケットの識別子。"""
    old = os.path.join(tree_root, place_rel.replace("/", os.sep))
    found = []
    for state in states:
        try:
            names = sorted(os.listdir(os.path.join(old, state) if state else old))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".md") or name[:-3] in known:
                continue
            found.append(name[:-3])
    return sorted(set(found))


def tree_has_tickets(root: str, tickets_rel: str) -> bool:
    """ワークスペースルートかワークツリーのどこかに提案の置き場があるか。"""
    for t in [tree.main_tree(root), *tree.worktrees(root)]:
        if os.path.isdir(os.path.join(t.root, tickets_rel.replace("/", os.sep))):
            return True
    return False


def _stray_claude_dirs(root: str, worktree_names: set[str]) -> list[str]:
    """リポジトリの中で `.claude/` を持つ、ワークツリーでもワークスペースルートでもない
    ディレクトリ。

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
    problems.extend(_bin_path(root, env.get(settings.BIN_ENV)))
    return problems


def _bin_path(root: str, declared: object) -> list[Problem]:
    """`.claude/settings.json` の env の実行ファイルの綴りを見る（設計 launcher-scripts 9 節）。

    プロセスの環境ではなく設定ファイルを読む。hook が起動するのは、ここに書いた綴り
    （`"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"`）だから。

    - 指す先が在るのに実行できなければ error。hook は sh を直に起動するので 126 で起動せず、
      判定そのものが動いていない。無いときは言わない。組み立ての前や、利用者ごとの設定で
      別の綴りを渡している形があり、無いことは selfguard が missing と言う。
      Windows では実行ビットを持たないので見ない
    """
    if not isinstance(declared, str) or not declared:
        return []
    problems: list[Problem] = []
    full = declared if os.path.isabs(declared) else os.path.join(root, declared)
    if os.name != "nt" and os.path.isfile(full) and not os.access(full, os.X_OK):
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "(project)",
                f"{PROJECT_SETTINGS} の env の {settings.BIN_ENV}={declared} は在るが実行できない。"
                "hook が起動しないので何も判定していない。実行ビットを付ける"
                f"（chmod +x {declared}）か、scripts/ccnavi-setup.sh を打ち直す",
            )
        )
    return problems


def _rules(path: str, root: str = "", home: str = "", layer: bool = False) -> list[Problem]:
    """ルールファイルを、判定が読むのと同じ読み方で読んで検証する。

    rules.load をそのまま呼ぶ。別の読み方をすると、検証は通ったのに実運用で
    落ちるという、検証があるぶんかえって危ない形になる。

    `home` は、ルールが指すファイル（additionalContextFile）を探す起点。層の
    ルールならその層の git プロジェクトルート。省けばワークスペースルート、
    それも無ければルールファイルの隣。

    `layer` は共通層より後ろの層かどうか。層は共通層に足すものなので、`deny` や
    `allow` が空でも穴にはならない（空の層 = 何も足さない）。共通層に掛けている
    「空のガードは入っていないのと同じ」の問いを、層にまで広げると、allow を
    1 件だけ足した層が毎回 error を出し続けることになる。
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
                "`deny` が空。何も止めないガードは、入っていないガードと"
                "同じでありながら、入っているように見える",
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
                    f"{key} の {rel} が無い。ワークツリーにもルートにも無ければ何も足さない",
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

    problems.extend(_every_problems(rule, name))

    # once の文は文脈ごとに 1 度しか積まれず、every > 1 は刻んだ回にしか積まれないので、
    # どちらも広さを咎めない。読めない every は 1（毎回渡る）に倒れているので、この式は
    # 素通りしない（rules.readable_every）。
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
                    "当たるたびに同じ文がコンテキストに積まれる。狭いルールに分けて書く",
                )
            )
    return problems


def _every_problems(rule: rules.Rule, name: str) -> list[Problem]:
    """`every`（渡す回の刻み）の値と、刻んだ先に渡すものがあるか。

    読めない値は error。判定は 1（毎回渡す）に倒して通すので、黙っていると書いた人は
    刻んだつもりのまま毎回渡ることになる。`every: 1` は既定値を明示しただけなので
    何も言わない。
    """
    if rule.every_written is None:
        return []
    if not rules.readable_every(rule.every_written):
        return [
            Problem(
                SEVERITY_ERROR,
                name,
                f"every の {rule.every_written!r} は刻みとして読めない。"
                "1 以上の整数で書く。このまま置くと刻まず毎回渡る",
            )
        ]
    # 刻むのは渡す回で、渡すものが無ければ刻んでも何も起きない。文が無くても
    # 本文（...File）は渡るので、4 つのどれか 1 つでもあれば咎めない。
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
                "every があるのに渡すものが無い。刻んでも何も渡らない。"
                "additionalContext（または additionalContextOnce・…File）を書くか、"
                "every を消す",
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

"""設定とルールの検証。判定を行わずに、防御を無効化しうる記述だけを報告する。

判定の経路は、苦情を言うために設定を読むわけではない。判定のついでに気づいたことを
標準エラーに出しているだけなので、ルールを書き換えた直後、CI、入れたばかりの
プロジェクトのように判定が走らない場面では、不備を見つける手段が無い。ここがその経路になる。

急ぐ理由は失敗したときの影響にある。ルールファイルが読めないと、block モードでは
すべてのツール呼び出しが拒否になる。そのファイルを直すための呼び出しも
止まるので、読めなくなってから気づいたのでは直せない。だから読めなくなる前に言う側が要る。

深刻度の分け方は 1 つの原則で決めてある。

* error はガードが働かない、あるいは働きすぎて全部を止める記述。放っておくと
  防御が消えるか、セッションで何もできなくなる。CI が落とす対象はここだけでよい
* warn は判定そのものは動くが、書いたユーザが意図した防御が働いていない記述。
  直さなくても今のところ困ることは起きないが、守っているつもりの穴が開いている

## 設計からの読み替え

ccnavi.md 付録 D.2「診断コマンド」の `--lint` と D.4「設定lintの検証項目」は、
config.yaml という 1 枚の設定ファイルに、ツールの許可・保護領域・
禁止コマンドがまとめて書かれている前提で書かれている。現在の形はそうではない。
設定は `.claude/settings.json` の env で渡す環境変数、防御の中身はルールファイルで、
パスの列挙という考え方そのものが無い。そこで D.4 の 9 項目を次のように読み替えた。

| D.4 の項目 | 現在の形での読み替え | 深刻度 |
|---|---|---|
| 3 正規表現がコンパイル可能か | regex / pattern を組み立てられるか | error |
| 6 tools セクションが存在するか | ルールが 1 件でも組み上がるか | error |
| 1,2,4 禁止値・`..`・絶対パス | 該当する列挙が無い。代わりに版番号と必須欄 | error |
| 5 max_* が既定より緩くないか | 呼び出しを止めないモードと読めない値 | warn |
| 8 重複していないか | id の欠落と重複 | warn |
| 7,9 保護領域・immutable の漏れ | どのツールにも当たらない match | warn |

読み替えても変わらないのは、CI で走らせて error だけを落とす対象にするという
D.4 の使い方のほうで、終了コードはそれに合わせてある。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
from dataclasses import replace
from typing import TextIO

from ..hook import judge
from ..infra import fsio, gitcmd, gitstate, hookio, modes, settings, tree
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import ctxfile, ruleload, rules, selfguard
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, Problem
from ..tickets import (
    agree,
    approval,
    configsync,
    flow,
    history,
    phase,
    phasetypes,
    review,
    risk,
    syncstate,
)
from ..tickets import ticket as ticket_mod
from . import version

# Claude Code の設定ファイル。ccnavi 自身はこのファイルを読まない。ここに書かれた
# env は Claude Code がプロセスに渡し、ccnavi はそれを環境変数として受け取る。
# 検証だけがこのファイルを直接見る。作業ツリーの中にあってエージェントが書き換えられる
# ファイルに、防御を無効化する値が書かれていないかを確かめられるのはここだけだから。
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
    as_json: bool = False,
    flow_path: str = "",
) -> int:
    """検証の結果を書き、error が 1 件でもあれば非ゼロを返す。

    as_json なら README「lint の JSON」の形で 1 つの JSON を書く。VS Code 拡張が
    プロジェクトごとの warn を拾うための形で、ユーザ向けの文面は書き換えてよいが、
    この JSON の形は拡張との契約になる。

    書き先は標準出力にしてある。この経路は hook の payload を読まないので、
    判定を渡すための標準出力が空いている。CI がそのまま拾える側に出す。
    """
    # モードの解決は判定と同じ関数に任せる。ここで別に書くと、検証は通ったのに
    # 実運用では違うモードになる、という一番まずい形になる。苦情を標準エラーへ
    # 書く作りなので、受け皿を渡して拾い、こちらで深刻度を付け直す。
    complaints = io.StringIO()
    mode = modes.resolve_mode(complaints, flag, conf)

    # 自動復元も同じ理由で同じ関数に任せる。受け皿を分けるのは、苦情の出所を
    # 取り違えないため。どちらの設定について言われたのかが区別できないと、
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
    flow_data = None
    flow_rendered = None
    flow_candidates = flow.catalog(root) if flow_path else {}
    if flow_path:
        flow_said, flow_data, flow_rendered = flow_problems(flow_path, root, flow_candidates)
        problems.extend(flow_said)
    problems += [
        Problem(SEVERITY_WARN, "(restore)", line.removeprefix("ccnavi: "))
        for line in said.getvalue().splitlines()
    ]
    problems += [
        Problem(SEVERITY_WARN, "(unwatched)", line.removeprefix("ccnavi: "))
        for line in unwatched_said.getvalue().splitlines()
    ]
    # この門に dry-run は無い。書いたユーザは「止めずに報告する」つもりでいるのに、
    # 実際は enable と同じに止める。設定ファイルを読んだだけでは、その食い違いが
    # どこにも現れない。warn ではなく error にするのは、直すまで意味が変わらない、
    # つまり直さないと設定ファイルの書き方と実際の動きが食い違ったままになるため。
    declared = (conf.guard_ticket_approval_declared or "").strip().lower()
    if declared and declared not in selfguard.GATE_SETTINGS:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "(ticket)",
                f"{settings.GUARD_TICKET_APPROVAL_ENV}={declared}。この設定は "
                f"{' か '.join(selfguard.GATE_SETTINGS)} しか受け付けない"
                "（承認は一度通れば済んでしまうので、止めずに報告するだけの値が無い）。"
                f"今は {selfguard.ENABLE} として動いている",
            )
        )
    # チケット制御も同じ 2 値。切ったつもりの書き誤りは enable として動いているので、
    # 書いたユーザが「切れている」と思い続けないよう error にする。
    declared = (conf.ticket_control_declared or "").strip().lower()
    if declared and declared not in selfguard.GATE_SETTINGS:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "(ticket)",
                f"{settings.TICKET_CONTROL_ENV}={declared}。"
                f"{' か '.join(selfguard.GATE_SETTINGS)} しか受け付けない。"
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
                f"{' と '.join(judge.PERMISSION_NO_JUDGE)} のモードでは、"
                "どのルールも言及しない呼び出しを止めない",
            )
        )
    if conf.tickets_enabled and conf.guard_ticket_approval == selfguard.DISABLE:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{settings.GUARD_TICKET_APPROVAL_ENV}=disable。"
                "エージェントが ccnavi の実行ファイルを直接打って、"
                "承認・レビュー済みの受け入れ・状態の移動を行える",
            )
        )
    # 戻す働きの 2 つも同じ扱いにする。ユーザ向けの本文には値が 1 行ずつ出ているが、
    # `problems` に入らないと `--json` を読む側（CI と VS Code の拡張）からは
    # 「揃っている」と見える。切ってあること自体は設定として正しく、それでも言う
    # 理由は上の 2 つと同じ（外から見て、守られている状態と区別が付かない）。
    problems.extend(_gate(settings.GUARD_CORE_FILES_ENV, guard_core_files, mode, _CORE_FILES_VOICE))
    problems.extend(_gate(settings.RESTORE_IF_DENY_ENV, restore_if_deny, mode, _RESTORE_VOICE))

    errors = sum(1 for p in problems if p.severity == SEVERITY_ERROR)
    warns = sum(1 for p in problems if p.severity == SEVERITY_WARN)
    # info は数えるが、終了コードには影響しない。層をまたいだ重複のように「そう
    # 書いてあるとおりに働いているが、書いたユーザが知りたいはずのこと」が入る。
    infos = sum(1 for p in problems if p.severity == SEVERITY_INFO)
    if as_json:
        payload = {
            "version": LINT_VERSION,
            "root": root,
            "rules": conf.rules,
            "mode": mode,
            "ticket_control": conf.ticket_control,
            "projects": [t.name for t in tree.projects(conf.projects)],
            "problems": [
                {"severity": p.severity, "where": p.rule, "detail": p.detail} for p in problems
            ],
            "errors": errors,
            "warns": warns,
            "infos": infos,
        }
        if flow_path:
            # 実行ファイルが読んだ中身。拡張のフロー編集画面は自分の読みとこれを見比べる
            # （README「lint の JSON」）。`rendered` は SubagentStart で渡る手順の行（読めなければ
            # null）、`candidates` はフローで選べるサブエージェントとスキルの名前
            payload["flow"] = {
                "path": flow_path,
                "data": flow_data,
                "rendered": flow_rendered,
                "candidates": flow_candidates,
            }
        stdout.write(json.dumps(payload, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_ERROR if errors else EXIT_OK

    stdout.write("ccnavi: 設定を検証する\n")
    stdout.write(f"  ルール: {conf.rules}\n")
    if flow_path:
        stdout.write(f"  フロー: {flow_path}\n")
    stdout.write(f"  deny の場所を戻す: {_shown(restore_if_deny, mode)}\n")
    stdout.write(f"  コアファイルを守る: {_shown(guard_core_files, mode)}\n")
    stdout.write(f"  確認できる者が居ないモードで守る: {guard_unwatched}\n")
    stdout.write(f"  チケット制御: {conf.ticket_control}\n")
    if conf.tickets_enabled:
        stdout.write(f"  チケットの承認の経路を守る: {conf.guard_ticket_approval}\n")
    # 環境変数はこの起動が受け取ったものであって、セッションが受け取るものではない。
    # 端末から打った検証と hook から届く環境は違うので、どちらを見た結果なのかを
    # 名乗らせる。名乗らないと、通った検証が別の設定についての報告になる。
    stdout.write(f"  モード: {mode}（この起動の環境から解決したもの）\n")

    for problem in problems:
        stdout.write(f"{problem}\n")

    stdout.write(f"error {errors} 件、warn {warns} 件、info {infos} 件\n")
    return EXIT_ERROR if errors else EXIT_OK


# 戻す働きの 2 つが、切られている・予行になっているときに言うこと。
# 3 値（enable / dry-run / disable）を取る門なので、止めない 2 つの値それぞれに文がある。
_CORE_FILES_VOICE = {
    selfguard.DISABLE: (
        "ccnavi 自身の設定ファイル（.claude/settings*.json と、共通層・自身の層・"
        "プロジェクトの層それぞれの設定 3 本）のバックアップを取らず、書き換えられても戻さない。"
        "ふだん実行前に足している組み込みの deny（実行ファイル・ccnavi ディレクトリ・共通層の"
        " 3 本）も足さないので、ワークツリー側の層の設定は、ルールファイルが名指ししていなければ"
        "書き込める"
    ),
    selfguard.DRY_RUN: (
        "ccnavi 自身の設定ファイルが書き換えられても戻さない（戻すはずだったことを報告するだけ）。"
        "実行前の deny は足したままなので、実行前に止める働きは効いている"
    ),
}
_RESTORE_VOICE = {
    selfguard.DISABLE: "`deny` と宣言した場所が副作用で変わっても戻さない",
    selfguard.DRY_RUN: (
        "`deny` と宣言した場所が副作用で変わっても戻さない（戻すはずだったことを報告するだけ）"
    ),
}


def _shown(declared: str, mode: str) -> str:
    """ユーザ向けの本文に出す値。モードによって変わるなら、そのことも書く。

    書かれた値だけを出すと、`CCNAVI_MODE=dry-run` のもとで `enable` と出る。
    読んだユーザは守られていると思い、実行時は戻らない。
    """
    effective = modes.effective_setting(mode, declared)
    if effective == declared:
        return declared
    return f"{declared}（{settings.MODE_ENV}={mode} なので実際は {effective}）"


def _gate(name: str, declared: str, mode: str, voices: dict[str, str]) -> list[Problem]:
    """守る働きを持つ門が、止めない値になっていることを言う。enable なら何も言わない。

    見るのは `CCNAVI_MODE` を反映したあとの値（`modes.effective_setting`）。書かれた値だけを
    見ると、`CCNAVI_MODE=dry-run` のもとで `enable` と書かれた門を「守っている」と読むことに
    なる。実行時はモードに従って戻さないので、それはこの検査がいちばん言うべき
    「切れているのに揃って見える」そのものになる。

    実際の値が書かれた値と違うときは、そのことも言う。言わないと、直す先が
    その門なのか `CCNAVI_MODE` なのかが読めない。
    """
    effective = modes.effective_setting(mode, declared)
    said = voices.get(effective)
    if not said:
        return []
    how = (
        f"{name}={declared}"
        if effective == declared
        else f"{name}={declared} だが {settings.MODE_ENV}={mode} なので実際は {effective}"
    )
    return [Problem(SEVERITY_WARN, "(restore)", f"{how}。{said}")]


def check(
    root: str, conf: settings.Settings, notes: list[str], mode: str, complaints: str
) -> list[Problem]:
    """防御を無効化しうる記述を数え上げる。

    notes は設定の解決が出した苦情、complaints はモードの解決が出した苦情。
    どちらも深刻度を持たない文字列で届くので、ここで付ける。
    """
    problems: list[Problem] = []

    # 上書き設定を読み飛ばしても、値は既定に戻ってガードは弱まらない。
    # ただしユーザが設定したつもりの値がどこにも使われていない状態にはなる。
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
        # ガードが働いている状態と区別が付かない。
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(mode)",
                f"{modes.DRY_RUN} なので判定はしても、呼び出しには何もしない",
            )
        )

    problems.extend(_project_settings(root))
    problems.extend(_sh_compat(root))
    problems.extend(_after(root))
    problems.extend(_rules(conf.rules, root))
    problems.extend(_phases(conf))
    problems.extend(_risk(conf, root))
    problems.extend(_ticket(conf, root))
    problems.extend(_scratch(conf, root))
    problems.extend(_projects(conf, root))
    # 層の読み込みは判定と同じ経路（ruleload.survey）を通る。読めない層の苦情は
    # そこが書く標準エラーにも出るので、受け皿で受け取って二重に言わない。
    problems.extend(_layers(io.StringIO(), conf, root))
    problems.extend(_layer_configs(conf, root))
    problems.extend(_worktree_layers(conf, root))
    problems.extend(_ticket_places(conf, root))
    problems.extend(_local_settings(root))
    problems.extend(_sync(conf, root))
    return problems


# sh が互換の版を名乗る場所。`.ccnavi/scripts/` の sh はどれもこれを `.` で読むので、
# 1 か所で足りる。
SH_COMPAT_FILE = os.path.join(".ccnavi", "scripts", "ccnavi-common.sh")
_SH_COMPAT = re.compile(r"^CCNAVI_COMPAT=([0-9]+)[ \t]*$", re.MULTILINE)


def compat_fix(root: str) -> str:
    """実行ファイルと sh が食い違ったときの直し方。ccnavi のリポジトリなら組み立て直し、
    配布先なら配り直し。配布先には組み立てる元（build.py とソース）が無い。"""
    if os.path.isfile(os.path.join(root, "build.py")) and os.path.isfile(
        os.path.join(root, "ccnavi", "__main__.py")
    ):
        return (
            "build.py を実行して組み立て直してください（uv run --with pyinstaller python build.py）"
        )
    return (
        "ccnavi のリポジトリで build.py を実行し、scripts/ccnavi-setup.sh <このワークスペース> "
        "--force で実行ファイルと sh を配り直してください"
    )


def _sh_compat(root: str) -> list[Problem]:
    """`.ccnavi/scripts/` の sh と、この実行ファイルの互換の版（version.COMPAT）が揃っているか。

    食い違っても判定は動くので warn。sh が使うフラグや出力の形が変わっていれば、sh の側で
    チケットやレビューの操作が落ちる。sh が無いワークスペース（試しの置き場）は言わない。
    層のファイルの書式の版（`version:`）は、読む側が既に error で言う。
    """
    path = os.path.join(root, SH_COMPAT_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        return [Problem(SEVERITY_WARN, "(version)", f"{path} を読めない ({exc})")]
    found = _SH_COMPAT.search(text)
    if found is None:
        return [
            Problem(
                SEVERITY_WARN,
                "(version)",
                f"{path} に互換の版（CCNAVI_COMPAT）が書かれていない。実行ファイル（互換 "
                f"{version.COMPAT}）より古い sh である。{compat_fix(root)}",
            )
        ]
    declared = int(found.group(1))
    if declared == version.COMPAT:
        return []
    return [
        Problem(
            SEVERITY_WARN,
            "(version)",
            f"sh（{path}）は互換 {declared}、実行ファイルは互換 {version.COMPAT} で食い違っている。"
            f"{compat_fix(root)}",
        )
    ]


def _risk(conf: settings.Settings, root: str) -> list[Problem]:
    """共通層のリスクの配点が読めるか。無いのは不備ではない（組み込みの配点）。

    `script:` が指す先が在ることも見る。走らせるときは「測れなかった」で重いほうに
    なるが、そこで気づくのは子を閉じる時点になる（設計 11.4.2）。
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


# `--lint --flow` の苦情の場所。VS Code 拡張のフロー編集画面はこの場所の苦情だけを読む。
FLOW_WHERE = "(flow)"


def flow_problems(
    path: str, root: str, candidates: dict | None = None
) -> tuple[list[Problem], object, list[str] | None]:
    """子のフローのファイル 1 本が、SubagentStart が読むのと同じ読みで読めるか（`--lint --flow`）。

    (苦情, 読めた中身を `flow.as_json` にしたもの, `SubagentStart` で渡る手順の行（`flow.render`）)
    を返す。読めなければ中身と行は None。
    読み手も検査も `flow.load` そのもの（大きさ、リンク・ふつうのファイルでない・ハードリンク、
    UTF-8 として読めない、YAML として読めない、別名、形）。ここで別に書くと、画面が
    「正しい」と言ったフローを SubagentStart が読めない、という食い違いになる（読みの答えは
    実行ファイルの 1 か所に置く）。読めなければ error。
    ツリーの中のファイルなら、ツリーのルートからの途中のリンクも見る（SubagentStart と同じ）。
    無いファイルも error にする（確かめたつもりで何も確かめていない形を作らない）。
    中身を返すのは、拡張が値の意味（`0755` や `yes` を何と読むか）を自分で決めずに済ませるため。

    読めたフローには、手順として怪しいところを warn で足す（読むのは止めない）。線の構造
    （`flow.structure_problems`）と、`candidates`（`flow.catalog`）を渡せばサブエージェントの種類と
    スキルの名前の表記（`flow.name_problems`）。どれも `detail` は渡したパスで始まる。
    """
    shown = flow.clean(path)
    try:
        exists = os.path.lexists(path)
    except (OSError, ValueError):
        exists = False
    if not exists:
        return [Problem(SEVERITY_ERROR, FLOW_WHERE, f"{shown}: 無い")], None, None
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root)) if root else os.pardir
    except ValueError:  # Windows でドライブが違う
        rel = os.pardir
    inside = rel != os.pardir and not rel.startswith(os.pardir + os.sep) and not os.path.isabs(rel)
    data, why = flow.load(path, root if inside else "")
    if why:
        return [Problem(SEVERITY_ERROR, FLOW_WHERE, f"{shown}: {why}")], None, None
    try:
        shaped = flow.as_json(data)
    except RecursionError:
        deep = f"{shown}: 入れ子が深すぎて中身を渡せない"
        return [Problem(SEVERITY_ERROR, FLOW_WHERE, deep)], None, None
    said = flow.structure_problems(data)
    if candidates is not None:
        said += flow.name_problems(data, candidates)
    warns = [Problem(SEVERITY_WARN, FLOW_WHERE, f"{shown}: {line}") for line in said]
    rendered, _ = flow.render(data)
    return warns, shaped, rendered


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
    """作業中の承認済みチケットを検査する。

    承認で本物とするのは置き場で、`ccnavi_approved` の欄ではない。

    置き場を動かして承認する運びでは `--agree` を通らないので、承認のときにしか
    当たらなかった検査が誰にも当たらない。判定は `blocked` の分だけを止めるが、
    止まる場所は書き込みのときで、そこで初めて知るのは遅い。ここで全部言う。

    severity は 3 通りに分かれる。

    - `blocked` は判定が止める理由なので error
    - フェーズの順序は warn。狂っていても範囲の決まり方には影響せず、判定も止めない
      （`approval.blocking_problems`）。承認のときは error だが、承認済みのものに当てるのは
      「その順で始めた」という記録で、いま止める根拠にはならない
    - 計画の形と、種類の定義が読めないことは `validate` が付けた severity のまま（error）。
      判定は止めないが、承認の画面を通っていれば起きない形なので、置き場を動かして
      承認した分の不備を CI で止める。範囲の超過だけは `validate` も warn
    """
    problems: list[Problem] = []
    pool = dict(index)
    for t in closed:
        pool.setdefault(t.ticket, t)
    resolve = _types_resolver(conf, root)
    for t in copies:
        if t.blocked:
            problems.append(Problem(SEVERITY_ERROR, "(ticket)", f"{t.ticket}: {t.blocked}"))
            continue
        types = resolve(agree.project_of(t, pool))
        complaints, overflow = agree.validate(t, pool, types)
        for p in complaints + overflow:
            problems.append(Problem(p.severity, "(ticket)", f"{t.ticket}: {p.detail}"))
        parent = pool.get(t.parent) if t.is_child else None
        if parent is not None:
            for p in phase.order_problems(root, conf, t, parent, types):
                # 承認のときは error。承認済みのものに当てるのは「その順で始めた」という
                # 記録で、いま止める根拠にはならない。
                problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {p.detail}"))
    return problems


def _types_resolver(conf: settings.Settings, root: str):
    """`project:` から、そのチケットに使う種類を引く（設計 11.4.1）。

    承認の対象の中でもチケットごとに層が違いうるので、1 つに決めずに引く形で渡す。
    読み込みは 1 層 1 回。
    """
    cache: dict[str, dict | None] = {}

    def resolve(project: str = ""):
        if project not in cache:
            cache[project] = phase.load_types(conf, root, project)
        return cache[project]

    return resolve


def _ticket(conf: settings.Settings, root: str) -> list[Problem]:
    """チケットと承認済みチケットとワークツリーが合っているかを見る（REQ-TKT-25）。

    判定に使われるのは承認済みチケットの側だけなので、ここで問うのは「使われている範囲は何か」と
    「ワークツリーと提案がそれと一致しているか」。一致していない状態は誤りでは
    ないが、書いたユーザは書いたとおりに使われていると思っている。
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
    # 置き場に残っていること自体は書いたユーザの思い違いで、言わないと他の機械へそのまま届く。
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
    preds = approval.predecessor_pool_of(copies, review, closed, proposals)
    approval.align_imported(conf, root, preds)
    # 統合先の done/ で閉じた識別子も閉じたものに数える（開いた親子のチケットでも統合先の
    # done/ は常に読む）。承認の対象から外れる（`agree.waiting`）ので、何も言わずに済ませず
    # 名指しする。
    done |= approval.integration_closed(conf, root, proposals)
    problems.extend(_proposal_problems(proposals, copies, index, closed, done, repo_of, preds))
    problems.extend(
        _branch_name_problems(
            proposals,
            copies,
            closed,
            review,
            conf.integration_branch or settings.integration_recorded(conf.state),
        )
    )
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
                "そこへ cd するだけで、別のワークスペースルートのように見える",
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
            f"承認済みチケットの置き場（{settings.APPROVED_ENV}={conf.approved}）が "
            f"ccnavi ディレクトリ（{settings.PROJECT_HOME_ENV}={conf.project_home}）の外にある。"
            "組み込みの保護が及ばないので、エージェントが承認済みチケットを書き換えられ、"
            "承認が意味を持たない",
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
    """承認で落ちるものを、承認の前に名指しする。ユーザが端末で初めて知るより早く。

    **承認と同じ関数を通す**（`agree.candidates`）。ここだけ `agree.validate` を
    当てる形にすると、順序で落ちる子（前のフェーズが閉じていない）・計画に無い番号・
    `project:` の食い違い・改版の検査が抜ける。同じ事実を数える経路が 2 本あると、片方が
    気づかないうちに弱くなる。`--agree --preview --verify` と同じ答えをここでも言う。

    範囲の超過は承認では落ちないが、判定で止まるので同じく名指しする（warn）。

    **「まだ承認できない」だけは warn にする。** 前のフェーズが閉じていない子
    （`rules.KIND_NOT_YET`）は、書いた側に直すものが無く、前が閉じれば同じ提案が通る。
    `--lint` はワークスペース全体を見る道具で、その終了コードは VS Code の設定画面が
    保存してよいかの判断にも使われる（`phases-panel.ts`）。ここを error にすると、
    編集と関わりのない提案 1 本で、設定の保存も CI も止まる。承認そのものは落とす
    （`agree.candidates` の側は error のまま）ので、緩むのは報告の重さだけ。
    """
    pending, revisions = agree.waiting(
        proposals,
        copies,
        closed,
        review,
        agree.types_resolver(conf, root, copies),
    )
    if not pending and not revisions:
        return []
    batch, rejected, _pool = agree.candidates(root, conf, pending, revisions, copies)
    problems: list[Problem] = []
    for cand in batch:
        for p in cand.complaints + cand.overflow:
            problems.append(_said(cand.ticket.ticket, p))
    for t, complaints in rejected:
        for p in complaints:
            problems.append(_said(t.ticket, p))
    return problems


def _said(ticket: str, problem) -> Problem:
    """承認の苦情 1 件を、`--lint` の言い方に直す。「まだ承認できない」は warn にする。"""
    severity = SEVERITY_WARN if problem.kind == rules.KIND_NOT_YET else problem.severity
    return Problem(severity, "(ticket)", f"{ticket}: {problem.detail}")


def _proposal_problems(
    proposals: list,
    copies: list,
    index: dict,
    closed: list,
    done: set[str],
    repo_of: dict[str, str],
    preds: dict | None = None,
) -> list[Problem]:
    """提案の側。承認待ち、先行が閉じていない着手済み、同じ識別子の重複。

    承認で落ちるものは `_approval_problems` が言う（承認と同じ関数を通す）。

    `todo/` に在るものは全部承認待ち。同じ識別子がどこかの置き場（作業中・レビュー待ち・
    閉じた）に在れば、親の改版でない限り書き損じなので名指しする。

    `closed` は `done/` の承認済みチケット。`proposals` は `todo/` と `review/`。作業中と
    レビュー待ちと閉じたの 3 つを横断して数えないと、`doing/` と `done/` に同じ識別子が
    在る形（動かす途中で止まった形跡）を CI が見逃し、状態の操作が「複数の場所にある」で
    止まって初めて知ることになる。

    **同じリポジトリの中ではツリーごとに数える。** 承認済みチケットは git に入れて運ぶので、
    切ったワークツリーの数だけ同じチケットができる。しかもワークツリーはそれぞれ別のコミットを指すので、
    古いほうで `doing`・新しいほうで `done` になるのも普通の形。ツリーをまたいだ食い違いまで
    咎めると、ワークツリーを 2 本持つだけで閉じたチケットが全部 error になり、`--lint` が
    常に非ゼロで終わる。捕まえたいのは 1 つのツリーの中で 2 つの状態に在る形だけ。

    **リポジトリをまたいだら、状態が何であれ咎める。** プロジェクトは自分の git を持つので
    （設計 11）、そこに同じ識別子が在るのは同じチケットではなく違うチケットどうしの衝突。
    識別子はユーザが選ぶ短い連番で、プロジェクトが独立に振れば重なる。コミットの遅れでは説明が付かないから、
    ツリーごとの免除を当ててはいけない。
    """
    problems: list[Problem] = []

    # ツリーと状態は分けて持つ。同じ識別子が別のツリーに在るのは普通で（承認済みチケットは
    # git に入れて運ぶので、切ったワークツリーの数だけ同じチケットができる）、しかもワークツリーは
    # それぞれ別のコミットを指すから、古いほうが doing・新しいほうが done になるのも普通。
    # 咎めるのは 1 つのツリーの中で 2 つの状態に在る形だけ。
    def place(t, state: str) -> tuple[str, str, str]:
        at = t.tree or "(ワークスペースルート)"
        return (repo_of.get(at, at), at, state)

    seen: dict[str, list[tuple[str, str, str]]] = {}
    # チケットそのものも識別子ごとに持つ。どれが本物か決まるかの判断は `ticket.collisions` が
    # 決め、ボードの `scattered` と状態の操作が止まる条件に揃える（同じ答えを 2 か所で
    # 出さない）。数えるのは `index`（識別子ごとに 1 つ）ではなく全部。同じ識別子が 2 つ
    # 残っているのがまさに言いたい形なので、引き当ての表で数えると自分でまとめてしまう。
    held: dict[str, list] = {}
    for t in copies:
        seen.setdefault(t.ticket, []).append(place(t, t.state or ticket_mod.DOING))
        held.setdefault(t.ticket, []).append(t)
    for t in closed:
        seen.setdefault(t.ticket, []).append(place(t, t.state or ticket_mod.DONE))
        held.setdefault(t.ticket, []).append(t)
    for t in proposals:
        seen.setdefault(t.ticket, []).append(place(t, t.state))
        held.setdefault(t.ticket, []).append(t)
        if t.state != ticket_mod.TODO:
            continue
        if t.ticket in index:
            current = index[t.ticket]
            if not t.is_child and t.has_plan and agree._plan_differs(t, current):
                continue  # 親の改版。承認待ちに入る
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は承認済み（{current.tree or '(ワークスペースルート)'} の "
                    f"{current.state or ticket_mod.DOING}/）なのに todo/ にも在る。"
                    "計画の改版でなければ todo/ の側を消してください",
                )
            )
            continue
        if t.ticket in done:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は閉じたかレビュー待ちなのに todo/ にも在る。"
                    "todo/ の側は承認の対象にならない。再開するには、"
                    "ユーザが承認済みチケットを戻す",
                )
            )
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{t.ticket} は承認待ち（{t.tree or '(ワークスペースルート)'} の todo/）。"
                "'ccnavi --agree' を通すまで範囲は効かない",
            )
        )
    for t in index.values():
        if t.started_at:
            # 先行の数え方は承認と着手と同じ（`done/` に在って取り消しでないものだけ満たす）。
            # 着手はこれで止まるので、ここに出るのは
            # 着手のあとに先行が動いたか、止める前の版で着手したもの。
            unmet = approval.unmet_predecessors(t, preds or {})
            if unmet:
                names = ", ".join(f"{p.ticket}（{p.label}）" for p in unmet)
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        "(ticket)",
                        f"{t.ticket} は先行 {names} を満たしていないのに着手している",
                    )
                )
    for ticket_id, places in seen.items():
        # 別のリポジトリに同じ識別子が在るのは、同じチケットではなく**違うチケットどうしの衝突**。
        # 識別子はユーザが選ぶ短い連番なので、プロジェクトが独立に振れば普通に重なる。
        # コミットの遅れでは説明できないので、状態が何であれ咎める。
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
                    "識別子はリポジトリごとに一意にしてください",
                )
            )
            continue
        # 本物とするツリーでまとめて 2 つ以上残る形（状態の操作が止まる）と、その中で 2 つの
        # 置き場に在る形（動かす途中で止まった形跡）。`todo/` に在るのは親の改版の途中なので
        # 咎めない。ツリーをまたいだチケットはまとめれば 1 つに決まるので、ここには出てこない。
        caught = ticket_mod.collisions(held[ticket_id])
        if not caught:
            continue
        where = ", ".join(
            f"{t.tree or '(ワークスペースルート)'}:{t.state}"
            for t in sorted(caught, key=lambda t: (t.tree, t.state))
        )
        problems.append(
            Problem(SEVERITY_ERROR, "(ticket)", f"{ticket_id} が複数の場所にある: {where}")
        )
    return problems


def _branch_name_problems(
    proposals: list, copies: list, closed: list, review: list, integration: str = ""
) -> list[Problem]:
    """識別子を親のブランチ名にできるか（warn だけ。拒否は `ccnavi-git.sh` が受け持つ）。

    親のブランチ名は親の識別子そのものにする。そのために次を名指しする。

    - 新規の提案（`todo/` にあって、承認済みでも閉じてもいないもの）の識別子の形
      （`ticket.branch_name_problems`）。承認済みの識別子はもう変えられないので言わない
    - 大文字小文字だけが違う識別子。Windows と macOS の既定のファイルシステムでは
      ブランチもワークツリーも同じ名前になる
    - 子の形（`<親>-<2 桁>`）に当たる親の識別子。親子のチケットを引くとき、別の親の子と読まれる

    承認と判定はまだ変えない。止めるのは後の段階で、ここで先に数を見ておく。
    `integration` はその時点の統合先の名前で、`--integration-branch` が無ければ `ccnavi-sync.sh` が
    取り込み結果に書いた名前。どちらも無ければ固定の並びだけを見る。
    """
    problems: list[Problem] = []
    everyone = list(copies) + list(closed) + list(review) + list(proposals)
    settled = {t.ticket for t in list(copies) + list(closed) + list(review)}
    said: set[str] = set()
    for t in proposals:
        if t.state != ticket_mod.TODO or t.ticket in settled or t.ticket in said:
            continue
        said.add(t.ticket)
        for text in ticket_mod.branch_name_problems(t, integration):
            problems.append(
                Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {text}（親のブランチ名の規則）")
            )

    spellings: dict[str, set[str]] = {}
    for t in everyone:
        spellings.setdefault(t.ticket.casefold(), set()).add(t.ticket)
    for names in spellings.values():
        if len(names) > 1:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{' と '.join(sorted(names))} は大文字小文字だけが違う。"
                    "大文字小文字を区別しないファイルシステムでは、ブランチとワークツリーの"
                    "名前がぶつかる（親のブランチ名の規則）",
                )
            )

    child = ticket_mod.child_pattern()
    parents = sorted({t.ticket for t in everyone if not t.is_child})
    for name in parents:
        matched = child.match(name)
        if matched is None:
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{name} は親なのに、識別子が子の形（`<親>-<2 桁>`）と一致する。"
                f"親子のチケットをまとめるとき {matched.group('parent')} の子として扱われる。"
                "親の識別子の末尾を `-<2 桁>` にしないでください（親のブランチ名の規則）",
            )
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
                        "承認済みチケットが指すリポジトリから切り直してください",
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
                    f"{tree.worktree_path(root, t.ticket)} が無い。"
                    "ワークツリーを作るまで範囲は効かない",
                )
            )
    return problems


def layer_where(name: str) -> str:
    """その層の苦情の出どころの表記。VS Code 拡張がこの前置きでプロジェクトを引く。"""
    if name == ruleload.LAYER_COMMON:
        return "(rules)"
    if name == ruleload.LAYER_SELF:
        return "(self)"
    return f"(projects/{name})"


def _layers(stderr: TextIO, conf: settings.Settings, root: str) -> list[Problem]:
    """層に食い違いが無いか（設計 11.9、REQ-MLT-16）。

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
        from_file = _rules(
            view.path,
            root,
            home=_layer_home(conf, root, view.name),
            layer=True,
            project=view.name != ruleload.LAYER_SELF,
        )
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
    """各層の phases / risk が、共通層と合成できるか（設計 11.4.1、11.4.2）。

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
    """ワークツリーの ccnavi ディレクトリに、元リポジトリに無いファイルがあるか（設計 11.6）。

    判定が読むのは元リポジトリに checkout されている版だけ（REQ-MLT-04）。
    ワークツリーの `.ccnavi/` に足したファイルは、そのブランチが統合されるまで使われない。
    使われないものを書いたユーザは、書いたとおりに使われていると思ったまま進む。統合の前に
    気づけるように、ここで名前を挙げる。

    足したファイルを咎めているのではない。設定を書き進める場所はワークツリーでよく、
    そこから統合するのも普通の手順。言うのは「今はまだ使われていない」という 1 点だけ。

    中身の違いは見ない。同じパスのファイルが両方に在れば、それは編集で、git の
    差分が拾う。ここが拾うのは、元リポジトリに無くて差分にも出ない新しいパスのほう。

    承認済みの領域（承認済みチケット・マーカー・子の記録・フロー）は数えない（ユーザの決定）。
    承認済みチケットは親のワークツリーに置かれ、判定もフローの案内もそのツリーの版を読む
    （設計 9.2・9.3.1）。「統合されるまで使われない」は当てはまらず、言えば誤った案内になる。
    """
    problems: list[Problem] = []
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("/", os.sep)
    for work in tree.worktrees(root, conf.projects):
        origin = tree.project_root(conf.projects, work.project) if work.project else root
        approved = os.path.normcase(os.path.normpath(settings.approved_dir(conf, work.root)))
        for rel in _files_under(os.path.join(work.root, home)):
            if os.path.exists(os.path.join(origin, home, rel.replace("/", os.sep))):
                continue
            where = os.path.normcase(
                os.path.normpath(os.path.join(work.root, home, rel.replace("/", os.sep)))
            )
            if where.startswith(approved + os.sep):
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


def _sync(conf: settings.Settings, root: str) -> list[Problem]:
    """取り込み状態と、取り込み済みの親子のチケットで本物とする側（親のブランチ上のチケットだけを本物とする）。

    - 親のワークツリー（名前が親の識別子）なのに HEAD が別のブランチ: warn（移行の検査）
    - 親子のチケットの取り込み状態が壊れている・gone・blocked、
      `present` なのに親のワークツリーが無い: error（その親子のチケットは決まらないので、
      承認も状態の操作も止まる）。閉じた親子のチケットは、
      親のワークツリーが残っていれば info（片付けてよい）、片付いていれば何も言わない（墓標）
    - 統合先の取り込み結果が壊れている・無い・読めない: error（識別子の再利用を確かめられない）
    - 作業ツリーの層と統合先の取り込み結果の層が違う: warn
    - `P` の上のプロジェクトの層が、統合先から計算した層と違う: warn

    親のワークツリーの外にしか無いチケット（移行の検査）は、チケットの `blocked` として
    `_copy_problems` が error で言う。取り込み状態の無い親子のチケットには、
    最初の 1 つのほかは何も言わない。
    """
    problems = _parent_trees_off_branch(conf, root)
    fams = syncstate.Families(conf, root)
    if not fams.active:
        return problems
    for repo, name in syncstate.family_names(conf.state):
        st = fams.standing(name, syncstate.project_of_key(repo))
        if not st.imported:
            continue
        where = f"(sync/{repo}/{name})"
        hint = " / ".join(syncstate.guidance(root, st))
        if st.closed:
            if st.home is not None:
                problems.append(Problem(SEVERITY_INFO, where, f"{st.stop}。{hint}"))
        elif st.stop:
            problems.append(Problem(SEVERITY_ERROR, where, f"{st.stop}。{hint}"))
        elif st.repo != syncstate.SELF and st.home is not None:
            problems.extend(_projected_layer_problems(conf, st, where))
    for repo in syncstate.repos(conf.state):
        integ = fams.integration(repo)
        if integ is None:
            continue
        where = f"(sync/{repo}/integration)"
        if integ.broken:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    where,
                    f"統合先の取り込み結果を読めない（{integ.broken}）。閉じた識別子の再利用を確かめられない"
                    "ので、このリポジトリの新規の提案は承認しない。オンラインで"
                    f" '{settings.script_command(root, 'ccnavi-sync.sh')}' を打ち直してください",
                )
            )
            continue
        home = root if repo == syncstate.SELF else tree.project_root(conf.projects, repo)
        if home and os.path.isdir(home):
            problems.extend(_layer_drift(conf, integ, home, where))
    return problems


def _parent_trees_off_branch(conf: settings.Settings, root: str) -> list[Problem]:
    """名前が親の識別子なのに、HEAD が別のブランチを指す親のワークツリー（移行の検査）。"""
    problems: list[Problem] = []
    for work in tree.worktrees(root, conf.projects):
        if not _holds_parent(conf, work):
            continue
        branch = tree.branch_of(work.root)
        if branch == work.name:
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                f"({tree.WORKTREES_DIR.replace(os.sep, '/')}/{work.name})",
                f"親 {work.name} のワークツリーが {branch or '（ブランチの外）'} の上に居る。"
                f"親のブランチの名前は識別子（{work.name}）と同じで、取り込み（ccnavi-sync.sh）と"
                "本物とする側の検査は同じ名前のブランチだけを見る。"
                "親が閉じるのを待ってから、ブランチを切り替えてください",
            )
        )
    return problems


def _holds_parent(conf: settings.Settings, work: tree.Tree) -> bool:
    """そのツリーに `ticket: <ツリーの名前>` の親の承認済みチケットか提案があるか。

    sh の `ccnavi_parent_tree` と同じ見方。
    """
    approved = settings.approved_dir(conf, work.root)
    proposals = os.path.join(work.root, conf.tickets.replace("/", os.sep))
    for path in (
        approval.copy_path(approved, work.name),
        approval.closed_path(approved, work.name),
        os.path.join(proposals, ticket_mod.TODO, f"{work.name}.md"),
        os.path.join(proposals, ticket_mod.REVIEW, f"{work.name}.md"),
    ):
        if not os.path.isfile(path):
            continue
        t, _ = ticket_mod.load(path)
        if t is not None and t.ticket == work.name and not t.parent:
            return True
    return False


def _layer_files(conf: settings.Settings, home_rel: str) -> list[tuple[str, str]]:
    """比べる層のファイル（種類, ツリーからの相対 "/" 区切り）。共通層と自身の層。"""
    config = f"{home_rel}/{settings.LAYER_CONFIG_DIR}"
    out = []
    for kind in settings.LAYER_KINDS:
        name = settings.LAYER_FILE_NAMES[kind]
        out.append((kind, f"{config}/{name}"))
    return out


def _common_rel(kind: str) -> str:
    return f".ccnavi/common/{settings.LAYER_FILE_NAMES[kind]}"


def _same_text(a: bytes | None, b: bytes | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def _read_plain(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _layer_drift(
    conf: settings.Settings, integ: syncstate.Integration, home: str, where: str
) -> list[Problem]:
    """作業ツリーの層と、統合先の取り込み結果の層（同じパス）が違うか。

    手元の判定は作業ツリーの層を読み、統合先の取り込み結果へは切り替えない（統合先の層が作業ツリーより
    緩いときに通るものが増えるため）。違いは、
    統合先に入るまで他の機械と Chrome の判定に使われないという知らせ。
    """
    home_rel = fsio.slashed(conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")
    rels = [rel for _, rel in _layer_files(conf, home_rel)]
    if integ.repo == syncstate.SELF:
        rels = [_common_rel(kind) for kind in settings.LAYER_KINDS] + rels
    problems: list[Problem] = []
    for rel in rels:
        snapshot, why = integ.file(rel)
        if why:
            problems.append(
                Problem(SEVERITY_ERROR, where, f"統合先の取り込み結果を読めない（{why}）")
            )
            continue
        local = _read_plain(os.path.join(home, *rel.split("/")))
        if _same_text(local, snapshot):
            continue
        if local is None:
            how = "作業ツリーに無い"
        elif snapshot is None:
            how = "統合先に無い"
        else:
            how = "中身が違う"
        problems.append(
            Problem(
                SEVERITY_WARN,
                where,
                f"作業ツリーの {rel} が統合先（{integ.branch or '?'}）の取り込み結果と違う"
                f"（{how}）。"
                "統合先に取り込まれるまで、他の機械と Chrome の判定には使われない",
            )
        )
    return problems


def _projected_layer_problems(
    conf: settings.Settings, st: syncstate.Standing, where: str
) -> list[Problem]:
    """`P` の上のプロジェクトの層が、統合先から計算した層と違うか。

    Chrome の判定は `P` の上の層を読まず、この計算した層を使う（`P` の上で層を書き換えて
    承認やレビューを不要にできないように）。

    計算した層は「プロジェクトの統合先の層（取り込み結果）に、ワークスペースの統合先の共通層（取り込み結果）を
    `configsync.projected` でコピーしたもの」。着手のときの configsync と同じく、共通層にある
    ファイルだけをコピーし、無いファイルはプロジェクトの側を残す。
    """
    selfinteg = syncstate.integration(conf.state, syncstate.SELF)
    projinteg = syncstate.integration(conf.state, st.repo)
    if selfinteg is None or projinteg is None or selfinteg.broken or projinteg.broken:
        return []
    home_rel = fsio.slashed(conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")
    problems: list[Problem] = []
    for kind, rel in _layer_files(conf, home_rel):
        common, why = selfinteg.file(_common_rel(kind))
        if why:
            continue
        if common is not None:
            expected: bytes | None = configsync.projected(conf, kind, common)
        else:
            expected, why = projinteg.file(rel)
            if why:
                continue
        actual = _read_plain(os.path.join(st.home.root, *rel.split("/")))
        if _same_text(actual, expected):
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                where,
                f"親のブランチ {st.family} の上のプロジェクトの層（{rel}）が、統合先の層と"
                "共通層から計算した層と違う。"
                "判定は親のブランチの上の層を読まない。"
                "統合先で直すか、着手のときにもう一度コピーしてください",
            )
        )
    return problems


def family_check(
    conf: settings.Settings, root: str, family: str, repo: str | None = None
) -> list[Problem]:
    """取り込みの後の検査（`ccnavi sync check <P> [<リポジトリ>]`）。

    この親子のチケットの承認済みチケットを判定し直し（C3）、本物とする側の検査（親のワークツリーの外のチケット・
    決まらない）とあわせて、止める理由（error）を返す。層の食い違いは warn で返す。
    error があれば sh が親子のチケットの取り込み状態を `blocked` にする。
    `repo` は取り込み状態の名前（`self` かプロジェクト名）で、sh が渡す。
    無ければ取り込み状態のあるリポジトリを全部探す。
    """
    fams = syncstate.Families(conf, root)
    project = None if repo is None else syncstate.project_of_key(repo)
    st = fams.standing_any(family, project)
    copies, _ = approval.scan(conf, root)
    closed, _ = approval.scan(conf, root, closed=True)
    review, _ = approval.scan_review(conf, root)

    def ours(t: ticket_mod.Ticket) -> bool:
        return (t.parent or t.ticket) == family and syncstate.repo_key(t.project) == st.repo

    mine = [t for t in copies if ours(t)]
    index = approval.by_id(copies)
    problems: list[Problem] = []
    for t in mine:
        # チケットごとに判定し直し、error がどのチケットのものかを構造で持つ（文面の書式に頼らない）
        found = [
            p
            for p in _copy_problems(root, conf, [t], index, closed)
            if p.severity == SEVERITY_ERROR
        ]
        written = _written_by_chrome(conf, t) if found else None
        if written is not None:
            found = [
                replace(
                    p, detail=f"Chrome {written} と手元 {version.VERSION} で判定が違う: {p.detail}"
                )
                for p in found
            ]
        problems.extend(found)
    # 親のワークツリーの中の読めないチケットも止める理由（読めないチケットは並びに入らないので、
    # 判定し直しの対象から気づかないうちに落ちる）。
    if st.home is not None:
        for _, message in approval.unreadable_copies(conf, st.home.root):
            problems.append(Problem(SEVERITY_ERROR, "(ticket)", message))
    for t in review:
        if ours(t) and t.blocked:
            problems.append(Problem(SEVERITY_ERROR, "(ticket)", f"{t.ticket}: {t.blocked}"))
    where = f"(sync/{st.repo}/{family})"
    if st.imported and st.stop and not st.closed:
        problems.append(Problem(SEVERITY_ERROR, where, st.stop))
    if st.imported and not st.stop and st.repo != syncstate.SELF and st.home is not None:
        problems.extend(_projected_layer_problems(conf, st, where))
    return problems


def _written_by_chrome(conf: settings.Settings, t: ticket_mod.Ticket) -> str | None:
    """この承認済みチケットを最後に書いたのが Chrome 拡張なら、その拡張の版。
    そうでなければ None。

    履歴の最後の行が Chrome（`via: chrome`）の承認か改版のときだけ Chrome が書いたとみなす。
    その後に手元の
    操作（着手・マーカーなど）の履歴があれば、違いが手元の操作から来ることもあるので名指ししない。
    互換の版が同じなら Chrome と手元は同じ答えを出すはずで、違えば不具合として版を名指しする
    （止めるかどうかは変えない。親子のチケットの取り込み状態を `blocked` にするのは sh）。
    """
    events, _ = history.read(settings.approved_dir(conf, t.tree_root), t.ticket)
    if not events:
        return None
    last = events[-1]
    if last.get("via") != history.VIA_CHROME or last.get("kind") not in (
        history.KIND_APPROVED,
        history.KIND_REVISED,
    ):
        return None
    return str(last.get("version") or "?")


def _files_under(base: str) -> list[str]:
    """base の下のファイルを、base からの相対（"/" 区切り）で並べる。順は文字列の順。"""
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

    実行前チェックはチケットの範囲を `scratchpad/` に当てない（`ticket.is_scratch_place`）。外して
    よい根拠は「git が追跡しないので統合先のブランチに乗らない」ことの 1 つだけ。

    **この警告で穴が無くなるわけではない。** 根拠が崩れた場合は、実行後チェックと
    サブエージェント終了時チェックが `scratchpad/` の変更を範囲外として報告する（`is_unscoped` の
    説明）。ここが言うのは、その報告が出はじめる前にユーザが気づけるようにするため。

    問うのは 2 つ。**追跡されているファイルが既にあるか**（`git ls-files`）と、これから
    書くものが追跡されるか（`git check-ignore`）。前者だけでは、まだ何も置いていない
    リポジトリで見逃す。後者だけでは、`/{SCRATCH}/` を足す前から追跡されていたファイルを
    見逃す（`.gitignore` は既に追跡されているファイルを対象にしない）。

    見るのはワークスペースと各プロジェクトのそれぞれの git。プロジェクトは自分の
    `.gitignore` を持つので、ワークスペース側の 1 行はそこまで及ばない。ワークツリーは見ない。
    あちらはブランチごとに中身が変わるうえ、実行時に実行後チェックとサブエージェント
    終了時チェックが拾うので、ここで数え上げると同じことを 2 度言うことになる。

    チケット制御が切れているときは言わない。そのとき範囲の判定自体が動かない。

    git が無ければ何も言わない。無いものを「入っていない」と報告すると、正しい設定に
    苦情を出すことになる。
    """
    if not conf.tickets_enabled:
        return []
    problems: list[Problem] = []
    # 名乗るのは `(scratch)` の側。プロジェクトのぶんも `(projects/<名前>)` とは名乗らない。
    # あちらはその層の設定についての苦情で、ここは追跡の話。同じ前置きにすると、
    # 「層について何も言わない」ことを見ているテストや読み手に、別の話が入り込む。
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
                f"{why}。実行前チェックはチケットの範囲をここに当てないので、追跡されて"
                "いると、承認した範囲の外のファイルがコミットに入りうる。入ったぶんは"
                "実行後チェックとサブエージェント終了時チェックが範囲外として報告するので、"
                "下書きのたびに範囲外と報告される。"
                f"このリポジトリの `.gitignore` に `/{place}/` を足して追跡から外すか"
                "（プロジェクトのリポジトリに運用の痕跡を残したくないなら"
                f" `.git/info/exclude` でもよい）、`{place}/` を使わずにチケットの範囲の"
                "中で作業してください",
            )
        )
    return problems


def _tracked(root: str, rel: str) -> str:
    """その置き場の下で git が追跡しているファイル 1 本。無ければ空。

    `.gitignore` は既に追跡されているファイルを対象にしないので、無視の設定だけを見ても
    「追跡されていない」は言えない。索引に何が入っているかを直接問う。
    """
    rc, out = gitcmd.output(root, ["ls-files", "--", rel + "/"], gitstate.TIMEOUT_SECONDS)
    if rc != 0:
        return ""
    first = out.strip().splitlines()
    return first[0] if first else ""


def _projects(conf: settings.Settings, root: str) -> list[Problem]:
    """プロジェクトの置き場が正しい形か（REQ-MLT-16）。

    置き場が無いのは不備ではない。あるなら、ワークスペースの git で無視されていること、
    予約名（`common` / `self`、表記違いも含む）を使っていないこと、プロジェクトが
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
                "持つので、ワークスペースの `.gitignore` に入れてください",
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
                    f"{reserved} は層の名前として予約してある（`{settings.LAYER_COMMON}` は共通層、"
                    f"`{settings.LAYER_SELF}` はワークスペース自身の層）。このプロジェクトは"
                    f"層として数えていない（id の `{p.name}:` がどちらの層を指すか決まらないため。"
                    "大文字小文字の違いは問わない）。ここに置いた宣言は 1 件も効いておらず、"
                    "このプロジェクトを行き先にするパスを持つツール（Read / Grep / Glob / Write / "
                    "Edit / NotebookEdit）は共通層だけで判定している。"
                    "プロジェクトを別の名前に変えてください",
                )
            )
        if os.path.isdir(os.path.join(p.root, ".claude")):
            # 文面の先頭「.claude/ を持つ」は VS Code 拡張（vscode-extension/ccnavi-board の
            # projects-render.ts）が同じ事象の説明と重ねないために見ている。変えるならそちらも直す
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    where,
                    ".claude/ を持つ。Claude Code がそこのスキルを読み込み、そこへ cd するだけで"
                    "別のルートのように見える。"
                    f"プロジェクトの設定は {conf.project_home}/ に置いてください",
                )
            )
    return problems


def _ticket_places(conf: settings.Settings, root: str) -> list[Problem]:
    """走査されないチケットの置き場が残っていないか（REQ-MLT-16）。

    提案の置き場はどのツリーでも同じ相対（`wip/proposals/`）で、プロジェクト向けはその
    プロジェクトのツリーに置く（設計 11.5、REQ-MLT-14）。ワークスペースの
    `wip/<名前>/proposals/` は、名前が `projects/` に在っても在らなくても走査されない。走査
    されない置き場は、提案があっても画面にもボードにも出ない。気づかないうちに消えるのが
    いちばん困るので名指しし、名前が在るなら正しい置き場を案内する。error にはしない。
    判定は動いている。
    """
    parts = [p for p in conf.tickets.split("/") if p]
    if len(parts) < 2:
        # 名前を挟む位置が置き場のパスから決められない。言えないことは言わない。
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
            why = f"プロジェクト向けの提案はそのプロジェクトの側 {proper}/ に置いてください"
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
                    f".ccnavi/scripts/{name} が無い。レビュー済みのマーカーが置かれるまで"
                    "シェル実行を止めている間、例外として通るはずのこの sh の操作もできなくなる",
                )
            )
    return problems


def tree_has_tickets(root: str, tickets_rel: str) -> bool:
    """ワークスペースルートかワークツリーのどこかに提案の置き場があるか。"""
    for t in [tree.main_tree(root), *tree.worktrees(root)]:
        if os.path.isdir(os.path.join(t.root, tickets_rel.replace("/", os.sep))):
            return True
    return False


def _stray_claude_dirs(root: str, worktree_names: set[str]) -> list[str]:
    """リポジトリの中で `.claude/` を持つ、ワークツリーでもワークスペースルートでもない
    ディレクトリ。

    浅くしか見ない。2 段まで。深くたどると大きなリポジトリで検証が待たされる。
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
    """実行後チェックが実際に動く形になっているかを見る。

    どちらも error にしない。実行前チェックは動いているので、防御が消えている
    わけではない。それでも言う。宣言した保護領域が、引数に現れない書き込みに
    対しては 1 つも守られていない状態は、外から見ると守られている状態と
    区別が付かない。

    見に行き方は判定と同じ。別の見方をすると、検証は通ったのに実運用では
    何も見えない、という一番まずい形になる。
    """
    registered = _registered(root)
    if registered is None:
        # 設定ファイルが無い、あるいは読めない。どこに登録されているかを
        # 言えないので、登録についても、そのチェックが見る先についても何も言わない。
        # 読めないこと自体は _project_settings が言う。
        return []

    if not registered:
        # 登録されていないなら、見る先の話はしない。走らないチェックに対して
        # 「見えない」と言っても、直す先が 2 つあるように読めるだけになる。
        return [
            Problem(
                SEVERITY_WARN,
                "(project)",
                f"{PROJECT_SETTINGS} の PostToolUse に ccnavi が登録されていない。"
                "実行後チェックは走らないので、ビルドの副作用やスクリプトが内部で開いた"
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
                f"作業ツリーを読めない（{unreadable}）ので実行後チェックは何も検知しない。"
                "実行前チェックはこれまでどおり動く",
            )
        )
    return problems


def _registered(root: str, event: str = hookio.POST_TOOL_USE) -> bool | None:
    """そのイベントに ccnavi が登録されているか。設定ファイルが無ければ None。

    コマンド文字列に名前が含まれるかどうかで見る。実行ファイルの置き場も
    呼び出し方もプロジェクトごとに違うので、表記を決め打ちにはできない。

    無いときに「登録されていない」と言い切らないのは、hook をここ以外
    （ユーザごとの設定）に書くことができ、そちらはこの検証から見えないため。
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
    次のセッションから使われる。ccnavi は環境変数しか読まないので、ここに書かれた
    off が「ユーザがセッションを起動するときに渡した off」と見分けのつかない形で届く。
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
                    "止めるならセッションを起動する側の環境から渡してください",
                )
            )
        elif normalized not in (modes.DRY_RUN, modes.ENABLE):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(project)",
                    f"{PROJECT_SETTINGS} の env の {settings.MODE_ENV}={declared!r} は"
                    f"モードとして読めない。{modes.ENABLE} として扱う",
                )
            )
    problems.extend(_bin_path(root, env.get(settings.BIN_ENV)))
    return problems


# `.claude/settings.local.json` に置かせない、承認と判定に影響する値。
# 置き場のパス・プロジェクトの置き場・ccnavi ディレクトリ・state の置き場（取り込み状態を読む先）・
# チケット制御と承認の保護の切り替え・動作モード。例外は統合先の名前だけ（リポジトリに置かず、
# 手元では環境変数で持つと決めた値なので）。
LOCAL_SETTINGS = settings.LOCAL_CLAUDE_SETTINGS
#
# 保護と判定の働きを変える値（戻す働き・ccnavi 自身の設定の保護・確かめられないモードの止め・
# 同じ理由の拒否の数え方・記録の置き場）も入れる。手元だけで切ると、ユーザが端末で打つ sh と
# 他の機械で、同じ親子のチケットに掛かる保護が別になる。入れないのは、判定の答えを変えない
# 次の値だけ。
# 実行ファイルのパス（`CCNAVI_BIN_PATH`。hook の起動のために手元で差し替える。README の
# 案内）、診断ログ（`CCNAVI_LOG_LEVEL` など）、見張りと待ちの秒（`CCNAVI_*_TIMEOUT`・
# `CCNAVI_LOCK_WAIT`）。
_LOCAL_FORBIDDEN = (
    settings.TICKETS_ENV,
    settings.APPROVED_ENV,
    settings.PROJECTS_ENV,
    settings.PROJECT_HOME_ENV,
    settings.STATE_ENV,
    settings.LOG_ENV,
    settings.TICKET_CONTROL_ENV,
    settings.GUARD_TICKET_APPROVAL_ENV,
    settings.GUARD_CORE_FILES_ENV,
    settings.GUARD_UNWATCHED_ENV,
    settings.RESTORE_IF_DENY_ENV,
    settings.DENY_REPEAT_ENV,
    settings.MODE_ENV,
)


def _local_settings(root: str) -> list[Problem]:
    """`.claude/settings.local.json` の env に、承認に影響する値が無いかを見る。

    承認と判定は、置き場のパスなどを統合先（リポジトリに乗る設定）と揃えて読む前提で組む。
    手元だけのファイルに置いた値は Claude Code が起こしたプロセスにだけ使われ、Chrome と
    ユーザが端末で打つ sh には使われないので、同じ親子のチケットを別のパスで読むことになる。
    例外は `CCNAVI_INTEGRATION_BRANCH` だけ（統合先の名前はリポジトリに置かず、手元では環境変数で
    持つと決めた）。
    """
    path = os.path.join(root, LOCAL_SETTINGS)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        return [Problem(SEVERITY_WARN, "(project)", f"{LOCAL_SETTINGS} を読めない: {exc}")]
    env = data.get("env") if isinstance(data, dict) else None
    if not isinstance(env, dict):
        return []
    return [
        Problem(
            SEVERITY_ERROR,
            "(project)",
            f"{LOCAL_SETTINGS} の env に {name} がある。承認と判定に効く値は手元だけの"
            "ファイルに置かない（Chrome と端末の sh には使われないので、"
            "同じ親子のチケットを"
            "プロセスごとに別のパスで読むことになる）。"
            f"{PROJECT_SETTINGS} に置いてコミットするか、セッションを起動する側の環境から"
            "渡してください。"
            f"ここに置けるのは {settings.INTEGRATION_ENV} だけ",
        )
        for name in _LOCAL_FORBIDDEN
        if name in env
    ]


def _bin_path(root: str, declared: object) -> list[Problem]:
    """`.claude/settings.json` の env の実行ファイルのパスを見る（設計 launcher-scripts 9 節）。

    プロセスの環境ではなく設定ファイルを読む。hook が起動するのは、ここに書いたパス
    （`"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"`）だから。

    - 指す先が在るのに実行できなければ error。hook は sh を直に起動するので 126 で起動せず、
      判定そのものが動いていない。無いときは言わない。組み立ての前や、ユーザごとの設定で
      別のパスを渡している形があり、無いことは selfguard が missing と言う。
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
                f"（chmod +x {declared}）か、scripts/ccnavi-setup.sh を打ち直してください",
            )
        )
    return problems


def _rules(
    path: str, root: str, home: str = "", layer: bool = False, project: bool = False
) -> list[Problem]:
    """ルールファイルを、判定が読むのと同じ読み方で読んで検証する。

    rules.load をそのまま呼ぶ。別の読み方をすると、検証は通ったのに実運用で
    落ちるという、検証があるぶんかえって危ない形になる。

    `home` は、ルールが指すファイル（additionalContextFile）を探す起点。層の
    ルールならその層の git プロジェクトルート。省けばワークスペースルート、
    それも無ければルールファイルの隣。

    `layer` は共通層より後ろの層かどうか。層は共通層に足すものなので、`deny` や
    `allow` が空でも穴にはならない（空の層 = 何も足さない）。共通層で確かめている
    「空のガードは入っていないのと同じ」の問いを、層にまで広げると、allow を
    1 件だけ足した層が毎回 error を出し続けることになる。

    `project` はプロジェクトの層かどうか。ターンの終わりのルール（`match: Stop`）は
    プロジェクトの層から読まない（共通層と自身の層だけ）ので、そこに書いたものを言う。
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
    # どちらも広さを咎めない。読めない every は 1（毎回渡る）になっているので、この式は
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
    2. プロジェクトの層に置いた。ターンの終わりには共通層と自身の層しか読まない
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
                f"プロジェクトの層の {rules.STOP_MATCH} のルールは使われない。ターンの終わりには"
                "共通層と自身の層しか読まない"
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
    2. 選択肢が 3 つ以上。`(ls|cat|sed)` のような並びは、それだけ多くの
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
    誤って咎めるようになる。差し込む欄の名前だけは judge の表から借りる。
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

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
from ..infra import gitcmd, hookio, modes, settings, tree
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import rules, selfguard
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, Problem
from ..tickets import (
    agree,
    approval,
    flow,
    history,
    ops,
    phase,
    phasetypes,
    review,
    risk,
    syncstate,
)
from ..tickets import ticket as ticket_mod
from . import version
from .lint_layers import _layer_configs, _layers, _projected_layer_problems, _sync, _worktree_layers
from .lint_places import _projects, _scratch, _ticket_places
from .lint_project import PROJECT_SETTINGS, _after, _local_settings, _project_settings, _registered
from .lint_rules import _rules

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
    # 確認できる者が居ないモードの切り替えの環境変数。2 値しか取らないので、受け皿も分ける。
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
    # この切り替えの環境変数に dry-run は無い。書いたユーザは「止めずに報告する」つもりでいるのに、
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
    # 明示する。明示しないと、通った検証が別の設定についての報告になる。
    stdout.write(f"  モード: {mode}（この起動の環境から解決したもの）\n")

    for problem in problems:
        stdout.write(f"{problem}\n")

    stdout.write(f"error {errors} 件、warn {warns} 件、info {infos} 件\n")
    return EXIT_ERROR if errors else EXIT_OK


# 戻す働きの 2 つが、切られている・予行になっているときに言うこと。
# 3 値（enable / dry-run / disable）を取る切り替えの環境変数なので、
# 止めない 2 つの値それぞれに文がある。
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
    """守る働きを持つ切り替えの環境変数が、止めない値になっていることを言う。
    enable なら何も言わない。

    見るのは `CCNAVI_MODE` を反映したあとの値（`modes.effective_setting`）。書かれた値だけを
    見ると、`CCNAVI_MODE=dry-run` のもとで `enable` と書かれた切り替えを「守っている」と読むことに
    なる。実行時はモードに従って戻さないので、それはこの検査がいちばん言うべき
    「切れているのに揃って見える」そのものになる。

    実際の値が書かれた値と違うときは、そのことも言う。言わないと、直す先が
    その切り替えの環境変数なのか `CCNAVI_MODE` なのかが読めない。
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


# sh が互換の版を宣言する場所。`.ccnavi/scripts/` の sh はどれもこれを `.` で読むので、
# 1 か所で足りる。
SH_COMPAT_FILE = os.path.join(".ccnavi", "scripts", "ccnavi-common.sh")
_SH_COMPAT = re.compile(r"^CCNAVI_COMPAT=([0-9]+)[ \t]*$", re.MULTILINE)


def compat_fix(root: str) -> str:
    """実行ファイルと sh が食い違ったときの直し方。ccnavi のリポジトリなら組み立て直し、
    配布先なら配り直し。配布先には組み立てる元（build.py とソース）が無い。"""
    if os.path.isfile(os.path.join(root, "build.py")) and os.path.isfile(
        os.path.join(root, "src", "ccnavi", "__main__.py")
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
    raw: approval.Raw | None = None,
) -> list[Problem]:
    """作業中の承認済みチケットを検査する。

    承認で本物とするのは置き場で、チケットの中の欄ではない。

    承認は置き場で決まり、`ccnavi_approved` の欄では決まらない。

    置き場を動かして承認する進め方では `--agree` を通らないので、承認のときにしか
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
    - 再開（`done/` から `doing/` へ手で戻す）で残った閉じるときの欄は warn。ユーザの再開を
      止めないため、判定も止めない
    - 着手済みのチケットで、状態の履歴に着手の行が無いことと、基準点（`base_sha`）がワークツリーの
      HEAD の祖先でないことは warn。提案の段階で書かれた着手の欄かもしれないが、履歴は ccnavi の外で
      動かした分を持たず、判定は git を読まないので止めない

    `raw` は呼び手が `approval.read_raw` で読んだ置き場。フェーズの順序（`phase.order_problems`）
    を子ごとに見るときに、置き場を読み直さないために渡す。
    """
    problems: list[Problem] = []
    pool = dict(index)
    for t in closed:
        pool.setdefault(t.ticket, t)
    resolve = _types_resolver(conf, root)
    for t in copies:
        left = approval.resumed_fields(t)
        if left:
            names = ", ".join(f"`{name}`" for name in left)
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket}: 作業中（doing/）なのに {names} に値が残っている。"
                    "done/ から手で戻した再開なら、ユーザがその欄を空にしてください"
                    "（残っていると、フローの着手中の扱いなど、着手中として数えない箇所がある）",
                )
            )
        unrecorded = _record_unrecorded(conf, t)
        if unrecorded:
            problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {unrecorded}"))
        unrecorded = _start_unrecorded(conf, t)
        if unrecorded:
            problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {unrecorded}"))
        off = ops.base_off_head(root, conf, t) if t.started_at else ""
        if off:
            # 判定には入れない（判定は git を読まない）。`start` は着手の前に同じ形で止める。
            problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {off}"))
        if t.blocked:
            problems.append(Problem(SEVERITY_ERROR, "(ticket)", f"{t.ticket}: {t.blocked}"))
            continue
        types = resolve(agree.project_of(t, pool))
        complaints, overflow = agree.validate(t, pool, types)
        for p in complaints + overflow:
            problems.append(Problem(p.severity, "(ticket)", f"{t.ticket}: {p.detail}"))
        parent = pool.get(t.parent) if t.is_child else None
        if parent is not None:
            for p in phase.order_problems(root, conf, t, parent, types, raw=raw):
                # 承認のときは error。承認済みのものに当てるのは「その順で始めた」という
                # 記録で、いま止める根拠にはならない。
                problems.append(Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {p.detail}"))
    return problems


def _record_unrecorded(conf: settings.Settings, t) -> str:
    """古い形（承認の記録 `ccnavi_approved` を持つ）なのに、状態の履歴に承認の行が無いときの文。

    古い形は取り下げを記録の欄で決め、`workflow:` の欄も待ち方として読む。今の承認は記録を
    書かないので、新しく置かれた古い形は手で書かれた記録かもしれない。承認の行は `approved`
    （続きの子は `raised`）。履歴は ccnavi の外で動かした分と、履歴を書く前の版の承認を持たない
    ので、無いことだけでは止めない（warn）。
    """
    if not approval.has_record(t) or not t.tree_root:
        return ""
    where = settings.approved_dir(conf, t.tree_root)
    entries, why = history.read(where, t.ticket, limit=0)
    kinds = (history.KIND_APPROVED, history.KIND_RAISED)
    if why or any(e.get("kind") in kinds for e in entries):
        return ""
    return (
        "承認の記録（ccnavi_approved）を持つ古い形なのに、状態の履歴に承認（approved / raised）の"
        "行が無い。今の承認は記録を書かないので、手で書かれた記録かもしれない。"
        "ユーザに承認済みチケットを確かめてもらってください（履歴を書く前の版で承認したものなら問題ない）"
    )


def _start_unrecorded(conf: settings.Settings, t) -> str:
    """着手の欄があるのに、状態の履歴に `started` の行が無いときの文。あれば空。

    着手の欄（`started_at`・`base_sha`）は `ticket start` だけが書き、書くときに履歴にも残す。
    承認はチケットの中身を変えないので、提案の段階で書かれた欄は、手で動かした承認では
    そのまま承認済みチケットに入り、中身だけでは道具が書いたものと見分けられない。
    履歴は ccnavi の外で動かした分を持たないので、無いことだけでは止めない
    （warn。判定にも入れない）。
    """
    if not t.started_at or not t.tree_root:
        return ""
    where = settings.approved_dir(conf, t.tree_root)
    entries, why = history.read(where, t.ticket, limit=0)
    if why or any(e.get("kind") == history.KIND_STARTED for e in entries):
        return ""
    return (
        "着手の欄（started_at）があるのに、状態の履歴に着手（started）の行が無い。"
        "ccnavi-ticket.sh start を通さずに書かれた欄かもしれない"
        "（提案の段階で書いて手で動かした承認など）。"
        "ユーザに started_at と base_sha を確かめてもらってください"
    )


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

    raw = approval.read_raw(conf, root)
    copies, notes = approval.scan(conf, root, raw=raw)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    # 閉じた承認済みチケットの苦情も拾う。判定は閉じたものを読まないが、読めないファイルが
    # 置き場に残っていること自体は書いたユーザの思い違いで、言わないと他の機械へそのまま届く。
    closed, notes = approval.scan(conf, root, closed=True, raw=raw)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    review, notes = approval.scan_review(conf, root, raw=raw)
    for note in notes:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", note))
    index = approval.by_id(copies)
    done = {t.ticket for t in closed + review}

    # 承認済みの識別子の提案は、承認済みチケットと合わせて本物とするツリーを決める
    # （`approval.scan_proposals` と同じまとめ方）。外に残った古い提案は別に名指しする。
    proposals, complaints, stale = approval.read_proposals(conf, root, raw.everything)
    problems.extend(complaints)
    problems.extend(_stale_problems(root, conf, stale, index, done))

    problems.extend(_copy_problems(root, conf, copies, index, closed, raw))

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
    preds = approval.predecessor_pool_of(copies, review, closed, proposals, root)
    approval.align_imported(conf, root, preds)
    # 統合先の done/ で閉じた識別子も閉じたものに数える（開いた親子のチケットでも統合先の
    # done/ は常に読む）。承認の対象から外れる（`agree.waiting`）ので、何も言わずに済ませず
    # 名指しする。
    done |= approval.integration_closed(conf, root, proposals)
    problems.extend(
        _proposal_problems(proposals, copies, index, closed, done, repo_of, preds, root=root)
    )
    problems.extend(
        _branch_name_problems(
            proposals,
            copies,
            closed,
            review,
            conf.integration_branch or settings.integration_recorded(conf.state),
            conf.branch_prefixes,
        )
    )
    problems.extend(_prefix_setting_problems(conf))
    problems.extend(_existing_branch_problems(root, conf, proposals, copies, closed, review))
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
    root: str = "",
) -> list[Problem]:
    """提案の側。承認待ち、先行が閉じていない着手済み、同じ識別子の重複。

    承認で落ちるものは `_approval_problems` が言う（承認と同じ関数を通す）。

    `todo/` に在るものは全部承認待ち。同じ識別子がどこかの置き場（作業中・レビュー待ち・
    閉じた）に在れば、親の改版でない限り書き損じなので名指しする。

    `closed` は `done/` の承認済みチケット。`proposals` は `todo/` と `review/`。作業中と
    レビュー待ちと閉じたの 3 つを横断して数えないと、`doing/` と `done/` に同じ識別子が
    在る形（動かす途中で止まった形跡）を CI が見逃し、状態の操作が「複数の場所にある」で
    止まって初めて知ることになる。

    **同じリポジトリの中ではツリーごとに数える。** 承認済みチケットは git に入れて共有するので、
    切ったワークツリーの数だけ同じチケットができる。しかもワークツリーはそれぞれ別のコミットを指すので、
    古いほうで `doing`・新しいほうで `done` になるのも普通の形。ツリーをまたいだ食い違いまで
    error にすると、ワークツリーを 2 本持つだけで閉じたチケットが全部 error になり、`--lint` が
    常に非ゼロで終わる。捕まえたいのは 1 つのツリーの中で 2 つの状態に在る形だけ。

    **リポジトリをまたいだら、状態が何であれ error にする。** プロジェクトは自分の git を持つので
    （設計 11）、そこに同じ識別子が在るのは同じチケットではなく違うチケットどうしの衝突。
    識別子はユーザが選ぶ短い連番で、プロジェクトが独立に振れば重なる。コミットの遅れでは説明が付かないから、
    ツリーごとの免除を当ててはいけない。
    """
    problems: list[Problem] = []

    # ツリーと状態は分けて持つ。同じ識別子が別のツリーに在るのは普通で（承認済みチケットは
    # git に入れて共有するので、切ったワークツリーの数だけ同じチケットができる）、
    # しかもワークツリーは
    # それぞれ別のコミットを指すから、古いほうが doing・新しいほうが done になるのも普通。
    # error にするのは 1 つのツリーの中で 2 つの状態に在る形だけ。
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
            if not t.is_child and t.has_plan and approval.plan_differs(t, current):
                continue  # 親の改版。承認待ちに入る
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は承認済み（{current.tree or '(ワークスペースルート)'} の "
                    f"{current.state or ticket_mod.DOING}/）なのに todo/ にも在る"
                    f"（{_rel(root, t.path)}）。"
                    "計画の改版でなければ todo/ の側を消してください",
                )
            )
            continue
        if t.ticket in done:
            problems.append(
                Problem(SEVERITY_WARN, "(ticket)", _closed_todo_text(t.ticket, _rel(root, t.path)))
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
        # コミットの遅れでは説明できないので、状態が何であれ error にする。
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
        # error にしない。ツリーをまたいだチケットはまとめれば 1 つに決まるので、
        # ここには出てこない。
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


def _rel(root: str, path: str) -> str:
    """名指しに使う、ワークスペースルートからの相対パス（`/` 区切り）。"""
    if not root or not path:
        return path
    return os.path.relpath(path, root).replace(os.sep, "/")


def _stale_problems(
    root: str, conf: settings.Settings, stale: list, index: dict, done: set[str]
) -> list[Problem]:
    """本物とするツリーの外に残った、承認済みの識別子の `todo/` の提案。

    `approval.stale_proposals` が返すもの。

    承認の対象にもボードにも入らない（本物とするツリーの側だけを読む）。名指しするのは 2 つだけ。

    - 計画の違う親の版。本物としないツリーに書いた改版か、改版の前に切ったワークツリーに
      残った古い版（巻き戻しの元）。書く場所を案内する（改版は本物とするツリーの `todo/` に
      置く）。文面は `--agree` と同じ（`approval.revision_elsewhere_text`）
    - 閉じたかレビュー待ちの識別子の `todo/`。前から出していた再開の案内を、本物とするツリーの
      外に在っても同じく出す

    計画の同じ古い提案は言わない。承認の前に切ったワークツリーに残るのは普通の形で、言うと
    ワークツリーを持つだけで WARN が並ぶ。
    """
    problems: list[Problem] = []
    for t, where in stale:
        current = index.get(t.ticket)
        if approval.revision_elsewhere(t, current):
            text = approval.revision_elsewhere_text(conf, root, t, where)
        elif current is None and t.ticket in done:
            text = _closed_todo_text(t.ticket, _rel(root, t.path))
        else:
            continue
        problems.append(Problem(SEVERITY_WARN, "(ticket)", text))
    return problems


def _closed_todo_text(ticket_id: str, rel: str) -> str:
    """閉じたかレビュー待ちの識別子が `todo/` にも在るときの案内。"""
    return (
        f"{ticket_id} は閉じたかレビュー待ちなのに todo/ にも在る（{rel}）。"
        "todo/ の側は承認の対象にならない。再開するには、"
        "ユーザが承認済みチケットを戻す"
    )


def _branch_name_problems(
    proposals: list,
    copies: list,
    closed: list,
    review: list,
    integration: str = "",
    prefixes=settings.DEFAULT_BRANCH_PREFIXES,
) -> list[Problem]:
    """識別子を親のブランチ名にできるか（warn だけ。拒否は `ccnavi-git.sh` が受け持つ）。

    親のブランチ名は親の識別子そのものにする。そのために次を名指しする。

    - 新規の提案（`todo/` にあって、承認済みでも閉じてもいないもの）の識別子の形
      （`ticket.branch_name_problems`）。承認済みの識別子はもう変えられないので言わない
    - 大文字小文字だけが違う識別子。Windows と macOS の既定のファイルシステムでは
      ブランチもワークツリーも同じ名前になる
    - 末尾が `-<2 桁>` の親の識別子。`-<2 桁>-<2 桁>` で終われば子の形（`<親>-<フェーズ>-<連番>`）に
      当たり、親子のチケットを引くとき別の親の子と読まれる。`-<2 桁>` だけでも、別の親の子の識別子の
      途中（`<親>-<フェーズ>`）と紛れる

    承認と判定はまだ変えない。止めるのは後の段階で、ここで先に数を見ておく。
    `integration` はその時点の統合先の名前で、`--integration-branch` が無ければ `ccnavi-sync.sh` が
    取り込み結果に書いた名前。どちらも無ければ固定のリストだけを見る。
    """
    problems: list[Problem] = []
    everyone = list(copies) + list(closed) + list(review) + list(proposals)
    settled = {t.ticket for t in list(copies) + list(closed) + list(review)}
    serial = ticket_mod.next_serial([t.ticket for t in everyone], prefixes)
    said: set[str] = set()
    fresh: list = []
    for t in proposals:
        if t.state != ticket_mod.TODO or t.ticket in settled or t.ticket in said:
            continue
        said.add(t.ticket)
        fresh.append(t)
        for text in ticket_mod.branch_name_problems(t, integration, serial, prefixes):
            problems.append(
                Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {text}（親のブランチ名の規則）")
            )
    problems.extend(_number_problems(everyone, fresh, prefixes, serial))

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
    tail = ticket_mod.child_tail_pattern()
    parents = sorted({t.ticket for t in everyone if not t.is_child})
    for name in parents:
        if tail.search(name) is None:
            continue
        matched = child.match(name)
        if matched is not None:
            said_how = (
                f"識別子が子の形（`<親>-<2 桁のフェーズ番号>-<2 桁の連番>`）と一致する。"
                f"親子のチケットをまとめるとき {matched.group('parent')} の子として扱われる"
            )
        else:
            said_how = (
                "識別子の末尾が `-<2 桁>` で、別の親の子の識別子の途中"
                "（`<親>-<2 桁のフェーズ番号>`）と紛れる"
            )
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{name} は親なのに、{said_how}。"
                "親の識別子の末尾を `-<2 桁>` にしないでください（親のブランチ名の規則）",
            )
        )
    return problems


def _number_problems(everyone: list, fresh: list, prefixes, serial: int) -> list[Problem]:
    """新規の提案の親の識別子の番号が、同じリポジトリの別の親と重なるか（warn）。

    番号は issue の番号か ccnavi の通し番号で、両方が同じ数になりうる。issue の番号はリポジトリ
    ごとなので、比べるのは同じリポジトリ（`project`）の親どうしだけ。前の形（`i0055`）の番号も数える。
    """
    problems: list[Problem] = []
    owners: dict[tuple[str, int], set[str]] = {}
    for t in everyone:
        if t.is_child:
            continue
        number = ticket_mod.identifier_number(t.ticket, prefixes)
        if number is not None:
            owners.setdefault((t.project, number), set()).add(t.ticket)
    for t in fresh:
        if t.is_child:
            continue
        number = ticket_mod.identifier_number(t.ticket, prefixes)
        if number is None:
            continue
        others = sorted(owners.get((t.project, number), set()) - {t.ticket})
        if others:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket}: 識別子の番号 {number} が {', '.join(others)} と重なる。"
                    "issue の番号と通し番号が同じ数になっていないか確かめる。"
                    f"issue が無いなら次の通し番号は {serial}（親のブランチ名の規則）",
                )
            )
    return problems


def _prefix_setting_problems(conf: settings.Settings) -> list[Problem]:
    """`CCNAVI_BRANCH_PREFIXES` に書かれた、先頭の語に使えない語（warn）。"""
    if not conf.branch_prefixes_rejected:
        return []
    return [
        Problem(
            SEVERITY_WARN,
            "(settings)",
            f"{settings.BRANCH_PREFIXES_ENV} の {', '.join(conf.branch_prefixes_rejected)} は"
            "先頭の語に使えないので読まない（英小文字で始まる英小文字と数字。main・master・"
            "develop・release は使えない）。"
            f"使うリストは {', '.join(conf.branch_prefixes)}（親のブランチ名の規則）",
        )
    ]


def _existing_branch_problems(
    root: str, conf: settings.Settings, proposals: list, copies: list, closed: list, review: list
) -> list[Problem]:
    """新規の提案（親）のブランチ名が、そのリポジトリの手元か origin に既にあるか（warn）。

    親のブランチ名は識別子そのもの。既にあるブランチと同じ名前で承認すると、別の作業のブランチを
    親のブランチとして取り込み・送ることになる。止めはせず、承認の前に名指しする。
    提案がそのブランチの上で書かれている（`.claude/worktrees/<識別子>` がそのブランチを
    チェックアウトしていて、提案がその中にある）ときは、そのブランチが親のブランチなので言わない。
    """
    return [
        Problem(SEVERITY_WARN, "(ticket)", f"{text}（親のブランチ名の規則）")
        for text in agree.existing_branch_warnings(root, conf, proposals, copies, closed, review)
    ]


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
    # 親のワークツリーの中の読めないチケットも止める理由（読めないチケットはリストに入らないので、
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

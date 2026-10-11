"""設定とルールの検証。判定を行わずに、防御を無効化しうる記述だけを報告する。

判定の経路は、苦情を言うために設定を読むわけではない。判定のついでに気づいたことを
標準エラーに出しているだけなので、ルールを書き換えた直後、CI、入れたばかりの
プロジェクトのように判定が走らない場面では、不備を見つける手段が無い。ここがその経路になる。

急ぐ理由は失敗したときの影響にある。ルールファイルが読めないと、block モードでは
すべてのツール呼び出しが拒否になる。そのファイルを直すための呼び出しも
止まるので、読めなくなってから気づいたのでは直せない。だから読めなくなる前に言う側が要る。

深刻度の分け方は 1 つの原則で決めてある。

* error はガードが働かない、あるいは働きすぎて全部を止める記述。放っておくと
  防御が消えるか、セッションで何もできなくなる。CI を失敗させる対象はここだけでよい
* warn は判定そのものは動くが、書いたユーザが意図した防御が働いていない記述。
  直さなくても今のところ困ることは起きないが、守っているつもりの穴が開いている

## 設計からの読み替え

もとの設計の診断コマンド `--lint` と「設定lintの検証項目」は、
config.yaml という 1 枚の設定ファイルに、ツールの許可・保護領域・
禁止コマンドがまとめて書かれている前提で書かれている。現在の形はそうではない。
設定は `.claude/settings.json` の env で渡す環境変数、防御の中身はルールファイルで、
パスの列挙という考え方そのものが無い。そこで検証項目の 9 項目を次のように読み替えた。

| 設計の検証項目 | 現在の形での読み替え | 深刻度 |
|---|---|---|
| 3 正規表現がコンパイル可能か | regex / pattern を組み立てられるか | error |
| 6 tools セクションが存在するか | ルールが 1 件でも組み上がるか | error |
| 1,2,4 禁止値・`..`・絶対パス | 該当する列挙が無い。代わりに版番号と必須欄 | error |
| 5 max_* が既定より緩くないか | 呼び出しを止めないモードと読めない値 | warn |
| 8 重複していないか | id の欠落と重複 | warn |
| 7,9 保護領域・immutable の漏れ | どのツールにも当たらない match | warn |

読み替えても変わらないのは、CI で走らせて error だけで失敗させる対象にするという
設計の検証項目の使い方のほうで、終了コードはそれに合わせてある。
"""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import replace
from typing import TextIO

from ..hook import judge
from ..infra import modes, settings, tree
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import selfguard
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, Problem
from ..tickets import (
    approval,
    approval_checks,
    flow,
    flow_render,
    flow_shape,
    flow_text,
    history,
    risk,
    syncstate,
    ticket_model,
)
from . import lint_layers, lint_places, lint_project, lint_rules, lint_ticket, version

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
    # info は数えるが、終了コードには影響しない。レイヤーをまたいだ重複のように「そう
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
        "ccnavi 自身の設定ファイル（.claude/settings*.json と、共通レイヤー・自身のレイヤー・"
        "プロジェクトのレイヤーそれぞれの設定 3 本）のバックアップを取らず、"
        "書き換えられても戻さない。"
        "ふだん実行前に足している組み込みの deny（実行ファイル・ccnavi ディレクトリ・共通レイヤーの"
        " 3 本）も足さないので、ワークツリー側のレイヤーの設定は、"
        "ルールファイルが名指ししていなければ書き込める"
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
        # 頭に付くので、その前置きは除く。
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

    problems.extend(lint_project._project_settings(root))
    problems.extend(_sh_compat(root))
    problems.extend(lint_project._after(root))
    if os.path.exists(conf.rules) or _named_rules(conf, root):
        # 共通レイヤーのルールが無いのは「設定が無い」正常（無い = 空）。不正なときだけ言う。
        # 診断の `--rules` で名指しされたファイルが無いのは、置き場が空なのとは別なので言う。
        problems.extend(lint_rules._rules(conf.rules, root))
    problems.extend(_phases(conf))
    problems.extend(_risk(conf, root))
    problems.extend(lint_ticket._ticket(conf, root))
    problems.extend(lint_places._scratch(conf, root))
    problems.extend(lint_places._projects(conf, root))
    # レイヤーの読み込みは判定と同じ経路（ruleload.survey）を通る。読めないレイヤーの苦情は
    # そこが書く標準エラーにも出るので、受け皿で受け取って二重に言わない。
    problems.extend(lint_layers._layers(io.StringIO(), conf, root))
    problems.extend(lint_layers._layer_configs(conf, root))
    problems.extend(lint_layers._worktree_layers(conf, root))
    problems.extend(lint_places._ticket_places(conf, root))
    problems.extend(lint_project._local_settings(root))
    problems.extend(lint_layers._sync(conf, root))
    return problems


def _named_rules(conf: settings.Settings, root: str) -> bool:
    """共通レイヤーのルールの置き場が、既定の場所ではなくフラグ（`--rules`）で名指しされたものか。"""
    default = os.path.join(root, settings.DEFAULT_RULES)
    return os.path.normcase(os.path.abspath(conf.rules)) != os.path.normcase(
        os.path.abspath(default)
    )


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
    チケットやレビューの操作が失敗する。sh が無いワークスペース（試しの置き場）は言わない。
    レイヤーのファイルの書式の版（`version:`）は、読む側が既に error で言う。
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
    """共通レイヤーのリスクの配点が読めるか。無いのは不備ではない（組み込みの配点）。

    `script:` が指す先が在ることも見る。走らせるときは「測れなかった」で重いほうに
    なるが、そこで気づくのは子を閉じる時点になる。
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

    (苦情, 読めた中身を `flow.as_json` にしたもの,
    `SubagentStart` で渡る手順の行（`flow_render.render`）) を返す。読めなければ中身と行は None。
    読み手も検査も `flow.load` そのもの（大きさ、リンク・ふつうのファイルでない・ハードリンク、
    UTF-8 として読めない、YAML として読めない、別名、形）。ここで別に書くと、画面が
    「正しい」と言ったフローを SubagentStart が読めない、という食い違いになる（読みの答えは
    実行ファイルの 1 か所に置く）。読めなければ error。
    ツリーの中のファイルなら、ツリーのルートからの途中のリンクも見る（SubagentStart と同じ）。
    無いファイルも error にする（確かめたつもりで何も確かめていない形を作らない）。
    中身を返すのは、拡張が値の意味（`0755` や `yes` を何と読むか）を自分で決めずに済ませるため。

    読めたフローには、手順として怪しいところを warn で足す（読むのは止めない）。線の構造
    （`flow_shape.structure_problems`）と、`candidates`（`flow.catalog`）を渡せばサブエージェントの種類と
    スキルの名前の表記（`flow_shape.name_problems`）。どれも `detail` は渡したパスで始まる。
    """
    shown = flow_text.clean(path)
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
    said = flow_shape.structure_problems(data)
    if candidates is not None:
        said += flow_shape.name_problems(data, candidates)
    warns = [Problem(SEVERITY_WARN, FLOW_WHERE, f"{shown}: {line}") for line in said]
    rendered, _ = flow_render.render(data)
    return warns, shaped, rendered


def _phases(conf: settings.Settings) -> list[Problem]:
    """共通レイヤーに `phases.yml` が置かれていないか。

    フェーズ定義は config の 1 本だけで、共通レイヤーには置けない。あれば error で名指しする。
    判定には使わず、空として扱っている。無いのは正常で、何も言わない。
    各レイヤーの `phases.yml` が読めるかは `lint_layers._layer_configs` が見る。
    """
    if not conf.phases or not os.path.exists(conf.phases):
        return []
    return [
        Problem(
            SEVERITY_ERROR,
            "(phases)",
            f"{conf.phases} は共通レイヤーには置けない。判定に使わず、空として扱っている。"
            "フェーズ定義は、使うレイヤーの .ccnavi/config/phases.yml に置いてください",
        )
    ]


def family_check(
    conf: settings.Settings, root: str, family: str, repo: str | None = None
) -> list[Problem]:
    """取り込みの後の検査（`ccnavi sync check <P> [<リポジトリ>]`）。

    この親子のチケットの承認済みチケットを判定し直し（C3）、正とする側の検査（親のワークツリーの外のチケット・
    決まらない）とあわせて、止める理由（error）を返す。レイヤーの食い違いは warn で返す。
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

    def ours(t: ticket_model.Ticket) -> bool:
        return (t.parent or t.ticket) == family and syncstate.repo_key(t.project) == st.repo

    mine = [t for t in copies if ours(t)]
    index = approval_checks.by_id(copies)
    problems: list[Problem] = []
    for t in mine:
        # チケットごとに判定し直し、error がどのチケットのものかを構造で持つ（文面の書式に頼らない）
        found = [
            p
            for p in lint_ticket._copy_problems(root, conf, [t], index, closed)
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
    # 判定し直しの対象から気づかないうちに外れる）。
    if st.home is not None:
        for _, message in approval.unreadable_copies(conf, st.home.root):
            problems.append(Problem(SEVERITY_ERROR, "(ticket)", message))
    for t in review:
        if ours(t) and t.blocked:
            problems.append(Problem(SEVERITY_ERROR, "(ticket)", f"{t.ticket}: {t.blocked}"))
    where = f"(sync/{st.repo}/{family})"
    if st.imported and st.stop and not st.closed:
        problems.append(Problem(SEVERITY_ERROR, where, st.stop))
    return problems


def _written_by_chrome(conf: settings.Settings, t: ticket_model.Ticket) -> str | None:
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

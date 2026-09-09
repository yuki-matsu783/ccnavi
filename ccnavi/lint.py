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

import io
import json
import os
from typing import TextIO

from . import gitstate, hookio, rules, selfguard, settings
from .rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

# Claude Code の設定ファイル。ccnavi 自身はこのファイルを読まない。ここに書かれた
# env は Claude Code がプロセスに渡し、ccnavi はそれを環境変数として受け取る。
# 検証だけがこのファイルを直接見る。作業ツリーの中にあってエージェントが書き換えられる
# ファイルに、防御を無効化する値が書かれていないかを問えるのはここだけだから。
PROJECT_SETTINGS = os.path.join(".claude", "settings.json")


def report(
    stdout: TextIO,
    root: str,
    conf: settings.Settings,
    notes: list[str],
    flag: str,
    restore_if_deny_flag: str = "",
    restore_setting_files_flag: str = "",
) -> int:
    """検証の結果を書き、error が 1 件でもあれば非ゼロを返す。

    書き先は標準出力にしてある。この経路は hook の payload を読まないので、
    判定を運ぶための標準出力が空いている。CI がそのまま拾える側に出す。
    """
    # 判定の側にある定数と関数を使う。lint は cli から呼ばれるので、
    # モジュールの頭で import すると循環する。
    from .cli import EXIT_ERROR, EXIT_OK, resolve_mode

    # モードの解決は判定と同じ関数に任せる。ここで別に書くと、検証は通ったのに
    # 実運用では違うモードになる、という一番まずい形になる。苦情を標準エラーへ
    # 書く作りなので、受け皿を渡して拾い、こちらで深刻度を付け直す。
    complaints = io.StringIO()
    mode = resolve_mode(complaints, flag, conf)

    # 自動復元も同じ理由で同じ関数に任せる。受け皿を分けるのは、苦情の出所を
    # 取り違えないため。どちらの設定について言われたのかが混ざると、
    # 直しに行く先が決まらない。
    said = io.StringIO()
    restore_if_deny = selfguard.resolve(
        said, restore_if_deny_flag, conf.restore_if_deny, settings.RESTORE_IF_DENY_ENV
    )
    restore_setting_files = selfguard.resolve(
        said,
        restore_setting_files_flag,
        conf.restore_setting_files,
        settings.RESTORE_SETTING_FILES_ENV,
    )

    problems = check(root, conf, notes, mode, complaints.getvalue())
    problems += [
        Problem(SEVERITY_WARN, "(restore)", line.removeprefix("ccnavi: "))
        for line in said.getvalue().splitlines()
    ]

    stdout.write("ccnavi: 設定を検証する\n")
    stdout.write(f"  ルール: {conf.rules}\n")
    stdout.write(f"  deny の場所を戻す: {restore_if_deny}\n")
    stdout.write(f"  設定ファイルを戻す: {restore_setting_files}\n")
    # 環境変数はこの起動が受け取ったものであって、セッションが受け取るものではない。
    # 端末から叩いた検証と hook から届く環境は別物なので、どちらを見た結果なのかを
    # 名乗らせる。名乗らないと、通った検証が別の設定についての報告になる。
    stdout.write(f"  モード: {mode}（この起動の環境から解決したもの）\n")

    for problem in problems:
        stdout.write(f"{problem}\n")

    errors = sum(1 for p in problems if p.severity == SEVERITY_ERROR)
    warns = len(problems) - errors
    stdout.write(f"error {errors} 件、warn {warns} 件\n")
    return EXIT_ERROR if errors else EXIT_OK


def check(
    root: str, conf: settings.Settings, notes: list[str], mode: str, complaints: str
) -> list[Problem]:
    """防御を無効化しうる記述を数え上げる。

    notes は設定の解決が出した苦情、complaints はモードの解決が出した苦情。
    どちらも深刻度を持たない文字列で届くので、ここで付ける。
    """
    from .cli import MODE_DISABLE, MODE_DRY_RUN

    problems: list[Problem] = []

    # 上書き設定を読み飛ばしても、値は既定に落ちてガードは弱まらない。
    # ただし人が設定したつもりの値がどこにも効いていない状態にはなる。
    for note in notes:
        problems.append(Problem(SEVERITY_WARN, "(settings)", note))

    for line in complaints.splitlines():
        # 判定の側は "ccnavi: " を付けて標準エラーへ書く。ここでは深刻度が
        # 頭に付くので、その前置きは落とす。
        problems.append(Problem(SEVERITY_WARN, "(mode)", line.removeprefix("ccnavi: ")))

    if mode == MODE_DISABLE:
        problems.append(Problem(SEVERITY_WARN, "(mode)", f"{MODE_DISABLE} なので何も判定しない"))
    elif mode == MODE_DRY_RUN:
        # warn は導入の途中では正しい状態なので error にはしない。それでも
        # 言う。ルールが揃っているのに 1 件も止まらない状態は、外から見ると
        # ガードが効いている状態と区別が付かない。
        problems.append(
            Problem(
                SEVERITY_WARN, "(mode)", f"{MODE_DRY_RUN} なので判定しても呼び出しに手を出さない"
            )
        )

    problems.extend(_project_settings(root))
    problems.extend(_after(root))
    problems.extend(_rules(conf.rules))
    problems.extend(_ticket(conf))
    return problems


def _ticket(conf: settings.Settings) -> list[Problem]:
    """チケットと承認台帳が噛み合っているかを見る。

    判定に効くのは台帳の側だけなので、ここで問うのは「効いている範囲は何か」と
    「作業ツリーのチケットがそれと一致しているか」の 2 つ。一致していない状態は
    壊れてはいないが、書いた人は書いたとおりに効いていると思っている。
    検証はその思い違いを名指しする場所になる。
    """
    from . import approval
    from . import ticket as ticket_mod

    problems: list[Problem] = []
    if not conf.ledger:
        problems.append(
            Problem(SEVERITY_WARN, "(ticket)", "承認台帳の置き場が空。チケットの範囲は効かない")
        )
        return problems

    approved, unreadable = approval.current(conf.ledger)
    if unreadable:
        problems.append(Problem(SEVERITY_ERROR, "(ticket)", unreadable))
        return problems

    proposed, complaints = ticket_mod.load(conf.ticket)
    # チケットの不備は error のまま上げる。検証は人が読む場所なので、
    # 承認しようとして初めて気づくより、ここで気づけるほうがよい。
    problems.extend(complaints)

    if approved is None and proposed is None:
        # チケットも承認も無い状態は不備ではない。チケットによる制御は任意で、
        # 使っていないプロジェクトにここで苦情を返すと、その 1 行が常態になって
        # 他の報告ごと読まれなくなる。
        return problems

    if approved is None:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{proposed.ticket} は未承認。'ccnavi --approve' を通すまで範囲は効かない",
            )
        )
    elif proposed is None:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"読めるチケットが無いが、{approved.ticket} の承認済みの範囲は効いている: "
                f"{', '.join(approved.write)}",
            )
        )
    elif proposed.digest() != approved.digest:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{conf.ticket} は承認された内容と違う。効いているのは {approved.ticket} の "
                f"承認済みの範囲: {', '.join(approved.write)}",
            )
        )
    return problems


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


def _registered(root: str) -> bool | None:
    """PostToolUse に ccnavi が登録されているか。設定ファイルが無ければ None。

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
    entries = hooks.get(hookio.POST_TOOL_USE) if isinstance(hooks, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
            command = hook.get("command") if isinstance(hook, dict) else None
            if isinstance(command, str) and "ccnavi" in command:
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
    from .cli import MODE_DISABLE, MODE_DRY_RUN, MODE_ENABLE

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
        if normalized == MODE_DISABLE:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    "(project)",
                    f"{PROJECT_SETTINGS} の env が {settings.MODE_ENV}={MODE_DISABLE} を"
                    "宣言している。"
                    "監視される側が書けるファイルから監視を止めている。"
                    "止めるならセッションを起動する側の環境から渡す",
                )
            )
        elif normalized not in (MODE_DRY_RUN, MODE_ENABLE):
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(project)",
                    f"{PROJECT_SETTINGS} の env の {settings.MODE_ENV}={declared!r} は"
                    f"モードとして読めない。{MODE_ENABLE} に落ちる",
                )
            )
    return problems


def _rules(path: str) -> list[Problem]:
    """ルールファイルを、判定が読むのと同じ読み方で読んで検証する。

    rules.load をそのまま呼ぶ。別の読み方をすると、検証は通ったのに実運用で
    落ちるという、検証があるぶんかえって危ない形になる。
    """
    try:
        rule_set, problems = rules.load(path)
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

        for tool in _inert(rule.match):
            problems.append(
                Problem(
                    SEVERITY_WARN, name, f"match の {tool} には当てる対象が無い。何も止まらない"
                )
            )

    return problems


def _inert(match: str) -> list[str]:
    """match に並んだツール名のうち、判定が対象を取り出せないものを返す。

    ルールを当てる文字列を選ぶのは cli.subject_of で、そこが知らないツール名では
    対象が空になり、rule.matches は必ず False を返す。つまりそのルールは
    書いてあるのに何も止めない。守っているつもりの穴なので warn で言う。

    ツール名の一覧を持たずに subject_of を実際に呼んで確かめている。一覧を写すと、
    判定側が扱うツールを増やしたときにこちらが黙って古くなり、正しいルールを
    誤って咎めるようになる。
    """
    from .cli import subject_of

    inert: list[str] = []
    for want in match.split("|"):
        tool = want.strip()
        if not tool:
            continue
        # 対象を持つツールなら何かしら返る値を入れておく。返るかどうかだけを見る。
        probe = hookio.Input(tool_name=tool, tool_input={"command": "x", "file_path": "x"})
        if not subject_of(probe):
            inert.append(tool)
    return inert

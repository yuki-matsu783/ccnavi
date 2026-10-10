"""`--lint` のうち、Claude Code の設定ファイル（`.claude/settings.json` と
`.claude/settings.local.json`）に書かれた hook と env の検査。
"""

from __future__ import annotations

import json
import os

from ..infra import gitstate, hookio, modes, settings
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

# Claude Code の設定ファイル。ccnavi 自身はこのファイルを読まない。ここに書かれた
# env は Claude Code がプロセスに渡し、ccnavi はそれを環境変数として受け取る。
# 検証だけがこのファイルを直接見る。作業ツリーの中にあってエージェントが書き換えられる
# ファイルに、防御を無効化する値が書かれていないかを確かめられるのはここだけだから。
PROJECT_SETTINGS = os.path.join(".claude", "settings.json")


def _after(root: str) -> list[Problem]:
    """実行後チェックが実際に動く形になっているかを見る。

    どちらも error にしない。実行前チェックは動いているので、防御が消えている
    わけではない。それでも言う。宣言した保護領域が、引数に現れない書き込みに
    対しては 1 つも守られていない状態は、外から見ると守られている状態と
    区別が付かない。

    見に行く方法は判定と同じ。別の見方をすると、検証は通ったのに実運用では
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
# チケット制御と承認の保護の切り替え・動作モード。例外は統合先の名前だけ（リポジトリに置かず、
# 手元では環境変数で持つと決めた値なので）。置き場は既定に固定で env では動かないので、
# 置き場の env は数えない。
LOCAL_SETTINGS = settings.LOCAL_CLAUDE_SETTINGS


#
# 保護と判定の働きを変える値（戻す働き・ccnavi 自身の設定の保護・確かめられないモードの止め・
# 同じ理由の拒否の数え方）も入れる。手元だけで切ると、ユーザが端末で打つ sh と
# 他の機械で、同じ親子のチケットに掛かる保護が別になる。入れないのは、判定の答えを変えない
# 次の値だけ。
# 実行ファイルのパス（`CCNAVI_BIN_PATH`。hook の起動のために手元で差し替える。README の
# 案内）、診断ログ（`CCNAVI_LOG_LEVEL` など）、タイムアウト監視と待ちの秒（`CCNAVI_*_TIMEOUT`・
# `CCNAVI_LOCK_WAIT`）。
_LOCAL_FORBIDDEN = (
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

    承認と判定は、保護の切り替えや動作モードを統合先（リポジトリに乗る設定）と揃えて読む前提で組む。
    手元だけのファイルに置いた値は Claude Code が起こしたプロセスにだけ使われ、Chrome と
    ユーザが端末で打つ sh には使われないので、同じ親子のチケットにプロセスごとに別の保護が掛かる。
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
            "同じ親子のチケットに"
            "プロセスごとに別の保護が掛かることになる）。"
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

"""動作モードと終了コード。判定をどう扱うかを決め、どう判定するかは決めない。

enable / dry-run / disable の 3 値はここが持つ。判定のモード（CCNAVI_MODE）も、
戻す働きの設定（CCNAVI_RESTORE_IF_DENY、CCNAVI_GUARD_CORE_FILES）と Stop の AI 判定の設定
（CCNAVI_STOP_REVIEW）も同じ 3 値を取るので、定義は 1 か所にして selfguard はここから借りる。

フラグ・環境・設定ファイルのどれから来た値がモードになるか（resolve_mode）、
モードを掛けたあとに戻す働きがどうなるか（effective_setting）、判定に達せな
かったときにどう終わるか（fail_closed）。どれも判定の中身には触れない。
"""

from __future__ import annotations

from typing import TextIO

from . import settings

# 終了コード。
EXIT_OK = 0  # 判定を書いた、あるいは言うことが無かった

EXIT_ERROR = 1  # 使い方の誤り、または読めない設定

EXIT_BLOCK = 2  # 判定を書けなかったので拒否にする
# 問われたことへの「いいえ」。`--agree --preview --verify` が、承認の対象に入らないと
# 答えるときに返す。1 と分けてあるのは、1 が「使い方か設定の誤り」だから。同じ値にすると、
# 打ち方を間違えた回と提案が失敗する回が区別できず、読む側（エージェント）は、直すものが
# 無いのに提案を直しに行く。
EXIT_ANSWER_NO = 3
# 端末の `--agree` が、承認済みチケットを一部だけ置いて失敗で終わったとき。何も置かずに失敗した
# ときは 1。`ccnavi-user-agree.sh` はこの値のときだけ、置かれたぶんをコミットして push する。
EXIT_PARTIAL = 4

# モードは判定をどう扱うかを決めるだけで、どう判定するかは決めない。
# 判定を続ける 2 つのモードは同じ経路を通るので、dry-run が報告するものが
# enable なら実際に起きたことと一致する。ただし関門（GATE）と補助（AUX）は dry-run でも
# 止めるので、その分は報告ではなく実際に起きる（下の分類）。
#
# 名前はガードそのものの状態を言う。判定に deny と ask の 2 つがある以上、
# 名前が「止める」だけを言うと、確認で済む回には名前が事実と合わなくなる。
# 弱いほうから強いほうへ並ぶのは lint の深刻度と同じ。
DISABLE = "disable"  # 判定しない

DRY_RUN = "dry-run"  # 判定して報告する。一般の保護は手を出さず、関門と補助は止める（下の分類）

ENABLE = "enable"  # 判定を呼び出しに適用する


def resolve_mode(stderr: TextIO, flag_value: str, conf: settings.Settings) -> str:
    """設定された値をモードにする。

    設定ファイルが決められるのは enable と dry-run だけで、その編集は次のツール
    呼び出しから反映される。disable を言えるのは環境だけ。設定ファイルは作業ツリーの中に
    あってエージェントが書けるので、そこに書かれた disable を有効にすると、
    監視される側が監視を止められてしまう。

    フラグは両方より強い。テストが、たまたま走った環境に依存せずモードを
    固定できるように。

    どこにも値が無ければ enable。設定の欠落が、ガードの欠落にならないように。
    """
    source, value = settings.MODE_ENV, conf.mode
    if flag_value:
        source, value = "--mode", flag_value

    normalized = value.lower()

    if normalized == DRY_RUN:
        return DRY_RUN

    if normalized == DISABLE:
        # disable の経路は 1 本だけ。セッションを起動したユーザの環境から来て、
        # かつ作業ツリーの中の何もそれを求めていないとき。設定ファイルもフラグも
        # エージェントが書ける場所から来るし、そこでの編集は次のツール呼び出しから
        # 反映されるので、どちらの off を認めても、監視される側が監視を
        # 止められることになる。
        from_file = conf.mode_declared_in_file.lower()
        from_env = conf.mode_from_environment.lower()
        if not flag_value and from_env == DISABLE and from_file != DISABLE:
            return DISABLE
        stderr.write(
            f"ccnavi: {source}={DISABLE} is ignored because it comes from inside "
            f"the project; start the session with {settings.MODE_ENV}={DISABLE} "
            "in the environment instead\n"
        )
        return ENABLE

    if normalized in ("", ENABLE):
        return ENABLE

    # 解釈できない値も最も強いモードになるが、それを言うことに意味がある。
    # 名前を変えた設定や打ち間違いが、何も言わないと意図した選択に見えてしまい、
    # 誰にも見えない理由でガードが厳しくなることになる。
    stderr.write(
        f"ccnavi: {source}={value!r} is not a mode; using {ENABLE}. "
        f"Valid modes are {DISABLE}, {DRY_RUN} and {ENABLE}\n"
    )
    return ENABLE


def effective_setting(mode: str, declared: str) -> str:
    """CCNAVI_MODE を掛けたあとの、実際に使われる設定。

    判定を適用しないモードでは、戻す側もファイルに触らない。dry-run は
    「呼び出しにも作業ツリーにも手を出さない」ことがモードの約束で、
    守る側だけがその約束の外に出ると、試している最中に誰も頼んでいない
    ファイル操作が起きる。試すことが怖くなれば、誰も試さなくなる。
    """
    if declared == DISABLE:
        return DISABLE
    return declared if mode == ENABLE else DRY_RUN


# 止めている箇所の分類。dry-run のときの扱いを分類で決める。
# GENERAL: ルールファイルの deny・ask など一般の保護。dry-run は報告だけにする。
# GATE: チケット制御の関門（状態の操作、承認済みの置き場、範囲、レビュー待ちなど）。
#   dry-run でも止める。
# AUX: 関門を外す抜け道をふさぐ補助の保護（生の git、環境変数の指定など）。dry-run でも止める。
GENERAL = "general"
GATE = "gate"
AUX = "aux"


def holds_in_dry_run(kind: str) -> bool:
    """dry-run でも止める分類か。"""
    return kind in (GATE, AUX)


# ツールの種類ごとの、判定に達せなかったときの扱い。
STRICT_TOOLS = ("Write", "Edit", "NotebookEdit", "Agent")
READ_TOOLS = ("Read", "Grep", "Glob")
SHELL_TOOLS = ("Bash", "PowerShell")


def fail_closed(mode: str, tool: str | None = None, *, safe: bool = False) -> int:
    """「判定に達せなかった」ときの終了コード。

    tool が None（ツールの呼び出しを持たないイベント）は、enable で止め、dry-run で通す。
    達せなかった判定が許可になってはならないが、呼び出しに手を出さないことが dry-run の
    約束なので、自分の失敗で作業を止めるようでは意味がない。

    PreToolUse の呼び出しはツールの種類で分ける。`safe` は、呼び手が設定を読まずに「通してよい」と
    確かめた呼び出し（`hook.failsafe`）。

    - 空（payload が読めずツールが分からない）: 止める
    - Write・Edit・NotebookEdit・Agent: dry-run でも止める
    - Read・Grep・Glob・Bash・PowerShell: `safe` のときだけ通す。dry-run でも同じ
    - 知らないツール: enable は止め、dry-run は通す
    """
    if tool is None:
        return EXIT_OK if mode == DRY_RUN else EXIT_BLOCK
    if tool == "" or tool in STRICT_TOOLS:
        return EXIT_BLOCK
    if tool in READ_TOOLS or tool in SHELL_TOOLS:
        return EXIT_OK if safe else EXIT_BLOCK
    return EXIT_OK if mode == DRY_RUN else EXIT_BLOCK

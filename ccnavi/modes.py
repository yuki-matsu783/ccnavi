"""動作モードと終了コード。判定をどう扱うかを決め、どう判定するかは決めない。

enable / dry-run / disable の 3 値はここが持つ。判定のモード（CCNAVI_MODE）も、
戻す働きの設定（CCNAVI_RESTORE_IF_DENY、CCNAVI_GUARD_CORE_FILES）も同じ 3 値を
取るので、定義は 1 か所にして selfguard はここから借りる。

フラグ・環境・設定ファイルのどれから来た値がモードになるか（resolve_mode）、
モードを掛けたあとに戻す働きがどう効くか（effective_setting）、判定に達せな
かったときにどう終わるか（fail_closed）。どれも判定の中身には触れない。
"""

from __future__ import annotations

from typing import TextIO

from . import settings

# 終了コード。
EXIT_OK = 0  # 判定を書いた、あるいは言うことが無かった

EXIT_ERROR = 1  # 使い方の誤り、または読めない設定

EXIT_BLOCK = 2  # 判定を書けなかったので拒否側に倒す

# モードは判定をどう扱うかを決めるだけで、どう判定するかは決めない。
# 判定を続ける 2 つのモードは同じ経路を通るので、dry-run が報告するものが
# enable なら実際に起きたことと一致する。
#
# 名前はガードそのものの状態を言う。判定に deny と ask の 2 つがある以上、
# 名前が「止める」だけを言うと、確認で済む回に嘘をつくことになる。
# 弱いほうから強いほうへ並ぶのは lint の深刻度と同じ。
DISABLE = "disable"  # 判定しない

DRY_RUN = "dry-run"  # 判定して報告するが、呼び出しには手を出さない

ENABLE = "enable"  # 判定を呼び出しに適用する


def resolve_mode(stderr: TextIO, flag_value: str, conf: settings.Settings) -> str:
    """設定された値をモードにする。

    設定ファイルが決められるのは warn と block だけで、その編集は次のツール
    呼び出しから効く。disable を言えるのは環境だけ。設定ファイルは作業ツリーの中に
    あってエージェントが書けるので、そこに書かれた disable を効かせると、
    見張られている側が見張りを止められてしまう。

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
        # disable の経路は 1 本だけ。セッションを起動した人の環境から来て、
        # かつ作業ツリーの中の何もそれを求めていないとき。設定ファイルもフラグも
        # エージェントが書ける場所から来るし、そこでの編集は次のツール呼び出しから
        # 効くので、どちらの off を認めても、見張られている側が見張りを
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

    # 解釈できない値も最も強いモードに着地するが、それを言うことに意味がある。
    # 名前を変えた設定や打ち間違いが、黙っていると意図した選択に見えてしまい、
    # 誰にも見えない理由でガードが締まることになる。
    stderr.write(
        f"ccnavi: {source}={value!r} is not a mode; using {ENABLE}. "
        f"Valid modes are {DISABLE}, {DRY_RUN} and {ENABLE}\n"
    )
    return ENABLE


def effective_setting(mode: str, declared: str) -> str:
    """CCNAVI_MODE を掛けたあとの、実際に効く設定。

    判定を適用しないモードでは、戻す側もファイルに触らない。dry-run は
    「呼び出しにも作業ツリーにも手を出さない」ことがモードの約束で、
    守る側だけがその約束の外に出ると、試している最中に誰も頼んでいない
    ファイル操作が起きる。試すことが怖くなれば、誰も試さなくなる。
    """
    if declared == DISABLE:
        return DISABLE
    return declared if mode == ENABLE else DRY_RUN


def fail_closed(mode: str) -> int:
    """「判定に達せなかった」ときの終了コード。

    enable は呼び出しを止める。達せなかった判定が許可に化けてはならないから。
    dry-run は通す。呼び出しに手を出さないことがそのモードの約束なので、
    自分の失敗で作業を止めるようでは意味がない。
    """
    return EXIT_OK if mode == DRY_RUN else EXIT_BLOCK

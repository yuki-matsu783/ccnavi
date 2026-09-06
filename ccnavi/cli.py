"""標準入出力とコマンドラインを 1 つの判定に繋ぐ。

run は終了コードを返す。自分で終了しないので、道具ぜんぶを別プロセス無しで
テストから動かせる。
"""

from __future__ import annotations

import argparse
import os
import time
from typing import TextIO

from . import audit, hookio, rules, settings, shellread

# 1 回の起動に張る期限。呼び手は長く走った hook を打ち切って出力を捨てるので、
# それより先に自前の判定へ着地することが、遅い判定が黙った許可に化けるのを防ぐ。
DEADLINE_SECONDS = 3.0

# 終了コード。
EXIT_OK = 0  # 判定を書いた、あるいは言うことが無かった
EXIT_ERROR = 1  # 使い方の誤り、または読めない設定
EXIT_BLOCK = 2  # 判定を書けなかったので拒否側に倒す

# モードは判定をどう扱うかを決めるだけで、どう判定するかは決めない。
# 判定を続ける 2 つのモードは同じ経路を通るので、warn が報告するものが
# block なら止めていたものと一致する。
#
# 名前はツール呼び出しがどうなるかを言っていて、lint の深刻度と同じように
# 弱いほうから強いほうへ並ぶ。
MODE_OFF = "off"  # 判定しない
MODE_WARN = "warn"  # 判定して報告するが、呼び出しは通す
MODE_BLOCK = "block"  # 判定して呼び出しを止める

USAGE = """ccnavi guards agent tool calls and guides the agent to a safer alternative.

It is a hook command, not something to run by hand: it reads one hook payload
as JSON on stdin and writes its response to stdout.

Register it on the tool-call events of your agent, then exercise it with

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash",
           "tool_input":{"command":"git push"}}' | ccnavi
"""


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    """1 回の起動を処理する。"""
    parser = argparse.ArgumentParser(prog="ccnavi", add_help=False)
    parser.add_argument("--root", default=None)
    parser.add_argument("--mode", default="")
    parser.add_argument("--rules", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_ERROR
    if args.help:
        stderr.write(USAGE)
        return EXIT_ERROR

    root = args.root if args.root is not None else default_root()
    conf, problems = settings.load(root)
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    # フラグは何よりも強い。診断のための実行が、プロジェクト全体で共有している
    # ファイルに触らずに別の場所を指せるように。
    if args.log:
        conf.log = args.log
    if args.rules:
        conf.rules = args.rules

    mode = resolve_mode(stderr, args.mode, conf)
    log = audit.Log(conf.log)
    deadline = time.monotonic() + DEADLINE_SECONDS

    try:
        payload = hookio.decode(stdin)
    except hookio.NoPayload:
        # hook ではなく端末の前の人。何も判定していないので何も記録しない。
        stderr.write(USAGE)
        return EXIT_ERROR
    except hookio.Unusable as exc:
        stderr.write(f"ccnavi: {exc}\n")
        _record(
            stderr,
            log,
            audit.Record(
                mode=mode,
                decision=audit.SKIP,
                reason=audit.REASON_PAYLOAD_UNUSABLE,
            ),
        )
        return fail_closed(mode)

    record = audit.Record(
        mode=mode,
        event=payload.event,
        tool=payload.tool_name,
        subject=subject_of(payload),
        session=payload.session_id,
    )

    code = decide(stdout, stderr, mode, conf.rules, payload, record, deadline)
    _record(stderr, log, record)
    return code


def decide(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    rules_path: str,
    payload: hookio.Input,
    record: audit.Record,
    deadline: float,
) -> int:
    """判定に達し、何が起きたかを record に書き込む。
    記録と応答が必ず同じ結論から作られるようにするため。"""
    if mode == MODE_OFF:
        record.decision, record.reason = audit.SKIP, audit.REASON_MODE_OFF
        return EXIT_OK
    if payload.event != hookio.PRE_TOOL_USE:
        # 判定を持たないイベントは誤りではない。想定していない登録が
        # 作業を止めてはいけない。
        record.decision, record.reason = audit.SKIP, audit.REASON_EVENT_NOT_CHECKED
        return EXIT_OK
    if not record.subject:
        record.decision, record.reason = audit.SKIP, audit.REASON_NO_SUBJECT
        return EXIT_OK

    try:
        rule_set, problems = rules.load(rules_path)
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: ルールを読めない: {exc}\n")
        record.decision = audit.SKIP
        record.reason = audit.REASON_RULES_UNREADABLE
        record.detail = rules_path
        return fail_closed(mode)
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    subject = screen(payload.tool_name, record.subject, record)

    reasons: list[str] = []
    for rule in rule_set.rules:
        if time.monotonic() > deadline:
            stderr.write("ccnavi: 判定を終える前に期限に達した\n")
            record.decision, record.reason = audit.SKIP, audit.REASON_DEADLINE_EXCEEDED
            return fail_closed(mode)
        if rule.matches(payload.tool_name, subject):
            reasons.append(rule.message)
            record.rules.append(rule.id)

    if not reasons:
        record.decision, record.enforced = audit.ALLOW, True
        return EXIT_OK

    # 当たった理由はまとめて 1 回で返す。1 つずつ返すと、エージェントも
    # 1 つずつ直すことになり、そのたびに往復が 1 回増える。
    reason = "\n".join(reasons)
    if record.degraded:
        reason = unreadable(record.degraded) + "\n" + reason

    if mode == MODE_WARN:
        record.decision, record.enforced = audit.DENY, False
        hookio.write_context(
            stdout,
            hookio.PRE_TOOL_USE,
            "[ccnavi warn] block mode would have stopped this call:\n" + reason,
        )
        return EXIT_OK

    record.decision, record.enforced = audit.DENY, True
    hookio.write_verdict(stdout, hookio.DENY, reason)
    return EXIT_OK


def fail_closed(mode: str) -> int:
    """「判定に達せなかった」ときの終了コード。

    block は呼び出しを止める。達せなかった判定が許可に化けてはならないから。
    warn は通す。何も変えないことが目的のモードが、自分の失敗で作業を
    止めるようでは意味がない。
    """
    return EXIT_OK if mode == MODE_WARN else EXIT_BLOCK


def _record(stderr: TextIO, log: audit.Log, record: audit.Record) -> None:
    """1 行を書く。書けなかったことは報告して捨てる。
    それがツールの実行可否を変えられてはいけない。"""
    try:
        log.write(record)
    except OSError as exc:
        stderr.write(f"ccnavi: 記録を書けない: {exc}\n")


def subject_of(payload: hookio.Input) -> str:
    """このツールでルールを当てる欄を選ぶ。"""
    if payload.tool_name == "Bash":
        return payload.field_value("command")
    if payload.tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return full_path(payload.field_value("file_path"), payload.cwd)
    return ""


def full_path(path: str, cwd: str) -> str:
    """ファイルのパスを、行き着く先が 1 つに決まる綴りに直す。

    来たままの文字列に当てると、同じ場所を別の綴りで書くだけでルールを外せる。
    相対パスは呼び出し側の作業ディレクトリ次第で意味が変わるし、`..` を挟めば
    `secrets/` を通らない綴りで `secrets/` の中に届く。シンボリックリンクなら
    名前を 1 つ増やすだけで済む。守る対象は名前ではなく場所なので、
    場所まで解いてから当てる。

    解けなかったときも、絶対パスにして `..` を畳むところまではやる。
    まだ存在しないファイルへの書き込みがこれにあたる。
    """
    if not path:
        return ""
    base = cwd or os.getcwd()
    joined = os.path.join(base, os.path.expanduser(path))
    try:
        return os.path.realpath(joined)
    except OSError:
        return os.path.normpath(os.path.abspath(joined))


def screen(tool: str, subject: str, record: audit.Record) -> str:
    """シェルのコマンドを、実際に実行される部分まで絞る。読み切れなかったときは
    record にそう書き残す。

    ルールはコマンドについて書かれたものであって、文字列についてではない。
    git push を引用した文書は push ではないし、そこで拒否を返すことは、
    やっていないことをやったと読み手に告げることになる。

    読み切れないコマンドは生の文字列に落とす。これは以前の挙動そのものなので、
    今まで捕まえていたものが抜けることはない。変わるのは、返す拒否が
    どちらの拒否なのかを名乗らなければならない点。
    """
    if tool != "Bash":
        return subject
    reading = shellread.read(subject)
    if reading.degraded:
        record.degraded = reading.reason
        return subject
    return reading.text


def unreadable(reason: str) -> str:
    """コマンドを読めないまま出す拒否の書き出し。

    2 つの違う失敗に同じ文を使わせないために要る。「禁止されたコマンドを実行した」と
    「読めない文字列のどこかにその語がある」では次にやることが違うし、
    後者なのに前者を渡された読み手は、書いた覚えのないコマンドを探しに行く。
    """
    what = {
        shellread.REASON_UNTERMINATED: ("a quote or heredoc in this command never closes"),
        shellread.REASON_TAKEN_AS_CODE: (
            "this command hands a string to something that runs it as code"
        ),
    }.get(reason, "this command could not be read")
    return (
        "[ccnavi] " + what + ", so the rules below were matched against the raw text instead of "
        "against what runs. If these words are only being written down and not "
        "run, nothing was deliberately forbidden: move the text into a file, or "
        "split the command up, and try again. The rules that matched the text:"
    )


def resolve_mode(stderr: TextIO, flag_value: str, conf: settings.Settings) -> str:
    """設定された値をモードにする。

    設定ファイルが決められるのは warn と block だけで、その編集は次のツール
    呼び出しから効く。off を言えるのは環境だけ。設定ファイルは作業ツリーの中に
    あってエージェントが書けるので、そこに書かれた off を効かせると、
    見張られている側が見張りを止められてしまう。

    フラグは両方より強い。テストが、たまたま走った環境に依存せずモードを
    固定できるように。

    どこにも値が無ければ block。設定の欠落が、ガードの欠落にならないように。
    """
    source, value = settings.MODE_ENV, conf.mode
    if flag_value:
        source, value = "--mode", flag_value

    normalized = value.lower()

    if normalized == MODE_WARN:
        return MODE_WARN

    if normalized == MODE_OFF:
        # off の経路は 1 本だけ。セッションを起動した人の環境から来て、
        # かつ作業ツリーの中の何もそれを求めていないとき。設定ファイルもフラグも
        # エージェントが書ける場所から来るし、そこでの編集は次のツール呼び出しから
        # 効くので、どちらの off を認めても、見張られている側が見張りを
        # 止められることになる。
        from_file = conf.mode_declared_in_file.lower()
        from_env = conf.mode_from_environment.lower()
        if not flag_value and from_env == MODE_OFF and from_file != MODE_OFF:
            return MODE_OFF
        stderr.write(
            f"ccnavi: {source}=off is ignored because it comes from inside the "
            f"project; start the session with {settings.MODE_ENV}=off in the "
            "environment instead\n"
        )
        return MODE_BLOCK

    if normalized in ("", MODE_BLOCK):
        return MODE_BLOCK

    # 解釈できない値も最も強いモードに着地するが、それを言うことに意味がある。
    # 名前を変えた設定や打ち間違いが、黙っていると意図した選択に見えてしまい、
    # 誰にも見えない理由でガードが締まることになる。
    stderr.write(
        f"ccnavi: {source}={value!r} is not a mode; using {MODE_BLOCK}. "
        f"Valid modes are {MODE_OFF}, {MODE_WARN} and {MODE_BLOCK}\n"
    )
    return MODE_BLOCK


def default_root() -> str:
    """プロジェクト根、つまり .claude を持つディレクトリを見つける。

    ここでは作業ディレクトリそのものに頼ってはいけない。hook は自分が走る
    ディレクトリを選べないから。代わりに上へ辿るので、プロジェクトの中の
    どこから起動しても同じ根に行き着くし、思わぬ場所で起動された hook でも
    プロジェクトの設定を読める。
    """
    # Claude Code はこれを渡してくるが、あることに依存してはいけない。
    from_env = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if from_env:
        return from_env
    found = _find_project_root()
    return found if found else "."


def _find_project_root() -> str:
    """作業ディレクトリからファイルシステムの根まで上って .claude を探す。
    バージョン管理が自分の根を見つけるのと同じやり方。"""
    try:
        directory = os.getcwd()
    except OSError:
        return ""
    while True:
        if os.path.isdir(os.path.join(directory, ".claude")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return ""
        directory = parent

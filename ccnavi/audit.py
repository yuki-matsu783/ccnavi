"""1 回の起動につき 1 行を、追記のみのファイルに書く。

通した呼び出しも書く。記録が意味を持つのは「記録が無い呼び出し」との対比に
おいてであって、拒否だけを残すとその対比ができない。動かなくなったガードと、
言うことが何も無かったガードが、まったく同じ見た目になる。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

# 呼び出しに何が起きたか。
ALLOW = "allow"
ASK = "ask"
DENY = "deny"
# SKIP は判定に至らなかったことを示す。SKIP には必ず reason が付くので、
# 「判定しなかった」と「判定して何も無かった」を見分けられる。
SKIP = "skip"
# HANDOVER は、判定を Claude Code の権限モードへ渡したことを示す。
# ASK と分けてあるのは、渡した回と人に聞いた回を別に数えるため。混ぜると
# 「ルールが言及していない呼び出し」の総量は見えても、そのうち誰かが実際に
# 判断した回がどれだけかを言えなくなる。ルールを足すべきかどうかは前者で決まり、
# ガードが効いていたかどうかは後者で決まるので、同じ欄には置けない。
HANDOVER = "handover"

# 判定に至らなかった理由。
REASON_MODE_DISABLED = "mode-disabled"
REASON_EVENT_NOT_CHECKED = "event-not-checked"
REASON_NO_SUBJECT = "no-subject"
# コマンドは在るが、実行される部分が無い。コメントだけの行がこれにあたる。
# 何も走らないものについて確認を出すと、確認の数だけが増えて中身が減る。
REASON_NOTHING_TO_RUN = "nothing-to-run"
REASON_PAYLOAD_UNUSABLE = "payload-unusable"
REASON_DEADLINE_EXCEEDED = "deadline-exceeded"
# 実行後の監視だけが出す 2 つは post.py が持っている。判定に至らなかった
# 理由という点では同じだが、あちらは作業ツリーを読めたかどうかの話なので、
# 名前もそちらに置いてある。
# ルールが読めないことは、ここには無い。判定に至らなかった理由ではなく、
# 組み込みの既定で判定を続けたうえで fallback に残す事実になっている。
# 拒否側へ倒すと、壊れたファイルを直す呼び出しまで止まって回復できなくなる。

# 記録に残すコマンドやパスの上限。ヒアドキュメントはファイル 1 本を運べるので、
# 1 回の呼び出しが記録を膨らませられないようにする。
SUBJECT_LIMIT = 1000


@dataclass
class Record:
    """記録の 1 行。"""

    mode: str = ""
    # permission_mode は、呼び出しが来たときの Claude Code の権限モード。
    # ルールが言及しない呼び出しの結末がこれで変わる（cli.undeclared_verdict）
    # ので、残さないと
    # 同じ subject に別の結末が並ぶ理由を、記録だけでは説明できなくなる。
    permission_mode: str = ""
    event: str = ""
    tool: str = ""
    subject: str = ""

    # decision は下した判定。それをどう扱ったかとは別に持つ。
    decision: str = ""
    # enforced はその判定を呼び出しに適用したかどうか。warn は deny に達して
    # enforced を false のまま残す。1 つのファイルが「何を止めるはずだったか」と
    # 「何を実際に止めたか」の両方に答えられるのはこのため。実際に止めた分だけを
    # 数えると、warn で先に走らせる意味がまるごと隠れる。
    enforced: bool = False

    # code は判定の根拠の種別。ccnavi.md 付録 B の体系から借りた名前で、
    # cli.py と post.py が返す文の先頭に載せるものと同じ。記録に残すのは、
    # 止めた回のうちどれだけが「宣言された禁止に当たった」もので、どれだけが
    # 「どのルールも言及していない」ものかを、あとから数えられるようにするため。
    # 後者が多いなら直すのはルールの側で、拒否を 1 件足すことではない。
    code: str = ""

    reason: str = ""
    # degraded は、判定は下したがコマンドを読み切れなかったときに、
    # 何が読みを止めたかを入れる。そこで下した判定は生の文字列に当てた結果なので、
    # 別に数えられないと、ガードの出力のどれだけが読み切れないまま出たものかを
    # あとから言えなくなる。
    degraded: str = ""
    # fallback は、ルールファイルを読めずに組み込みの既定で判定したことを示す。
    # そのとき効いているのはプロジェクトのルールではないので、記録を数えるときに
    # 混ぜられない。ガードが落ちたまま何回動いたかも、これでしか分からない。
    fallback: str = ""
    # detail は reason だけでは言えないことを運ぶ。読めなかったファイルのパスなど。
    # これが無いと、設置を誤った状態が「どのファイルのことか分からない理由」に見える。
    detail: str = ""
    rules: list[str] = field(default_factory=list)
    # paths は実行後の監視が保護領域の中に見つけた変更。件数ではなく綴りで
    # 残すのは、同じ場所が繰り返し汚れているのか毎回違う場所なのかで、
    # 直す先が変わるため。前者は出力先の設定 1 つ、後者は経路そのもの。
    paths: list[str] = field(default_factory=list)
    # guarded は、ccnavi 自身の設定ファイルについてこの 1 回で何をしたか。
    # ルールに当たった結果ではないので rules とも paths とも混ぜない。
    # 「戻した」が何回あったかは、ここを数えないと分からない。
    guarded: list[str] = field(default_factory=list)
    session: str = ""


class Log:
    """記録をファイルに追記する。パスが空なら書かない。
    診断のための実行が痕跡を残さずに済むようにするため。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._start = time.perf_counter()

    def write(self, record: Record) -> None:
        """1 件を追記する。時刻と経過時間はここで埋めるので、
        呼び手は自分が知ったことだけを渡せばよい。

        ここでの失敗は報告するが、判定は決して変えない。記録の 1 行を失うほうが、
        帳簿の都合でツールの実行可否が決まるより害が小さい。
        """
        if not self._path:
            return

        line = json.dumps(self._as_dict(record), ensure_ascii=False)

        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        # 同じイベントに登録された hook は同時に走るので、複数のプロセスが
        # このファイルに同時に追記する。1 行をまるごと 1 回の書き込みで、
        # 追記モードで開いたハンドルに出す。2 件が混ざらないのはそのため。
        fd = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)

    def _as_dict(self, record: Record) -> dict:
        subject = record.subject
        if len(subject) > SUBJECT_LIMIT:
            subject = subject[:SUBJECT_LIMIT] + f"…(+{len(record.subject) - SUBJECT_LIMIT})"

        elapsed_ms = (time.perf_counter() - self._start) * 1000
        out: dict = {
            "ts": datetime.now(UTC).astimezone().isoformat(),
            "mode": record.mode,
        }
        # 空の欄は落とす。1 行を目で追うのに、意味の無い欄は邪魔にしかならない。
        for key, value in (
            ("permission_mode", record.permission_mode),
            ("event", record.event),
            ("tool", record.tool),
            ("subject", subject),
        ):
            if value:
                out[key] = value

        out["decision"] = record.decision
        out["enforced"] = record.enforced

        for key, value in (
            ("code", record.code),
            ("reason", record.reason),
            ("degraded", record.degraded),
            ("fallback", record.fallback),
            ("detail", record.detail),
        ):
            if value:
                out[key] = value
        if record.rules:
            out["rules"] = record.rules
        if record.paths:
            out["paths"] = record.paths
        if record.guarded:
            out["guarded"] = record.guarded
        if record.session:
            out["session"] = record.session

        out["ms"] = round(elapsed_ms, 3)
        return out

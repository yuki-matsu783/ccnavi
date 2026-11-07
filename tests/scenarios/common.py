"""実行器の全モジュールが使う土台（段 0）。外を呼ばない。

定数・拒否の例外（Refusal）・書き出しの小道具・claude を呼ぶ道のゲート。
"""

import json
import os
from pathlib import Path

from tests import diaglog

ROOT = Path(__file__).resolve().parents[2]


# setup.git / setup.reset / setup.files の追記が触れてよい唯一のリポジトリ（ルートからの相対）
DISPOSABLE_REL = "projects/test-repo"

LOG_NAME = "scenario-runner"

RUNNER = "uv run python -m tests.scenarios.runner"

PASS, FAIL, INCONCLUSIVE, REVIEW = "pass", "fail", "inconclusive", "needs-review"

# claude を呼ぶ道は、`python -m tests.scenarios.runner` で起動されたときだけ開く。
# ほかのモジュール（全量テストに入るテストを含む）が import して呼んでも、ここで止まる。
# 全量テストに入らないのは「test_ で始まるモジュールが無いから」だけでなく、このゲートでも守る。
_ENTRY = False


def enable_entry():
    """入口（runner.py の `__main__`）が呼ぶ。これ以降、claude を呼ぶ道が開く。"""
    global _ENTRY
    _ENTRY = True


def require_entry():
    if not _ENTRY:
        raise RuntimeError(
            "受入シナリオは `python -m tests.scenarios.runner` から起動したときだけ回せる"
            "（テストや他のモジュールから呼ばない）"
        )


def write_state(out, st):
    (out / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


class Refusal(Exception):
    """回す前の拒否・停止。str() は、理由の行・`案内:` の行・最後に `次:` の 1 行。
    行頭の語は .agents/docs/script-output.md。"""

    def __init__(self, what, next_line, *notes):
        super().__init__(what)
        self.what, self.next_line, self.notes = what, next_line, notes

    def __str__(self):
        return "\n".join([self.what, *[f"案内: {n}" for n in self.notes], f"次: {self.next_line}"])


def fold(path):
    """パスと名前を比べる形。区切りは機械のもの、大文字小文字はどの機械でも区別しない。

    実行ファイルの pathcase.Fold と同じ比べ方。
    """
    return os.path.normcase(str(path)).casefold()


def log(root):
    return diaglog.get(LOG_NAME, str(root))


def clip(text, limit, where):
    """limit を超えたら切り、切ったことと全量の在りかを末尾に書く。"""
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（全 {len(text)} 文字のうち先頭 {limit} 文字。全量は {where}）"


def refuse(exc):
    """回す前の拒否・停止の文面（理由・案内:・次:）を出して、終了コード 3 を返す。"""
    print(exc)
    return 3

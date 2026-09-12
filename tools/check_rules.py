"""見本をぜんぶ判定に掛けて、期待と食い違ったものを並べる。

`/ccnavi-config` スキルが呼ぶ。人が直接叩いてもよい。

    uv run python tools/check_rules.py [ルールファイル]

中身は `ccnavi --test-samples` の薄い皮。見本の読み方も突き合わせも実行ファイルの
側にあり、ここは引数を足して呼ぶだけ。VS Code 拡張のルール設定画面も同じ口を
`--json` 付きで叩く。判定を 2 か所で作らないのが肝で、別の道で確かめると、
見本が通ったのに実運用で落ちる、という一番まずい形になる（REQ-DIA-03）。

終了コードは実行ファイルのものをそのまま返す。食い違いが 1 件でもあれば 1。無ければ 0。
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, ".claude", "ccnavi", "rule-samples.yml")


def main() -> int:
    rules_path = (
        sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, ".claude", "ccnavi", "rules.yml")
    )
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "ccnavi",
            "--root",
            ROOT,
            "--rules",
            rules_path,
            # 見るのはルールだけ。写しと控えは外し、記録も残さない。
            "--approved",
            "",
            "--state",
            "",
            "--log",
            "",
            "--test-samples",
            SAMPLES,
        ],
        cwd=ROOT,
        env=environment,
    )
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())

"""配布物の入口。

`python -m ccnavi` とは別に置いてある。PyInstaller は指定されたスクリプトを
パッケージの外の素のスクリプトとして走らせるので、パッケージ内の入口を直接
渡すと相対 import が解決できない。ここは絶対 import で書く。
"""

from __future__ import annotations

import sys

from ccnavi import cli, hookio


def main() -> int:
    hookio.rebind_streams()
    return cli.run(sys.stdin, sys.stdout, sys.stderr, sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())

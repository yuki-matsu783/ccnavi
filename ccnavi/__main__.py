"""入口。標準入出力を整えて cli.run を呼ぶだけ。"""

from __future__ import annotations

import sys

from . import cli, hookio


def main() -> int:
    hookio.rebind_streams()
    return cli.run(sys.stdin, sys.stdout, sys.stderr, sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())

"""Python の内部（src/ccnavi/）を直に import する白箱のテストのために、src を sys.path に足す。

共通の部品（tests/ 直下と tests/sh の土台）は ccnavi の Python を読まない。白箱のテストを
go test へ移し終えて整理したら、このモジュールと、グループの `__init__.py` の import を消す。
"""

import os
import sys

from tests import MESSAGES, SRC

if SRC not in sys.path:
    sys.path.insert(0, SRC)


def _use_repo_messages_when_absent() -> None:
    """同じプロセスで動く Python の文面の置き場を、文面の無い木ではこのリポジトリのものにする。

    実行ファイルを起こすテストには効かない（そちらは tests/inproc.py が木にコピーする）。
    """
    from ccnavi.policy import texts

    real = texts.dirs

    def dirs(root: str) -> str:
        where = real(root)
        return where if os.path.isdir(where) else MESSAGES

    texts.dirs = dirs


_use_repo_messages_when_absent()

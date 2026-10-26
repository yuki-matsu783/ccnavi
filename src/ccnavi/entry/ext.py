"""Chrome 拡張（Pyodide）が呼ぶ本体の公開の入口。

拡張はここで再公開されるモジュールだけを呼ぶ。本体の内部のモジュールを改名・移動しても、
ここの行を直せば拡張は動く。拡張が引く属性は、`tests/ticket/test_ext_entry.py` が
`ccnavi_chrome.py` の実際の使用から集め、本体にあることを確かめる。
ロジックは持たない。再公開だけ。
"""

from __future__ import annotations

from ..infra import fsio as fsio
from ..infra import settings as settings
from ..tickets import core as core
from ..tickets import review as review
from ..tickets import review_host as review_host
from ..tickets import ticket as ticket
from ..tickets import ticket_ids as ticket_ids
from ..tickets import ticket_model as ticket_model
from ..tickets import via as via
from . import cli as cli
from . import lint as lint
from . import version as version

__all__ = [
    "cli",
    "core",
    "fsio",
    "lint",
    "review",
    "review_host",
    "settings",
    "ticket",
    "ticket_ids",
    "ticket_model",
    "version",
    "via",
]

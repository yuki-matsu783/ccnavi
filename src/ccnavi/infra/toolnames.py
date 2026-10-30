"""Claude Code のツール名の一覧。

どの判定も同じ名前の組を使う。モジュールごとに書き直すと、ツールが増えたときに
直し漏れが出て、判定が食い違う。ツール名の増減はここだけを直す。
"""

from __future__ import annotations

# ワークツリーのファイルを書き換えるツール。
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
# 書いたファイルの識別子を記録に残すツール（MultiEdit を含む）。
WRITTEN_FILE_TOOLS = ("Write", "Edit", "MultiEdit")
# サブエージェントの起動ツール。Claude Code の版によって Agent か Task で来る。
AGENT_TOOLS = ("Agent", "Task")
# サブエージェントが親へ報告を返すためのツール。判定に達せなかったときも止めない
# （止めると、体が報告を返せずに終われない）。
REPORT_TOOLS = ("SubagentHandback", "StructuredOutput")
# 読み取りだけで、パスを持つツール。
READ_TOOLS = ("Read", "Grep", "Glob")
# シェルを実行するツール。
SHELL_TOOLS = ("Bash", "PowerShell")
# 探す場所を省略できるツール。省略は「いま居る場所」の意味なので、cwd を対象にする。
# 空のままにすると、場所を書かない呼び出しがどのルールにもヒットせずに通る。
SEARCH_TOOLS = ("Grep", "Glob")

# 行き先のパスで判定するツール。subject に解決済みのパスが入っている。
PATH_TOOLS = (*READ_TOOLS, "Edit", "Write", "NotebookEdit")
# 判定に達せなかったときに止めるツール。
STRICT_TOOLS = (*WRITE_TOOLS, *AGENT_TOOLS)
# 実行後チェックを省ける、読み取りだけのツール。
READ_ONLY_TOOLS = (*READ_TOOLS, "WebFetch", "WebSearch")

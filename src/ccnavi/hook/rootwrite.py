"""シェルからワークスペースルート直下へ書き込む形を、実行前に止める。

Write / Edit は、ルールファイルの `workspace-root` が止める（チェックアウトしたブランチ直下は
編集しない。`wip/handoff/`・`scratchpad/`・`.claude/worktrees/` だけ書ける）。シェルでも同じ場所を
書けると、`sed -i`・`>`・`tee` などで同じ決まりを素通りできる。対象は Bash だけ
（PowerShell は `no-powershell` のルールが丸ごと止める）。そこで、シェルの書き込み先を
`parentwrite` と同じ読みで取り出し、解いた絶対パスを同じルールに Write として当てる。
置き場の決まりをここに複写しない。ルールを直せば、Write とシェルの両方が変わる。
ガード自身の保護の場所は、シェルから書く形を止める専用の保護が先に止めるので、この関数は
ほかの deny に当たらなかった呼び出しに限って使う。

読み方と限界は `parentwrite` のとおり。インタプリタに渡したコード（`python -c`）、スクリプトの
ファイルを実行する形、変数・置換で組み立てた書き込み先、居場所が読めない相対パスは見ない。
それらは実行後チェックが、作業ツリーの実物を見て戻す。
"""

from __future__ import annotations

from ..infra import settings
from ..policy import rules
from . import parentwrite

CODE = "DENY_SHELL_WORKSPACE_ROOT"
RULE_ID = "builtin:guard-root-shell-write"  # 文面の見出し。記録のルール名は当てたルール
# 当てるルールの id。ルールファイルの Write / Edit の決まり。
SOURCE_RULE_ID = "workspace-root"


def check(
    rule_set: rules.RuleSet, cwd: str, subject: str, layers: list[str]
) -> tuple[str, str, rules.Rule | None]:
    """書き込み先がルート直下の決まりに当たれば（書いたとおりの語, 解いたパス, 当てたルール）。

    当たらなければ空と None。当てるルールは id が `workspace-root` のもの。
    共通レイヤー以外のレイヤーのルールは、id に `<レイヤー名>:` が付くので、
    その形も同じ決まりとして扱う。
    """
    found = [
        r for r in rule_set.deny if r.id == SOURCE_RULE_ID or r.id.endswith(f":{SOURCE_RULE_ID}")
    ]
    if not found:
        return "", "", None
    for target, base in parentwrite.write_targets(cwd, subject, layers):
        path = parentwrite.resolve_word(target, base)
        if not path:
            continue
        for rule in found:
            if rule.matches("Write", path):
                return target, path, rule
    return "", "", None


def message(root: str, target: str, path: str) -> str:
    worktree_sh = settings.script_command(root, "ccnavi-worktree.sh")
    return (
        f"シェルでチェックアウトしたブランチ直下（{path}）へ書き込む形です"
        f"（書き込み先: {target}）。"
        "Write / Edit と同じく、ここには書きません。"
        "ユーザに渡すものはワークスペースルート直下の wip/handoff/、"
        "一時的なもの・コミットしたくないものは同じく直下の scratchpad/ に書いてください"
        "（.ccnavi/docs/scratchpad.md）。"
        f"リポジトリのファイルを直すときは、{worktree_sh} <名前> でワークツリーを切り、"
        "その中で編集します。"
    )

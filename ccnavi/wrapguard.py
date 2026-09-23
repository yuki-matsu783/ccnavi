"""保護済みの sh（`.ccnavi/scripts/ccnavi-*.sh`）を呼ぶ形のうち、実行前に止めるもの 2 つ。

1. **sh の検査の材料を変える環境変数を、同じコマンド行で置く形。** sh はワークスペースルート・
   承認済みチケットの置き場・実行ファイルを環境変数から読む（`ccnavi-common.sh`）。
   `CCNAVI_TICKETS_APPROVED=/x sh …ccnavi-git.sh push` は、存在しない置き場を見て子の push を
   通す。拒否されたエージェントが言い換えて再試行する形そのもので、§2.1 の「逸れていく LLM」の
   範囲に入る。hook は `settings.json` の env で起動するので、ここで見る代入の影響を受けない。
2. **子チケットのワークツリーからの `ccnavi-git.sh push`。** 子の成果は親が合流してから親の
   ツリーで送る（設計 §9.10）。sh も同じ検査を持つが、sh の検査は上の環境変数で外れ、承認済み
   チケットの探し方を sh の中に写して持つので、写しがずれると黙って外れる（#120）。止める場所を
   hook に置き、sh の検査は 2 重目として残す（ADR-0077）。

どちらも組み込み。ルールを空にされても消えないように、守りの根拠を守られる対象の外に置く（P4）。

止める広さは sh の検査に合わせる。登録されていない `.claude/worktrees/<名前>` も、閉じた子
（`done/`）も、レビュー待ちの子（`review/`）も、YAML として読めない承認済みチケットも、
`parent:` の行があれば子と見る。Python の読み方（`approval.scan`）はどれも落とすので、
そちらに寄せると止まっていたものが通るようになる。
"""

from __future__ import annotations

import os
import re

from . import settings, shellread, tree
from . import ticket as ticket_mod

CODE_ENV = "DENY_SCRIPT_ENV_OVERRIDE"
CODE_CHILD_PUSH = "DENY_CHILD_PUSH"
ENV_RULE_ID = "builtin-script-env"
CHILD_PUSH_RULE_ID = "builtin-child-push"

# 保護済みの sh の名前。置き場（`.ccnavi/scripts/`）は `CCNAVI_PROJECT_HOME` で変わりうるので、
# 名前で見る。
_SCRIPT = re.compile(r"ccnavi-[A-Za-z0-9-]+\.sh")
_GIT_SCRIPT = "ccnavi-git.sh"

# 同じコマンド行で置いてよい変数。出力の量と待ち時間だけを変え、検査の材料には触れない。
# 通すものを並べる形にして、あとから sh が読むようになった変数は止まる側に倒す。
PASS_THROUGH = frozenset(
    {
        "CCNAVI_GIT_MAX_LINES",
        "CCNAVI_GIT_FAIL_LINES",
        "CCNAVI_GIT_KEEP_LOGS",
        "CCNAVI_FETCH_TIMEOUT",
    }
)
# `CCNAVI_` で始まるもののほかに止める変数。sh のワークスペースルートの探し方に効く。
_ALSO_BLOCKED = frozenset({"CLAUDE_PROJECT_DIR"})

# 引数に変数名を取り、その変数を置く・外す・書き出すコマンド。
_DECLARES = frozenset({"export", "declare", "typeset", "readonly", "local", "unset"})
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?\+?=")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def blocked(name: str) -> bool:
    """同じコマンド行で置くと、保護済みの sh を呼ぶ形を止める変数か。"""
    if name in PASS_THROUGH:
        return False
    return name.startswith("CCNAVI_") or name in _ALSO_BLOCKED


def check_env(subject: str, degraded: str) -> tuple[str, list[str]]:
    """保護済みの sh を呼ぶコマンド行に、止める変数の代入があれば（sh の綴り, 変数名）。

    コマンド行のどこで置いても数える。前置きの代入（`X=… sh …`）、`env X=…`、`export X=…`、
    すでに書き出してある変数への素の代入（`X=…; sh …`。settings.json の env が書き出した変数は
    代入だけで sh に届く）、`unset X`・`env -u X`。読み切れない形は、allow が当たらずに
    確認へ落ちるので、ここでは見ない。
    """
    if degraded:
        return "", []
    places = shellread.placed(subject)
    scripts = [s for s in (_script_call(words) for words, _ in places) if s]
    if not scripts:
        return "", []
    names: list[str] = []
    for words, _ in places:
        names.extend(n for n in _set_names(words) if blocked(n))
    return scripts[0][0], list(dict.fromkeys(names))


def check_child_push(
    conf: settings.Settings, root: str, cwd: str, subject: str, degraded: str
) -> tuple[str, str, str]:
    """子チケットのワークツリーからの `ccnavi-git.sh push` なら（ツリーの名前, 親, 見つけた綴り）。

    居場所は payload の cwd から `cd` を追った先（§6.3.2）。追えなくなった先で push を
    打つ形は、どのツリーから送るのかが決まらないので止める（親は `?`）。
    """
    if degraded or not cwd:
        return "", "", ""
    for words, here in shellread.placed(subject):
        call = _script_call(words)
        if not call or os.path.basename(call[0].replace("\\", "/")) != _GIT_SCRIPT:
            continue
        if call[1][:1] != ["push"]:
            continue
        if here is None:
            return "?", "?", ""
        place = here if os.path.isabs(here) else os.path.join(cwd, here)
        name = worktree_name(root, place)
        if not name:
            continue
        parent, found = child_parent(conf, root, name)
        if parent:
            return name, parent, found
    return "", "", ""


def worktree_name(root: str, place: str) -> str:
    """place がワークスペースの `.claude/worktrees/<名前>` の中なら、その名前。

    git に登録されているかは問わない（sh の検査と同じ広さ）。symlink は実際のパスに畳んで
    比べる。論理のパスと実際のパスを混ぜると、`/tmp` → `/private/tmp` のような場所で外れる。
    """
    base = tree._canonical(os.path.join(root, tree.WORKTREES_DIR))
    target = tree._canonical(place)
    if not target.startswith(base + os.sep):
        return ""
    return target[len(base) + 1 :].split(os.sep, 1)[0]


def child_parent(conf: settings.Settings, root: str, name: str) -> tuple[str, str]:
    """その名前の承認済みチケットが子なら（親, 見つけたファイル）。子でなければ空。

    探すのは、ワークスペースルート・プロジェクトの置き場の下・`.claude/worktrees/` の下の
    ディレクトリ全部の `doing/`・`done/` と `review/`。git のリポジトリかどうかは問わない。
    読めないファイルは「子かもしれない」として止める側に倒す（親は `?`）。
    """
    for top in _trees(conf, root):
        approved = settings.approved_dir(conf, top)
        proposals = os.path.join(top, (conf.tickets or "").replace("/", os.sep))
        for path in (
            os.path.join(approved, ticket_mod.DOING, name + ".md"),
            os.path.join(approved, ticket_mod.DONE, name + ".md"),
            os.path.join(proposals, ticket_mod.REVIEW, name + ".md"),
        ):
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                return "?", path
            match = re.search(r"^parent:[ \t]*(.*?)[ \t]*$", text, re.MULTILINE)
            if match:
                return match.group(1).strip("'\"") or "?", path
    return "", ""


def _trees(conf: settings.Settings, root: str) -> list[str]:
    tops = [root]
    for base in (conf.projects, os.path.join(root, tree.WORKTREES_DIR)):
        if not base:
            continue
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        tops.extend(os.path.join(base, n) for n in names if os.path.isdir(os.path.join(base, n)))
    return tops


def _script_call(words: list[str]) -> tuple[str, list[str]] | None:
    """コマンド 1 本が保護済みの sh を呼ぶなら（sh の綴り, sh に渡る引数）。

    `sh <sh>`・`bash <sh>`・`<sh>` の直接の実行・`. <sh>`、と、それを `env` `sudo` などの
    実行役のコマンド越しに呼ぶ形。`sh -c '…'` は読み切れない形として read() が縮退させる。
    """
    words = _strip_reserved(words)
    depth = 0
    while words and depth <= shellread.UNWRAP_DEPTH:
        k = shellread._name_index(words)
        if k >= len(words):
            return None
        name = shellread._base(words[k])
        args = words[k + 1 :]
        if name in shellread._RUNNERS:
            inner = shellread._runner_command(name, args)
            if not inner:
                return None
            words = inner[0]
            depth += 1
            continue
        if name in shellread._SHELLS or name in shellread._SOURCES:
            i = 0
            while i < len(args) and args[i][:1] in ("-", "+") and args[i] != "-":
                if args[i] in shellread._SHELL_VALUE_OPTIONS:
                    i += 1
                i += 1
            if i < len(args) and _is_script(args[i]):
                return args[i], args[i + 1 :]
            return None
        if _is_script(words[k]):
            return words[k], args
        return None
    return None


def _is_script(word: str) -> bool:
    return bool(_SCRIPT.fullmatch(os.path.basename(word.replace("\\", "/"))))


def _set_names(words: list[str]) -> list[str]:
    """コマンド 1 本が置く・外す変数の名前。実行役のコマンドの層も見る。"""
    names: list[str] = []
    words = _strip_reserved(words)
    depth = 0
    while words and depth <= shellread.UNWRAP_DEPTH:
        k = shellread._name_index(words)
        names.extend(_assigned(w) for w in words[:k] if _assigned(w))
        if k >= len(words):
            break
        name = shellread._base(words[k])
        args = words[k + 1 :]
        if name == "env":
            names.extend(_env_names(args))
        if name in _DECLARES:
            for arg in args:
                if arg[:1] in ("-", "+"):
                    continue
                assigned = _assigned(arg)
                if assigned:
                    names.append(assigned)
                elif _NAME.fullmatch(arg):
                    names.append(arg)
            break
        if name not in shellread._RUNNERS:
            break
        inner = shellread._runner_command(name, args)
        if not inner:
            break
        words = inner[0]
        depth += 1
    return names


def _env_names(args: list[str]) -> list[str]:
    """`env` が置く・外す変数。`-u X`・`--unset=X`・`X=…`。"""
    names: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-u", "--unset") and i + 1 < len(args):
            names.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("--unset="):
            names.append(arg.partition("=")[2])
        elif arg.startswith("-u") and len(arg) > 2:
            names.append(arg[2:])
        elif _assigned(arg):
            names.append(_assigned(arg))
        elif not arg.startswith("-"):
            break
        i += 1
    return names


def _assigned(word: str) -> str:
    match = _ASSIGNMENT.match(word)
    return match.group(1) if match else ""


def _strip_reserved(words: list[str]) -> list[str]:
    i = 0
    while i < len(words) and words[i] in shellread._RESERVED:
        if words[i] in shellread._TAKES_A_WORD:
            return []
        i += 1
    return words[i:]


def env_message(script: str, names: list[str]) -> str:
    """環境変数の代入で止めた文。"""
    shown = ", ".join(names)
    return (
        f"保護済みの sh（{script}）を、sh の検査の材料を変える環境変数（{shown}）と"
        "同じコマンド行で呼んでいます。この変数は sh がワークスペースルート・承認済みチケットの"
        "置き場・実行ファイルを決めるのに使うので、書き換えると sh の検査が外れます。"
        "変数を置かずに sh をそのまま打ってください。置き場が本当に違うなら、利用者に"
        "settings.json の env を直してもらってください。出力の量と待ち時間の変数（"
        + ", ".join(sorted(PASS_THROUGH))
        + "）は置けます。"
    )


def child_push_message(name: str, parent: str) -> str:
    """子のワークツリーからの push を止めた文。sh の文面と揃える。"""
    if name == "?":
        return (
            "ccnavi-git.sh push をどのツリーから打つのかが読めません（`cd` の行き先に変数・"
            "置換・グロブがある、など）。子チケットのワークツリーからは送らないので、"
            "送るツリーが決まらない形は通しません。`cd` の行き先をそのまま書くか、"
            "送るツリーに入ってから打ち直してください。"
        )
    return (
        f"{name} は子チケットのワークツリーです。子のブランチはリモートへ送りません。"
        f"親（{parent}）が子の成果を取り込んでから、親のワークツリー "
        f"(.claude/worktrees/{parent}) で送ります。子は作業を終えたら結果を報告して"
        "終わってください。"
    )

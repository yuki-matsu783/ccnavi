"""保護済みの sh（`.ccnavi/scripts/ccnavi-*.sh`）を呼ぶ形のうち、実行前に止めるもの 3 つ。

1. sh の検査の材料を変える環境変数を、同じコマンド行で置く形。sh はワークスペースルート・
   実行ファイルを環境変数から読む（`ccnavi-common.sh` とその部品の
   `ccnavi-common-*.sh`）。
   `CCNAVI_WORKSPACE=<子のワークツリー> sh …ccnavi-git.sh push` は、子のワークツリーを
   ワークスペースルートと読ませ、`.claude/worktrees/` の下に居ないことにして子の push を
   通す。拒否されたエージェントが言い換えて再試行する形そのもので、脅威モデルの「逸れていく LLM」の
   範囲に入る。hook は `settings.json` の env で起動するので、ここで見る代入の影響を受けない。
2. 子チケットのワークツリーからの `ccnavi-git.sh push`。子の成果は親が合流してから親の
   ツリーで送る。sh も同じ検査を持つが、sh の検査は上の環境変数で外れ、承認済み
   チケットの探し方を sh の中にコピーして持つので、
   コピーが食い違うと気づかないうちに外れる（#120）。止める場所を hook に置き、
   sh の検査は 2 重目として残す。
3. `ccnavi-git.sh` の `rm`・`mv`・`restore --source`・`checkout <ref> <パス>` で、自己防衛の対象
   （hook の登録・設定・保護済みのスクリプト・実行ファイル・記録）を消す・別のコミットの
   中身に戻す形。
   シェルの `rm` と Write / Edit は自己防衛の組み込みが止めるが、ラッパーを通すと `git rm` が
   同じファイルを消せた。ラッパー自身を消しても、生の git の組み込みの止めは外れない。
   sh も同じ場所の検査を 2 重目として持つ。承認済みチケットの置き場と
   レビュー待ちの置き場（`wip/proposals/review/`）もエージェントからは止める（sh は `rm` を通す）。

どれも組み込み。ルールを空にされても消えないように、保護の根拠を守られる対象の外に置く。

止める広さは sh の検査に合わせる。登録されていない `.claude/worktrees/<名前>` も、閉じた子
（`done/`）も、レビュー待ちの子（`review/`）も、YAML として読めない承認済みチケットも、
`parent:` の行があれば子と見る。Python の読み方（`approval.scan`）はどれも対象から外すので、
そちらにそろえると止まっていたものが通るようになる。
"""

from __future__ import annotations

import os
import re

from ..infra import pathcase, settings, shellread, shellread_words, tree
from ..policy import selfguard_shell, texts
from ..tickets.model import ticket_model

CODE_ENV = "DENY_SCRIPT_ENV_OVERRIDE"
CODE_CHILD_PUSH = "DENY_CHILD_PUSH"
CODE_CORE_PATH = "DENY_WRAPPER_CORE_PATH"
CODE_PARENT_TREE = "DENY_SUBAGENT_PARENT_TREE"
ENV_RULE_ID = "builtin:script-env"
CHILD_PUSH_RULE_ID = "builtin:child-push"
CORE_PATH_RULE_ID = "builtin:guard-wrapper-paths"
PARENT_TREE_RULE_ID = "builtin:subagent-parent-tree"

# 保護済みの sh の名前。置き場は `.ccnavi/scripts/` に固定だが、呼び出しの綴りは
# 絶対パス・相対パス・ワークツリーの中のコピーとまちまちなので、パスではなく名前で見る。
# 名前の大小は問わない。Windows と macOS の既定のファイルシステムでは `CCNAVI-GIT.SH` も同じ
# ファイルで、区別すると表記を変えるだけで外せる。
_SCRIPT = re.compile(r"ccnavi-[A-Za-z0-9-]+\.sh", re.IGNORECASE)
_GIT_SCRIPT = "ccnavi-git.sh"

# 同じコマンド行で置いてよい変数。出力の量と待ち時間だけを変え、検査の材料には触れない。
# 通すものを並べる形にして、あとから sh が読むようになった変数は止まる側にする。
PASS_THROUGH = frozenset(
    {
        "CCNAVI_GIT_MAX_LINES",
        "CCNAVI_GIT_FAIL_LINES",
        "CCNAVI_GIT_KEEP_LOGS",
        "CCNAVI_FETCH_TIMEOUT",
    }
)
# `CCNAVI_` で始まるもののほかに止める変数。sh のワークスペースルートの探し方に影響する。
_ALSO_BLOCKED = frozenset({"CLAUDE_PROJECT_DIR"})
# `GIT_` で始まる変数のうち、同じコマンド行で置いてよいもの。どれも作者の名前と出力の形だけを
# 変え、git が操作するリポジトリ・ワークツリー・索引・設定には触れない。ほかの `GIT_` は止める。
# `GIT_DIR=<親>/.git GIT_WORK_TREE=<親> sh …ccnavi-git.sh commit` を子のワークツリーで打つと、
# 居場所の検査は子を見るのに、git は親のワークツリーへコミットする（ラッパーも先頭で外す）。
_GIT_PASS_THROUGH = frozenset(
    {
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
        "GIT_PAGER",
        "GIT_TERMINAL_PROMPT",
        "GIT_MERGE_AUTOEDIT",
    }
)

# 引数に変数名を取り、その変数を置く・外す・書き出すコマンド。
_DECLARES = frozenset({"export", "declare", "typeset", "readonly", "local", "unset"})
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?\+?=")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# `env -i` は環境を丸ごと空にする。settings.json の env が書き出した置き場も消え、sh は既定の
# 置き場を見る。変数の名前は並ばないので、この表記で数える。
ENV_CLEARED = "env -i"
_ENV_CLEARS = frozenset({"-i", "--ignore-environment", "-"})


def blocked(name: str) -> bool:
    """同じコマンド行で置くと、保護済みの sh を呼ぶ形を止める変数か。"""
    if name in PASS_THROUGH or name in _GIT_PASS_THROUGH:
        return False
    return name.startswith(("CCNAVI_", "GIT_")) or name in _ALSO_BLOCKED


def check_env(subject: str, degraded: str) -> tuple[str, list[str]]:
    """保護済みの sh を呼ぶコマンド行に、止める変数の代入があれば（sh のパス, 変数名）。

    コマンド行のどこで置いても数える。前置きの代入（`X=… sh …`）、`env X=…`、`export X=…`、
    すでに書き出してある変数への素の代入（`X=…; sh …`。settings.json の env が書き出した変数は
    代入だけで sh に届く）、`unset X`・`env -u X`。読み切れない形は、allow がヒットせずに
    確認になる。
    """
    if degraded:
        return "", []
    places = shellread.placed(subject)
    scripts = [s for s in (_script_call(words) for words, _ in places) if s]
    if not scripts:
        return "", []
    names: list[str] = []
    for words, _ in places:
        names.extend(n for n in _set_names(words) if n == ENV_CLEARED or blocked(n))
    return scripts[0][0], list(dict.fromkeys(names))


def check_child_push(
    conf: settings.Settings, root: str, cwd: str, subject: str, degraded: str
) -> tuple[str, str, str]:
    """子チケットのワークツリーからの `ccnavi-git.sh push` なら（ツリーの名前, 親,
    見つけた文字列）。

    居場所は payload の cwd から `cd` と実行役の行き先（`env -C`・`sudo -D`）を追った先。
    追えなくなった先で push を
    打つ形は、どのツリーから送るのかが決まらないので止める（親は `?`）。
    """
    if degraded or not cwd:
        return "", "", ""
    for words, here in shellread.placed(subject):
        call = _script_call_at(words)
        if not call or not _is_git_script(call[0]):
            continue
        if call[1][:1] != ["push"]:
            continue
        place = _call_place(cwd, here, call[2])
        if place is None:
            return "?", "?", ""
        name = worktree_name(root, place)
        if not name:
            continue
        parent, found = child_parent(conf, root, name)
        if parent:
            return name, parent, found
    return "", "", ""


# サブエージェントが親のワークツリーで打てないラッパーのサブコマンド。
# 親のワークツリーの中身・
# 索引・ブランチの先頭を動かすもの。読むサブコマンド（status・log・diff など）は止めない。
# 合流・コミット・push はメインが finish で行う。履歴を書き換えるサブコマンド（cherry-pick・revert・
# rebase など）はラッパーも通さないが、ここでも名指しして止める
# （ラッパーの許可が広がっても、親のワークツリーには届かない）。
_PARENT_WRITES = frozenset(
    {
        "add",
        "rm",
        "mv",
        "restore",
        "checkout",
        "switch",
        "stash",
        "merge",
        "commit",
        "pull",
        "cherry-pick",
        "revert",
        "rebase",
        "am",
        "apply",
        "reset",
        "clean",
        "integrate",
        "integrate-done",
    }
)


def check_parent_tree_git(
    conf: settings.Settings, root: str, cwd: str, subject: str, degraded: str
) -> tuple[str, str]:
    """サブエージェントの `ccnavi-git.sh` が親のワークツリーを書き換える形なら
    （サブコマンド, 親のワークツリーの名前）。

    居場所は payload の cwd から `cd` と実行役の行き先（`env -C`・`sudo -D`）を追った先
    （子のワークツリーでのコミットと、子のワークツリーで
    親のブランチを取り込む merge は通す）。居場所を追えない形（`cd` の行き先に変数・置換がある、
    読み切れないコマンド行）で書き換えるサブコマンドを打つ形は、どのツリーにヒットするかが決まらないので
    止める（名前は `?`）。
    """
    if _GIT_SCRIPT not in subject.lower():
        return "", ""
    if degraded:
        for name in _DEGRADED_NAME.finditer(subject):
            sub = _DEGRADED_SUB.match(subject, name.end())
            if not sub or sub.group(1).lower() in _PARENT_WRITES:
                return (sub.group(1).lower() if sub else "?"), "?"
        return "", ""
    for words, here in shellread.placed(subject):
        call = _script_call_at(words)
        if not call or not _is_git_script(call[0]):
            continue
        args = call[1]
        if not args:
            continue
        if _OPEN_SUB.search(args[0]):
            return args[0], "?"
        if args[0].lower() not in _PARENT_WRITES:
            continue
        place = _call_place(cwd, here, call[2]) if cwd else None
        if place is None:
            return args[0], "?"
        name = worktree_name(root, place)
        if name and ticket_role(conf, root, name)[0] == ROLE_PARENT:
            return args[0], name
    return "", ""


def parent_tree_message(root: str, sub: str, name: str) -> str:
    """サブエージェントが `ccnavi-git.sh` で親のワークツリーを書き換えるのを止めた文。"""
    if name == "?":
        return texts.say(root, "DENY_SUBAGENT_PARENT_TREE__GIT_UNREAD", {"sub": sub})
    return texts.say(root, "DENY_SUBAGENT_PARENT_TREE__GIT", {"sub": sub, "name": name})


def parent_write_message(root: str, what: str, name: str, kind: str = "write") -> str:
    """サブエージェントが親のワークツリーへファイルを書き込むのを止めた文。

    what は書き込みの形（ツールの名前か、シェルの書き込み先・コードの文字列）。
    """
    if name == "?":
        return texts.say(root, "DENY_SUBAGENT_PARENT_TREE__WRITE_UNREAD", {"what": what})
    if kind == "code":
        return texts.say(
            root, "DENY_SUBAGENT_PARENT_TREE__WRITE_CODE", {"name": name, "what": what}
        )
    return texts.say(root, "DENY_SUBAGENT_PARENT_TREE__WRITE", {"name": name, "what": what})


# ラッパーのサブコマンドのうち、行き先のファイルを消す・別のコミットの中身に戻すもの。
# `restore` は `--source` を付けた形だけ（付けない形は索引の中身に戻すだけで、衝突を解く
# `--ours` / `--theirs` もこちら）。`checkout` は 2 つ目からの語がパス（`checkout <ref> <パス>`）。
# `mv` はラッパーが通さないが、通すようになっても同じ場所を守るよう数えておく。
_CORE_SUBS = frozenset({"rm", "mv", "restore", "checkout"})
# 値を次の語で取るオプション（`=` でつないだ形は 1 語で済む）。値をパスと読み違えないため。
_VALUE_LONGS = {
    "restore": frozenset({"--source", "--conflict", "--pathspec-from-file"}),
    "checkout": frozenset({"--orphan", "--conflict", "--pathspec-from-file"}),
    "rm": frozenset({"--pathspec-from-file"}),
    "mv": frozenset(),
}
_VALUE_SHORTS = {"restore": "s", "checkout": "bB", "rm": "", "mv": ""}
# どこにヒットするかを読みで決められない pathspec。グロブは `/` もまたぐ（git の pathspec）ので、
# 文字の前の固定の部分（ディレクトリ）より下のどこにでもヒットしうる。
_GLOB = re.compile(r"[*?\[]")
# ディレクトリがこの名前を直に持つなら、守る場所を中に含む
# （ツリーの頂上・ccnavi ディレクトリの親）。
_MARKERS = (settings.DEFAULT_PROJECT_HOME, ".claude", ".git")


# 止めた理由の種類。文面を分ける。
CORE_HIT = "core"
CORE_STORE = "store"
CORE_REVIEW = "review"
CORE_UNREAD = "unread"
CORE_INDEX = "index"
# 引数の中で、シェルが実行の時点で値を決める書き方（変数・置換）。
_EXPANDS = re.compile(r"[$`]")
# サブコマンドの語に現れてはいけない文字（展開・置換・glob）。
_OPEN_SUB = re.compile(r"[$`*?\[]")
# 走査が読み切れなかったときに、生の文字列でラッパーの名前の後ろのサブコマンドを読む。
_DEGRADED_NAME = re.compile(r"ccnavi-git\.sh['\"]?", re.IGNORECASE)
_DEGRADED_SUB = re.compile(r"[ \t]+([A-Za-z][\w-]*)(?=[\s'\";&|)]|$)")


def check_core_paths(
    conf: settings.Settings, root: str, cwd: str, subject: str, degraded: str
) -> tuple[str, str, str]:
    """`ccnavi-git.sh` で自己防衛の対象を消す・別のコミットの中身に戻す形なら
    （サブコマンド, ヒットした語, 理由の種類）。

    ヒットするとみなすのは次のどれか。

    - シェルから書き込む形の組み込み（`builtin:guard-setting-files`）と同じ場所の式に、
      書いたとおりのパスか、居場所から解いた絶対パスがヒットする（`.ccnavi/`・
      `.claude/settings*.json`・`.claude/hooks/`・`ccnavi-git.sh`・記録と state・
      実行ファイル・動かした共通レイヤー）
    - そのパスが守る場所を中に含むディレクトリ（ツリーの頂上・`.ccnavi` や `.claude` を持つ
      ディレクトリとその 1 つ上・実行ファイルや共通レイヤーの上・プロジェクトの置き場の上）
    - 承認済みチケットの置き場（`CORE_STORE`）。レビュー済みのマーカーを消すのはユーザがターミナルで
      打つ手順で、sh は通す。エージェントの呼び出しはここで止めて、ユーザに依頼させる
    - レビュー待ちの置き場（`wip/proposals/review/` とその下。`CORE_REVIEW`）。承認済みチケットの
      置き場と同じ扱いで、sh は `rm` を通す。置き場の親（`wip/`・`wip/proposals/`）は、置き場に
      何かがあるときだけヒットする（親を閉じた後の `rm -r wip` の案内を止めないため）
    - ヒットする先を読みで決められない指定（`:` で始まる pathspec、固定の部分の無いグロブ、
      `--pathspec-from-file`）
    - 対象を読み切れない形（`CORE_UNREAD`）。変数や置換で組み立てた語、居場所の読めない
      `cd` / `pushd` の後の相対パス、走査が読み切れないコマンド行。共通ルールの allow
      （`*ccnavi-git.sh*`）が生の文字列にヒットするので、ここで見ないと確認に落ちずに通る
    - `checkout -- <パス>`（索引の中身に戻す。`CORE_INDEX`）。sh はこの形を通さないので、
      sh と同じく restore を案内する

    通すのは、索引の中身に戻す `restore`（`--staged` も、衝突を解く `--ours` / `--theirs` も）と、
    何も消さない `rm -n` / `--dry-run`。消える・過去の中身になるのでないものは、ルールと
    ラッパーの許可リストに任せる。
    """
    if _GIT_SCRIPT not in subject.lower():
        return "", "", ""
    if degraded:
        return _degraded_core(subject)
    places = shellread.placed(subject)
    found = _fed_to_stdin(places)
    if found:
        return found
    compiled = None
    for words, here in places:
        call = _script_call(words)
        if not call or not _is_git_script(call[0]):
            continue
        args = call[1]
        if args and _OPEN_SUB.search(args[0]):
            # サブコマンドが文字どおりの語でない（変数・置換・glob）。
            # どのサブコマンドが走るかを読めない。
            return args[0], args[0], CORE_UNREAD
        if not args or args[0] not in _CORE_SUBS:
            continue
        sub = args[0]
        paths, unknown, index = _core_targets(sub, args[1:])
        if unknown:
            kind = CORE_UNREAD if _EXPANDS.search(unknown) else CORE_HIT
            return sub, unknown, kind
        if not paths:
            continue
        if compiled is None:
            pattern = selfguard_shell.guard_shell_regex(
                root, conf.bin, conf.project_home, selfguard_shell.common_layer_files(conf)
            )
            compiled = re.compile(pattern, re.IGNORECASE)
        base = None
        if here is not None and cwd:
            base = here if os.path.isabs(here) else os.path.join(cwd, here)
        for path in paths:
            kind = _core_hit(conf, root, compiled, path, base, sub)
            if kind:
                return sub, path, CORE_INDEX if index and kind == CORE_HIT else kind
    return "", "", ""


def _degraded_core(subject: str) -> tuple[str, str, str]:
    """走査が読み切れないコマンド行。ラッパーの名前の後ろに文字どおりのサブコマンドが無いか、
    消す・戻すサブコマンドがあればヒットする（`… | xargs sh …ccnavi-git.sh`・`sh -c '… rm …'`）。"""
    for name in _DEGRADED_NAME.finditer(subject):
        sub = _DEGRADED_SUB.match(subject, name.end())
        if not sub:
            return "?", "読み切れないコマンド行", CORE_UNREAD
        if sub.group(1).lower() in _CORE_SUBS:
            return sub.group(1).lower(), "読み切れないコマンド行", CORE_UNREAD
    return "", "", ""


def _fed_to_stdin(places: list) -> tuple[str, str, str] | None:
    """標準入力から読むシェルがあり、呼び出しの語でない語にラッパーの名前があればヒットする
    （`echo '…ccnavi-git.sh rm …' | sh`）。流し込む文字列の中は、サブコマンドも対象も読めない。"""
    mention = ""
    stdin = False
    for words, _ in places:
        call = _script_call(words)
        script = call[0] if call else None
        mention = mention or next(
            (w for w in words if w is not script and _GIT_SCRIPT in w.lower()), ""
        )
        head = _strip_heads(words)
        k = shellread_words._name_index(head)
        stdin = stdin or shellread_words.reads_stdin(head[k:])
    if mention and stdin:
        return "?", mention[:80], CORE_UNREAD
    return None


def _long_option(arg: str, full: str, least: int) -> bool:
    """`--名前`（`=値` を除く）が full か、git が受ける full の省略形
    （least 字以上の前方一致）か。"""
    return len(arg) >= least and full.startswith(arg)


def _core_targets(sub: str, args: list[str]) -> tuple[list[str], str, bool]:
    """サブコマンドの引数から、消す・戻す先のパスの語を返す。

    返すのは 3 つ組（パスの語、読めない指定があればその語、索引の中身に戻す
    `checkout -- <パス>` か）。

    `restore` は `--source` が無ければ何も返さない。`checkout` は `--` より前の最初の語（行き先か
    起点）を除く。`rm` は `-n` / `--dry-run` があれば（後ろに `--no-dry-run` が無ければ）何も
    返さない。長いオプションは、git が受ける
    省略形（`--sou=` など）も同じに読む。パスに変数・置換があれば、その語を読めない指定として
    返す。オプションを変数で書く形（`restore $O HEAD~1 <パス>`）は見ない（この検査の限界。sh
    が止める）。
    """
    longs, shorts = _VALUE_LONGS[sub], _VALUE_SHORTS[sub]
    positional: list[str] = []
    before_dashes = 0
    source = False
    dry = False
    after_dashes = False
    from_file = ""
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if after_dashes or arg == "-" or not arg.startswith("-"):
            positional.append(arg)
            continue
        if arg == "--":
            after_dashes = True
            before_dashes = len(positional)
            continue
        if arg.startswith("--"):
            name, eq, _ = arg.partition("=")
            if _long_option(name, "--pathspec-from-file", len("--pathspec-fr")):
                from_file = arg
                name = "--pathspec-from-file"
            if _long_option(name, "--source", len("--so")):
                source = True
                name = "--source"
            if _long_option(name, "--dry-run", len("--d")):
                dry = True
            if _long_option(name, "--no-dry-run", len("--no-d")):
                # git は後に書いたほうを採る。`--dry-run --no-dry-run` は消す形。
                dry = False
            if name in longs and not eq:
                i += 1
            continue
        # まとめた短いオプション。値を取る字が出たら、残りの字か次の語が値。
        rest = arg[1:]
        while rest:
            ch, rest = rest[0], rest[1:]
            if sub == "restore" and ch == "s":
                source = True
            if sub == "rm" and ch == "n":
                dry = True
            if ch in shorts:
                if not rest:
                    i += 1
                break
    if not after_dashes:
        before_dashes = len(positional)
    if sub == "restore" and not source:
        return [], "", False
    if sub == "rm" and dry:
        return [], "", False
    if from_file:
        # パスをファイルから読む形。どこにヒットするかを読みで決められない。
        return [], from_file, False
    index = False
    targets = positional
    if sub == "checkout":
        # `checkout -- <パス>` は起点が無く、索引の中身に戻す。
        index = before_dashes == 0 and after_dashes
        targets = positional if index else positional[1:]
    # パスを変数・置換で組み立てた語。どこにヒットするかを読みで決められない。
    unread = next((p for p in targets if _EXPANDS.search(p)), "")
    if unread:
        return [], unread, False
    return targets, "", index


def _core_hit(
    conf: settings.Settings,
    root: str,
    compiled: re.Pattern,
    path: str,
    base: str | None,
    sub: str = "rm",
) -> str:
    """パスの語 1 つが自己防衛の対象にヒットするなら、理由の種類。

    ヒットするかを決められない語もヒットする側に倒す。
    """
    word = path.replace("\\", "/")
    if word.startswith(":"):
        return CORE_HIT
    glob = _GLOB.search(word)
    if glob:
        # グロブの前の固定の部分（最後の `/` まで）のディレクトリとして見る。
        head = word[: glob.start()]
        word = head[: head.rfind("/") + 1] if "/" in head else ""
        if not word.strip("/."):
            return CORE_HIT
    if base is None and not os.path.isabs(word):
        # 居場所が読めない（`cd "$D"`・`pushd`）。相対パスはどこを指すかを決められない。
        return CORE_UNREAD
    full = os.path.normpath(os.path.join(base, word)) if base is not None else None
    if _in_store(conf, word, full):
        return CORE_STORE
    if _in_review(conf, word, full, sub):
        return CORE_REVIEW
    if sub == "rm" and not glob and selfguard_shell.handoff_delete_word(root, base, word):
        # 下書きの置き場の中の削除は守る場所の外。承認済み・レビュー待ちの置き場を先に見る。
        # グロブは外さない（シェルの側と同じ。`.?` が `..` に当たりうる）。
        return ""
    if compiled.search("rm " + word):
        return CORE_HIT
    if full is None:
        return ""
    if compiled.search("rm " + full.replace("\\", "/")):
        return CORE_HIT
    return CORE_HIT if _holds_core(conf, root, full) else ""


def _in_store(conf: settings.Settings, word: str, full: str | None) -> bool:
    """承認済みチケットの置き場（とその下）を指すか。置き場の親は守る場所として扱う。"""
    store = (conf.approved or settings.DEFAULT_APPROVED).replace("\\", "/").strip("/").lower()
    if not store:
        return False
    for form in (os.path.normpath(word) if not os.path.isabs(word) else word, full):
        if form is None:
            continue
        text = "/" + form.replace("\\", "/").lower().strip("/") + "/"
        if text.startswith("/../") or "/" + store + "/" not in text:
            continue
        return True
    return False


def _in_review(conf: settings.Settings, word: str, full: str | None, sub: str = "rm") -> bool:
    """レビュー待ちの置き場（とその下）を指すか、置き場の親か。

    置き場はツリーごとにあり、どのツリーのものかは問わない（承認済みチケットの置き場と同じ）。
    親（`wip/`・`wip/proposals/`）を丸ごと消すと置き場ごと消える。空なら消えるものが無いので通す。
    チケットを閉じた後の案内（`ccnavi-git.sh rm -r wip`。ops._close_parent）がこの形で、そのとき
    レビュー待ちの子は残っていない（残っていれば親を閉じられない）。

    空の親を通すのは `rm` だけ。`restore --source <ref>`・`checkout <ref> --` で親を戻すと、
    いまは空でも、過去のコミットにあったレビュー待ちの提案が置き場に戻りうるので、中身に
    依らず適用する。
    """
    tickets = (conf.tickets or settings.DEFAULT_TICKETS).replace("\\", "/").strip("/")
    review = f"{tickets}/{ticket_model.REVIEW}".lower()
    parts = review.split("/")
    parents = ["/".join(parts[:n]) for n in range(1, len(parts))]
    for form in (os.path.normpath(word) if not os.path.isabs(word) else word, full):
        if form is None:
            continue
        text = "/" + form.replace("\\", "/").lower().strip("/") + "/"
        if text.startswith("/../"):
            continue
        if "/" + review + "/" in text:
            return True
        place = full if full is not None else (word if os.path.isabs(word) else None)
        for parent in parents:
            if text.endswith("/" + parent + "/"):
                if place is None or sub != "rm":
                    return True
                return _has_entries(os.path.join(place, *review[len(parent) + 1 :].split("/")))
    return False


def _has_entries(path: str) -> bool:
    """ディレクトリに何かがあるか。無いディレクトリは空と見る。読めなければヒットする側に倒す。"""
    if not os.path.lexists(path):
        return False
    if not os.path.isdir(path):
        return True
    try:
        return bool(os.listdir(path))
    except OSError:
        return True


def _holds_core(conf: settings.Settings, root: str, full: str) -> bool:
    """full が守る場所を中に含むディレクトリか。"""
    anchors = [
        root,
        conf.bin,
        conf.projects,
        conf.log,
        conf.state,
        *selfguard_shell.common_layer_files(conf),
    ]
    forms = {pathcase.fold(os.path.abspath(full)), pathcase.fold(os.path.realpath(full))}
    for anchor in anchors:
        if not anchor:
            continue
        for a in {os.path.abspath(anchor), os.path.realpath(anchor)}:
            a = pathcase.fold(a)
            for f in forms:
                f = f.rstrip(os.sep) or os.sep
                if a == f or a.startswith(f.rstrip(os.sep) + os.sep):
                    return True
    if not os.path.isdir(full):
        return False
    try:
        children = [full, *(os.path.join(full, n) for n in os.listdir(full))]
    except OSError:
        return True
    return any(
        os.path.isdir(child) and any(os.path.lexists(os.path.join(child, m)) for m in _MARKERS)
        for child in children
    )


def core_path_message(root: str, sub: str, word: str, kind: str = CORE_HIT) -> str:
    """自己防衛の対象をラッパーで消す・戻す形を止めた文。

    処理フロー:
    1. ラッパーの呼び出し方を `settings.script_command` で求める。
    2. `kind` で文面を分ける。承認済みの置き場（`CORE_STORE`）・レビュー待ちの置き場
       （`CORE_REVIEW`）・索引（`CORE_INDEX`）・読み切れない形（`CORE_UNREAD`）。
    3. どれにもヒットしなければ、ccnavi 自身を守る場所（既定の `CORE_HIT`）の文を返す。
    """
    if kind == CORE_STORE:
        return texts.say(root, "DENY_WRAPPER_CORE_PATH__STORE", {"sub": sub, "word": word})
    if kind == CORE_REVIEW:
        return texts.say(root, "DENY_WRAPPER_CORE_PATH__REVIEW", {"sub": sub, "word": word})
    if kind == CORE_INDEX:
        return texts.say(root, "DENY_WRAPPER_CORE_PATH__INDEX", {"sub": sub, "word": word})
    if kind == CORE_UNREAD:
        call = f"{_GIT_SCRIPT} の呼び出し" if sub == "?" else f"{_GIT_SCRIPT} {sub}"
        return texts.say(root, "DENY_WRAPPER_CORE_PATH__UNREAD", {"call": call, "word": word})
    return texts.say(root, "DENY_WRAPPER_CORE_PATH__HIT", {"sub": sub, "word": word})


def worktree_name(root: str, place: str) -> str:
    """place がワークスペースの `.claude/worktrees/<名前>` の中なら、その名前。

    git に登録されているかは問わない（sh の検査と同じ広さ）。symlink は実際のパスに直して
    比べる。論理のパスと実際のパスを混ぜると、`/tmp` → `/private/tmp` のような場所で外れる。
    """
    rest = tree.parts_under(os.path.join(root, tree.WORKTREES_DIR), place)
    if not rest:
        return ""
    # 書いた綴りではなく、在るディレクトリの綴りで返す（呼び手がパスを組み直して開く）。
    return os.path.basename(tree.worktree_path(root, rest[0]))


ROLE_CHILD = "child"
ROLE_PARENT = "parent"


def ticket_role(conf: settings.Settings, root: str, name: str) -> tuple[str, str]:
    """その名前のワークツリーの役（`ROLE_CHILD` / `ROLE_PARENT`）と、子なら親の識別子。

    見るのはそのワークツリー自身のツリーにある同じ名前のチケット（`doing/`・`done/`・
    `review/`）だけ。ほかのツリーに置いたファイルでは役を決めない（子のワークツリーに
    `parent:` の行を持つ同名の偽のチケットを 1 つ置くだけで、親のワークツリーの禁止が
    丸ごと外れる）。
    見つかったものすべてに `parent:` があれば子。`parent:` の無いものが 1 つでもあるか、読めない
    ファイルがあれば親（止める側）。チケットが無ければ役は空（直接作業のワークツリーなど）。
    """
    top = os.path.join(root, tree.WORKTREES_DIR, name)
    approved = settings.approved_dir(conf, top)
    proposals = os.path.join(top, (conf.tickets or "").replace("/", os.sep))
    parents: list[str] = []
    found = False
    for path in (
        os.path.join(approved, ticket_model.DOING, name + ".md"),
        os.path.join(approved, ticket_model.DONE, name + ".md"),
        os.path.join(proposals, ticket_model.REVIEW, name + ".md"),
    ):
        if not os.path.lexists(path):
            continue
        found = True
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return ROLE_PARENT, ""
        match = re.search(r"^parent:[ \t]*(.*?)[ \t]*$", text, re.MULTILINE)
        parent = match.group(1).strip("'\"") if match else ""
        if not parent:
            return ROLE_PARENT, ""
        parents.append(parent)
    if not found:
        return "", ""
    if len(set(parents)) != 1:
        return ROLE_PARENT, ""
    return ROLE_CHILD, parents[0]


def child_parent(conf: settings.Settings, root: str, name: str) -> tuple[str, str]:
    """その名前の承認済みチケットが子なら（親, 見つけたファイル）。子でなければ空。

    探すのは、ワークスペースルート・プロジェクトの置き場の下・`.claude/worktrees/` の下の
    ディレクトリ全部の `doing/`・`done/` と `review/`。git のリポジトリかどうかは問わない。
    読めないファイルは「子かもしれない」として止める側にする（親は `?`）。
    """
    for top in _trees(conf, root):
        approved = settings.approved_dir(conf, top)
        proposals = os.path.join(top, (conf.tickets or "").replace("/", os.sep))
        for path in (
            os.path.join(approved, ticket_model.DOING, name + ".md"),
            os.path.join(approved, ticket_model.DONE, name + ".md"),
            os.path.join(proposals, ticket_model.REVIEW, name + ".md"),
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
    """コマンド 1 本が保護済みの sh を呼ぶなら（sh のパス, sh に渡る引数）。

    `sh <sh>`・`bash <sh>`・`<sh>` の直接の実行・`. <sh>`、と、それを `env` `sudo` などの
    実行役のコマンド越しに呼ぶ形。`sh -c '…'` は読み切れない形として read() が縮退させる。
    """
    found = _script_call_at(words)
    return (found[0], found[1]) if found else None


# 実行役のコマンドのうち、後ろのコマンドを別のディレクトリで実行させる選択肢
# （`env -C <dir>`・`sudo -D <dir>`。長い形はどちらも `--chdir`）。
_CHDIR_SHORT = {"env": "C", "sudo": "D"}


# 居場所の表記のうち、シェルが実行の時点で値を決めるもの（変数・置換・glob・`~`）。
_UNREAD_PLACE = re.compile(r"[$`*?\[~]")


def _call_place(cwd: str, here: str | None, chdirs: list[str]) -> str | None:
    """sh が実際に走る場所。`cd` を追った先に、実行役の `-C` / `-D` / `--chdir` の行き先を重ねる。

    どれかが読めなければ None（どこで走るかが決まらない。呼び手は止める側に倒す）。
    """
    if here is None:
        return None
    place = here if os.path.isabs(here) else os.path.join(cwd, here)
    for target in chdirs:
        if not target or _UNREAD_PLACE.search(target):
            return None
        place = os.path.join(place, target)
    return place


def _runner_chdirs(name: str, args: list[str]) -> list[str]:
    """実行役が後ろのコマンドを動かすディレクトリ（書いた順）。無ければ空。"""
    short = _CHDIR_SHORT.get(name)
    if not short:
        return []
    values, _ = shellread_words._RUNNERS[name]
    found: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--" or not arg.startswith("-") or arg == "-":
            break
        if arg.startswith("--"):
            option, eq, value = arg.partition("=")
            if option == "--chdir":
                if eq:
                    found.append(value)
                elif i + 1 < len(args):
                    found.append(args[i + 1])
            i += shellread_words._option_width(arg, args[i + 1 :], values)
            continue
        letters = arg[1:]
        for k, letter in enumerate(letters):
            if letter == short:
                rest = letters[k + 1 :]
                if rest:
                    found.append(rest)
                elif i + 1 < len(args):
                    found.append(args[i + 1])
                break
            if "-" + letter in values:
                break
        i += shellread_words._option_width(arg, args[i + 1 :], values)
    return found


def _script_call_at(words: list[str]) -> tuple[str, list[str], list[str]] | None:
    """`_script_call` と同じ読みに、実行役が移す先のディレクトリ（書いた順）を添える。"""
    words = _strip_heads(words)
    chdirs: list[str] = []
    depth = 0
    while words and depth <= shellread_words.UNWRAP_DEPTH:
        k = shellread_words._name_index(words)
        if k >= len(words):
            return None
        name = shellread_words._base(words[k])
        args = words[k + 1 :]
        if name in shellread_words._RUNNERS:
            inner = shellread_words._runner_command(name, args)
            if not inner:
                return None
            chdirs.extend(_runner_chdirs(name, args))
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
                return args[i], args[i + 1 :], chdirs
            return None
        if _is_script(words[k]):
            return words[k], args, chdirs
        return None
    return None


def _is_git_script(word: str) -> bool:
    return os.path.basename(word.replace("\\", "/")).lower() == _GIT_SCRIPT


def _is_script(word: str) -> bool:
    return bool(_SCRIPT.fullmatch(os.path.basename(word.replace("\\", "/"))))


def _set_names(words: list[str]) -> list[str]:
    """コマンド 1 本が置く・外す変数の名前。実行役のコマンドの層も見る。"""
    names: list[str] = []
    words = _strip_heads(words)
    depth = 0
    while words and depth <= shellread_words.UNWRAP_DEPTH:
        k = shellread_words._name_index(words)
        names.extend(_assigned(w) for w in words[:k] if _assigned(w))
        if k >= len(words):
            break
        name = shellread_words._base(words[k])
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
        if name not in shellread_words._RUNNERS:
            break
        inner = shellread_words._runner_command(name, args)
        if not inner:
            break
        words = inner[0]
        depth += 1
    return names


def _env_names(args: list[str]) -> list[str]:
    """`env` が置く・外す変数。`-u X`・`--unset=X`・`X=…`。環境を空にする形は ENV_CLEARED。"""
    names: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-u", "--unset", "-C", "--chdir") and i + 1 < len(args):
            if arg in ("-u", "--unset"):
                names.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("--unset="):
            names.append(arg.partition("=")[2])
        elif arg.startswith("-u") and len(arg) > 2:
            names.append(arg[2:])
        elif arg in _ENV_CLEARS or (arg[:1] == "-" and arg[1:2] != "-" and "i" in arg[1:]):
            names.append(ENV_CLEARED)
        elif _assigned(arg):
            names.append(_assigned(arg))
        elif not arg.startswith("-"):
            break
        i += 1
    return names


def _assigned(word: str) -> str:
    match = _ASSIGNMENT.match(word)
    return match.group(1) if match else ""


def _strip_heads(words: list[str]) -> list[str]:
    """コマンドの位置より前に来る語を除く。予約語と、関数の定義の頭。

    関数の定義（`f() { …; }`・`function f { …; }`）の本体は、定義の行では走らないが、同じ
    コマンド行の後ろで呼べば走る。呼ばれるかは読みで決まらないので、本体は走るものとして数える。
    頭を除かないと、`f` をコマンドの名前と読み違え、本体の sh の呼び出しと代入を見落とす。
    """
    i = 0
    while i < len(words):
        word = words[i]
        if word == "function":
            i += 2
            if i < len(words) and words[i] == "()":
                i += 1
            continue
        if word in shellread_words._RESERVED:
            if word in shellread_words._TAKES_A_WORD:
                return []
            i += 1
            continue
        if i + 1 < len(words) and words[i + 1] == "()" and _NAME.fullmatch(word):
            i += 2
            continue
        if word == "(":
            i += 1
            continue
        break
    return words[i:]


def env_message(root: str, script: str, names: list[str]) -> str:
    """環境変数の代入で止めた文。"""
    return texts.say(
        root,
        "DENY_SCRIPT_ENV_OVERRIDE",
        {
            "script": script,
            "names": ", ".join(names),
            "pass_through": ", ".join(sorted(PASS_THROUGH)),
        },
    )


def child_push_message(root: str, name: str, parent: str) -> str:
    """子のワークツリーからの push を止めた文。sh の文面と揃える。"""
    if name == "?":
        return texts.say(root, "DENY_CHILD_PUSH__UNREAD", {})
    return texts.say(root, "DENY_CHILD_PUSH__CHILD", {"name": name, "parent": parent})

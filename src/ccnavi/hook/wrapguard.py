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
   同じファイルを消せた。ラッパー自身を消すと、生の git の組み込みの止め（ラッパーがあるときだけ
   足す）も外れる。sh も同じ場所の検査を 2 重目として持つ。

どれも組み込み。ルールを空にされても消えないように、保護の根拠を守られる対象の外に置く。

止める広さは sh の検査に合わせる。登録されていない `.claude/worktrees/<名前>` も、閉じた子
（`done/`）も、レビュー待ちの子（`review/`）も、YAML として読めない承認済みチケットも、
`parent:` の行があれば子と見る。Python の読み方（`approval.scan`）はどれも対象から外すので、
そちらにそろえると止まっていたものが通るようになる。
"""

from __future__ import annotations

import os
import re

from ..infra import settings, shellread, shellread_words, tree
from ..policy import selfguard_shell
from ..tickets import ticket_model

CODE_ENV = "DENY_SCRIPT_ENV_OVERRIDE"
CODE_CHILD_PUSH = "DENY_CHILD_PUSH"
CODE_CORE_PATH = "DENY_WRAPPER_CORE_PATH"
ENV_RULE_ID = "builtin-script-env"
CHILD_PUSH_RULE_ID = "builtin-child-push"
CORE_PATH_RULE_ID = "builtin-guard-wrapper-paths"

# 保護済みの sh の名前。置き場は `.ccnavi/scripts/` に固定だが、呼び出しの綴りは
# 絶対パス・相対パス・ワークツリーの中の写しとまちまちなので、パスではなく名前で見る。
_SCRIPT = re.compile(r"ccnavi-[A-Za-z0-9-]+\.sh")
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
    if name in PASS_THROUGH:
        return False
    return name.startswith("CCNAVI_") or name in _ALSO_BLOCKED


def check_env(subject: str, degraded: str) -> tuple[str, list[str]]:
    """保護済みの sh を呼ぶコマンド行に、止める変数の代入があれば（sh のパス, 変数名）。

    コマンド行のどこで置いても数える。前置きの代入（`X=… sh …`）、`env X=…`、`export X=…`、
    すでに書き出してある変数への素の代入（`X=…; sh …`。settings.json の env が書き出した変数は
    代入だけで sh に届く）、`unset X`・`env -u X`。読み切れない形は、allow が当たらずに
    確認になるので、ここでは見ない。
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

    居場所は payload の cwd から `cd` を追った先。追えなくなった先で push を
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


# ラッパーの副命令のうち、行き先のファイルを消す・別のコミットの中身に戻すもの。
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
# どこに当たるかを読みで決められない pathspec。グロブは `/` もまたぐ（git の pathspec）ので、
# 文字の前の固定の部分（ディレクトリ）より下のどこにでも当たりうる。
_GLOB = re.compile(r"[*?\[]")
# ディレクトリがこの名前を直に持つなら、守る場所を中に含む
# （ツリーの頂上・ccnavi ディレクトリの親）。
_MARKERS = (settings.DEFAULT_PROJECT_HOME, ".claude", ".git")


def check_core_paths(
    conf: settings.Settings, root: str, cwd: str, subject: str, degraded: str
) -> tuple[str, str]:
    """`ccnavi-git.sh` で自己防衛の対象を消す・別のコミットの中身に戻す形なら
    （副命令, 当たった語）。

    当たるとみなすのは次のどれか。

    - シェルから書き込む形の組み込み（`builtin-guard-setting-files`）と同じ場所の式に、
      書いたとおりのパスか、居場所から解いた絶対パスが当たる（`.ccnavi/`・
      `.claude/settings*.json`・`.claude/hooks/`・`ccnavi-git.sh`・記録と state・
      実行ファイル・動かした共通レイヤー）
    - そのパスが守る場所を中に含むディレクトリ（ツリーの頂上・`.ccnavi` や `.claude` を持つ
      ディレクトリとその 1 つ上・実行ファイルや共通レイヤーの上・プロジェクトの置き場の上）
    - 当たる先を読みで決められない指定（`:` で始まる pathspec、固定の部分の無いグロブ、
      `--pathspec-from-file`、居場所の読めない `..`）

    通すのは、索引の中身に戻す `restore`（`--staged` も、衝突を解く `--ours` / `--theirs` も）。
    消える・過去の中身になるのでないものは、ルールとラッパーの許可リストに任せる。
    読み切れない形は、allow が当たらずに確認になるので、ここでは見ない。
    """
    if degraded:
        return "", ""
    pattern = ""
    for words, here in shellread.placed(subject):
        call = _script_call(words)
        if not call or os.path.basename(call[0].replace("\\", "/")) != _GIT_SCRIPT:
            continue
        args = call[1]
        if not args or args[0] not in _CORE_SUBS:
            continue
        sub = args[0]
        paths, unknown = _core_targets(sub, args[1:])
        if unknown:
            return sub, unknown
        if not paths:
            continue
        if not pattern:
            pattern = selfguard_shell.guard_shell_regex(
                root, conf.bin, conf.project_home, selfguard_shell.common_layer_files(conf)
            )
            compiled = re.compile(pattern, re.IGNORECASE)
        base = None
        if here is not None and cwd:
            base = here if os.path.isabs(here) else os.path.join(cwd, here)
        for path in paths:
            if _core_hit(conf, root, compiled, path, base):
                return sub, path
    return "", ""


def _core_targets(sub: str, args: list[str]) -> tuple[list[str], str]:
    """副命令の引数から、消す・戻す先のパスの語（, 読めない指定があればその語）。

    `restore` は `--source` が無ければ何も返さない。`checkout` は最初の語（行き先か起点）を除く。
    """
    longs, shorts = _VALUE_LONGS[sub], _VALUE_SHORTS[sub]
    positional: list[str] = []
    source = False
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
            continue
        if arg.startswith("--"):
            name, eq, _ = arg.partition("=")
            if name == "--pathspec-from-file":
                from_file = arg
            if name == "--source":
                source = True
            if name in longs and not eq:
                i += 1
            continue
        # まとめた短いオプション。値を取る字が出たら、残りの字か次の語が値。
        rest = arg[1:]
        while rest:
            ch, rest = rest[0], rest[1:]
            if sub == "restore" and ch == "s":
                source = True
            if ch in shorts:
                if not rest:
                    i += 1
                break
    if sub == "restore":
        if not source:
            return [], ""
        return positional, from_file
    if from_file:
        # パスをファイルから読む形。どこに当たるかを読みで決められない。
        return [], from_file
    if sub == "checkout":
        return positional[1:] if not after_dashes else positional, ""
    return positional, ""


def _core_hit(
    conf: settings.Settings, root: str, compiled: re.Pattern, path: str, base: str | None
) -> bool:
    """パスの語 1 つが、自己防衛の対象に当たるか（当たるかを決められない語も当たる側）。"""
    word = path.replace("\\", "/")
    if word.startswith(":"):
        return True
    glob = _GLOB.search(word)
    if glob:
        # グロブの前の固定の部分（最後の `/` まで）のディレクトリとして見る。
        head = word[: glob.start()]
        word = head[: head.rfind("/") + 1] if "/" in head else ""
        if not word.strip("/."):
            return True
    if compiled.search("rm " + word):
        return True
    if base is None:
        # 居場所が読めない。ツリーの頂上かもしれない語と、上へ出る語は決められない。
        return os.path.isabs(word) is False and (
            word.strip("/") in ("", ".") or ".." in word.split("/")
        )
    full = os.path.normpath(os.path.join(base, word))
    if compiled.search("rm " + full.replace("\\", "/")):
        return True
    return _holds_core(conf, root, full)


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
    forms = {os.path.normcase(os.path.abspath(full)), os.path.normcase(os.path.realpath(full))}
    for anchor in anchors:
        if not anchor:
            continue
        for a in {os.path.abspath(anchor), os.path.realpath(anchor)}:
            a = os.path.normcase(a).lower()
            for f in forms:
                f = f.lower().rstrip(os.sep) or os.sep
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


def core_path_message(root: str, sub: str, word: str) -> str:
    """自己防衛の対象をラッパーで消す・戻す形を止めた文。"""
    git_sh = settings.script_command(root, _GIT_SCRIPT)
    return (
        f"{_GIT_SCRIPT} {sub} が、ccnavi 自身を守る場所（{word}）を消すか、別のコミットの中身に"
        "戻す形になっています。ここには hook の登録・ルールとフェーズの設定・保護済みのスクリプト・"
        "実行ファイル・判定の記録があり、消したり古い中身に戻したりすると ccnavi の止めが外れます。"
        "シェルの rm や Write / Edit でも拒否される場所です。変更が要るなら、何をなぜ変えたいのかを"
        "伝えてユーザに依頼してください。"
        f"衝突を解くときの '{git_sh} restore --ours -- <パス>'（--theirs も）と、索引だけを戻す "
        f"'{git_sh} restore --staged <パス>' は通ります。"
        "グロブ（* ? [）・: で始まる指定・--pathspec-from-file は、守る場所に当たるかを"
        "決められないので、"
        "消す・戻すファイルを 1 つずつ名指ししてください。"
    )


def worktree_name(root: str, place: str) -> str:
    """place がワークスペースの `.claude/worktrees/<名前>` の中なら、その名前。

    git に登録されているかは問わない（sh の検査と同じ広さ）。symlink は実際のパスに直して
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
    words = _strip_heads(words)
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


def env_message(script: str, names: list[str]) -> str:
    """環境変数の代入で止めた文。"""
    shown = ", ".join(names)
    return (
        f"保護済みの sh（{script}）を、sh の検査の材料を変える環境変数（{shown}）と"
        "同じコマンド行で呼んでいます。この変数は sh がワークスペースルート・承認済みチケットの"
        "置き場・実行ファイルを決めるのに使うので、書き換えると sh の検査が外れます。"
        "コマンド行のどこに書いても数えます（ループや関数の中では、後ろに書いた代入も次の呼び出しに"
        "届くため）。変数を置かずに、sh の呼び出しを別のコマンドに分けて打ってください。"
        "置き場が本当に違うなら、ユーザに "
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

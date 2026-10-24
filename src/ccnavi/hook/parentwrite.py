"""サブエージェントが Bash から親のワークツリーへ書き込む形を、実行前に止める。

サブエージェントは親のワークツリーを書き換えない（合流・コミット・push はメインが finish で行う）。
Write / Edit は書き込み先のパスで、`ccnavi-git.sh` は居場所で止めている。ここはシェルからの
書き込みを受け持つ。

読む書き込み先は次のとおり。どれも `cd` と実行役の行き先（`env -C`・`sudo -D`）を追った
居場所から解いて、親のワークツリー（`.claude/worktrees/<名前>` で、そのツリーのチケットが
親のもの）の中なら止める。

- リダイレクト（`>`・`>>`・`>|`・`&>`・`<>`）の行き先。fd の複製（`2>&1`・`>&-`）は数えない
- `tee`・`touch`・`mkdir`・`rm`・`rmdir`・`truncate`・`mv`・`shred`・`unlink` の引数
  （mv は元も消える）
- `cp`・`install`・`ln` の行き先（`-t` の値か最後の引数）
- `sed -i`・`perl -i` の対象のファイル、`dd` の `of=`

インタプリタに渡した任意のコード（`python -c`・`node -e`・`perl -e` など）は中を読み切れない。
コードの文字列に親のワークツリーのパス（絶対パス、`.claude/worktrees/<親>`、`../<親>`）が含まれ、
かつ書き込みを疑う語（`open(`・`write`・`>` など）があるか、居場所が親のワークツリーで書き込みを
疑う語があれば、止める側に倒す。読むだけのコード（パスを含んでも書き込みを疑う語が無い）は通す。

読めないもの（限界）。変数・置換で組み立てた書き込み先（`> "$P/x"`）、スクリプトのファイルを
実行する形（`python3 tool.py`）、書き込みを疑う語を含まないコード、ビルドの道具が居場所に書く
生成物（`npm install` など）は見ない。走査が読み切れないコマンド行（`sh -c '…'` の入れ子など）は、
文字列に親のワークツリーのパスと書き込みを疑う語が並べば止める。

下書きの置き場（`wip/proposals/`・`wip/handoff/`・`wip/eli5/`）と承認済みの置き場は、Write と同じく
対象にしない（置き場ごとの保護が別に掛かっている）。
"""

from __future__ import annotations

import os
import re

from ..infra import pathcase, settings, shellread, shellread_words, tree
from ..tickets import ticket_places
from . import wrapguard

KIND_WRITE = "write"
KIND_CODE = "code"

# 引数がすべて書き込み先（作る・消す・書き換える）になるコマンド。
_ALL_ARGS = frozenset({"tee", "touch", "mkdir", "rm", "rmdir", "truncate", "mv", "shred", "unlink"})
# 行き先（`-t` の値か最後の引数）に書くコマンド。
_COPIES = frozenset({"cp", "install", "ln"})
# 値を次の語に取る選択肢（書き込み先と読み違えないため）。
_VALUE_OPTIONS = {
    "tee": frozenset(),
    "touch": frozenset({"-d", "-r", "-t", "--date", "--reference"}),
    "mkdir": frozenset({"-m", "--mode", "-Z"}),
    "truncate": frozenset({"-s", "-r", "--size", "--reference"}),
    "mv": frozenset({"-S", "--suffix", "-t", "--target-directory"}),
    "cp": frozenset({"-S", "--suffix", "-t", "--target-directory"}),
    "install": frozenset({"-S", "--suffix", "-t", "--target-directory", "-m", "-o", "-g"}),
    "ln": frozenset({"-S", "--suffix", "-t", "--target-directory"}),
    "shred": frozenset({"-n", "-s", "--iterations", "--size"}),
    "sed": frozenset({"-e", "-f", "--expression", "--file", "-l", "--line-length"}),
    "perl": frozenset({"-e", "-E", "-M", "-I"}),
}
# 任意のコードを文字列で受け取るインタプリタと、その文字列を取る選択肢。
_INTERPRETERS = ("python", "node", "perl", "ruby", "php", "deno", "bun", "pwsh", "powershell")
_CODE_FLAGS = frozenset({"-c", "-e", "-E", "--eval", "-p", "--print", "-r", "-Command", "-command"})
# 書き込みを疑う語。読むだけのコードを止めないための線引きで、広めに取る。`open(` は書く
# モード（`'w'`・`"a"`・`'x'`・`+`）を添えたときだけ数える（読むだけの `open(p).read()` を通す）。
_WRITE_HINT = re.compile(
    r"open\s*\([^)]*['\"][rbt]*[wax][bt+]*['\"]|write|append|unlink|remove|rename|replace\s*\(|rmtree|copy|move|mkdir|"
    r"makedirs|truncate|chmod|symlink|touch|system|exec|spawn|popen|subprocess|"
    r"out-file|set-content|add-content|new-item|>",
    re.IGNORECASE,
)
# 書き込み先に読めない展開がある。
_EXPANDS = re.compile(r"[$`*?\[~{]")


def check(
    conf: settings.Settings,
    root: str,
    cwd: str,
    subject: str,
    degraded: str,
    layers: list[str] | None = None,
) -> tuple[str, str, str]:
    """親のワークツリーへの書き込みがあれば（書き込みの形, 親の名前か `?`, 種類）。無ければ空。"""
    parents = _parent_trees(conf, root)
    if not parents:
        return "", "", ""
    texts = [subject, *(layers or [])]
    for text in texts:
        places = shellread.placed(text)
        if not places:
            continue
        for words, here in places:
            found = _check_command(conf, root, cwd, parents, words, here)
            if found[0]:
                return found
    if degraded or any(not shellread.placed(t) for t in texts if t.strip()):
        # 走査が読み切れない。文字列に親のワークツリーのパスと書き込みを疑う語が並べば止める。
        for text in texts:
            name = _mentioned_parent(root, parents, text)
            if name and _WRITE_HINT.search(text):
                return _short(text), name, KIND_CODE
    return "", "", ""


def _parent_trees(conf: settings.Settings, root: str) -> list[str]:
    """親のワークツリーの名前（`.claude/worktrees/` の下で、自身のチケットが親のもの）。"""
    base = os.path.join(root, tree.WORKTREES_DIR)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    return [
        n
        for n in names
        if os.path.isdir(os.path.join(base, n))
        and wrapguard.ticket_role(conf, root, n)[0] == wrapguard.ROLE_PARENT
    ]


def _check_command(
    conf: settings.Settings,
    root: str,
    cwd: str,
    parents: list[str],
    words: list[str],
    here: str | None,
) -> tuple[str, str, str]:
    """1 つのコマンドの語から、親のワークツリーへの書き込みを探す。見つからなければ空の 3 つ組。

    処理フロー:
    1. 先頭の飾り語を外し（`wrapguard._strip_heads`）、リダイレクトの行き先を先に拾う。
    2. 実行役（`env`・`sudo` など）を、深さの上限まで 1 枚ずつ外す
       （`shellread_words._runner_command`）。
    3. 実行役を外したコマンドの名前で、書き込みの形を振り分ける。インタプリタのコードは
       `_check_code` で読み、`perl -i`・`sed -i`・コピー・`dd of=` は行き先を足す。
    4. 集めた行き先を、1 つずつ `_check_target` で親のワークツリーに当てる。
    """
    # 先頭の飾り語を外してから読む。
    words = wrapguard._strip_heads(words)
    chdirs: list[str] = []
    targets: list[str] = []
    depth = 0
    # リダイレクトは外側のコマンドの語にある。実行役を外す前に拾う。
    targets.extend(_redirect_targets(words))
    while words and depth <= shellread_words.UNWRAP_DEPTH:
        k = shellread_words._name_index(words)
        if k >= len(words):
            break
        name = shellread_words._base(words[k]).lower()
        args = _drop_redirects(words[k + 1 :])
        if name in shellread_words._RUNNERS:
            inner = shellread_words._runner_command(name, args)
            if not inner:
                break
            chdirs.extend(wrapguard._runner_chdirs(name, args))
            words = inner[0]
            depth += 1
            continue
        base = wrapguard._call_place(cwd, here, chdirs) if cwd else None
        if name.startswith(_INTERPRETERS):
            code = _code_of(args)
            if code is not None:
                # 引用の中の `>` `<` は目印の文字に置き換わっている。コードとして読むので戻す。
                for stand_in, back in shellread_words.QUOTED_BACK.items():
                    code = code.replace(stand_in, back)
            if code is not None:
                found = _check_code(conf, root, parents, base, code)
                if found[0]:
                    return found
            if name.startswith("perl") and _in_place(name, args):
                files = _positionals(name, args)
                targets.extend(files[1:] if code is None else files)
        elif name in _ALL_ARGS:
            targets.extend(_positionals(name, args))
        elif name in _COPIES:
            targets.extend(_copy_destination(name, args))
        elif name == "sed" and _in_place(name, args):
            positional = _positionals(name, args)
            scripted = any(
                a in ("-e", "-f", "--expression", "--file")
                or a.startswith(("--expression=", "--file="))
                for a in args
            )
            targets.extend(positional if scripted else positional[1:])
        elif name == "dd":
            targets.extend(a[3:] for a in args if a.startswith("of="))
        break
    base = wrapguard._call_place(cwd, here, chdirs) if cwd else None
    for target in targets:
        found = _check_target(conf, root, parents, base, target)
        if found[1]:
            return found
    return "", "", ""


def _redirect_targets(words: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(words):
        width = shellread_words._redirect_width(words, i)
        if not width:
            i += 1
            continue
        op = words[i + width - 2]
        target = words[i + width - 1]
        if ">" in op and not (op.endswith("&") and (target.isdigit() or target == "-")):
            out.append(target)
        i += width
    return out


def _drop_redirects(args: list[str]) -> list[str]:
    kept: list[str] = []
    i = 0
    while i < len(args):
        width = shellread_words._redirect_width(args, i)
        if width:
            i += width
            continue
        kept.append(args[i])
        i += 1
    return kept


def _positionals(name: str, args: list[str]) -> list[str]:
    values = _VALUE_OPTIONS.get(name, frozenset())
    out: list[str] = []
    i = 0
    after = False
    while i < len(args):
        arg = args[i]
        i += 1
        if after or arg == "-" or not arg.startswith("-"):
            out.append(arg)
            continue
        if arg == "--":
            after = True
            continue
        if arg in values:
            i += 1
    return out


def _copy_destination(name: str, args: list[str]) -> list[str]:
    for i, arg in enumerate(args):
        if arg in ("-t", "--target-directory") and i + 1 < len(args):
            return [args[i + 1]]
        if arg.startswith("--target-directory="):
            return [arg.partition("=")[2]]
        if arg.startswith("-t") and len(arg) > 2 and not arg.startswith("--"):
            return [arg[2:]]
    positional = _positionals(name, args)
    return positional[-1:] if len(positional) > 1 else []


def _in_place(name: str, args: list[str]) -> bool:
    """`sed -i` / `perl -i` の形か（まとめた短い選択肢 `-pi`・`-Ei` も）。"""
    values = {v[1:] for v in _VALUE_OPTIONS.get(name, frozenset()) if len(v) == 2}
    for arg in args:
        if arg == "--":
            return False
        if arg.startswith("--in-place"):
            return True
        if not arg.startswith("-") or arg.startswith("--"):
            continue
        for letter in arg[1:]:
            if letter == "i":
                return True
            if letter in values:
                break
    return False


def _code_of(args: list[str]) -> str | None:
    """インタプリタに文字列で渡したコード。無ければ None。"""
    for i, arg in enumerate(args):
        if arg in _CODE_FLAGS and i + 1 < len(args):
            return args[i + 1]
        # まとめた短い選択肢（`-pe`・`-ic`）。最後の字がコードを取る選択肢なら次の語。
        bundled = arg.startswith("-") and not arg.startswith("--") and len(arg) > 2
        if bundled and "-" + arg[-1] in _CODE_FLAGS and i + 1 < len(args):
            return args[i + 1]
    return None


def _check_code(
    conf: settings.Settings,
    root: str,
    parents: list[str],
    base: str | None,
    code: str,
) -> tuple[str, str, str]:
    if not _WRITE_HINT.search(code):
        return "", "", ""
    name = _mentioned_parent(root, parents, code)
    if name:
        return _short(code), name, KIND_CODE
    if base is not None:
        place = wrapguard.worktree_name(root, base)
        if pathcase.fold(place) in {pathcase.fold(p) for p in parents} and not _unscoped(
            conf, root, place, base
        ):
            return _short(code), place, KIND_CODE
    return "", "", ""


def _mentioned_parent(root: str, parents: list[str], text: str) -> str:
    """文字列に親のワークツリーのパスがあれば、その親の名前。"""
    folded = text.replace("\\", "/")
    base = os.path.join(root, tree.WORKTREES_DIR)
    for name in parents:
        forms = {
            os.path.join(base, name).replace("\\", "/"),
            os.path.realpath(os.path.join(base, name)).replace("\\", "/"),
            f"worktrees/{name}",
            f"../{name}",
        }
        for form in forms:
            for match in re.finditer(re.escape(form), folded, re.IGNORECASE):
                tail = folded[match.end() : match.end() + 1]
                if not tail or not (tail.isalnum() or tail in "-_."):
                    return name
    return ""


def _check_target(
    conf: settings.Settings,
    root: str,
    parents: list[str],
    base: str | None,
    target: str,
) -> tuple[str, str, str]:
    word = target.replace("\\", "/")
    if not word:
        return "", "", ""
    expand = _EXPANDS.search(word)
    if expand:
        # 展開より前の固定の部分（最後の `/` まで）が親のワークツリーの中なら止める。
        head = word[: expand.start()]
        word = head[: head.rfind("/") + 1] if "/" in head else ""
        if not word or (not os.path.isabs(word) and base is None):
            return "", "", ""
    if not os.path.isabs(word):
        if base is None:
            return target, "?", KIND_WRITE
        word = os.path.join(base, word)
    name = wrapguard.worktree_name(root, word)
    if pathcase.fold(name) not in {pathcase.fold(p) for p in parents} or _unscoped(
        conf, root, name, word
    ):
        return "", "", ""
    return target, name, KIND_WRITE


def _unscoped(conf: settings.Settings, root: str, name: str, path: str) -> bool:
    """下書き・提案・ELI5・承認済みの置き場か（Write と同じく、置き場ごとの保護に任せる）。"""
    rest = tree.parts_under(os.path.join(root, tree.WORKTREES_DIR, name), path)
    if not rest:
        return False
    rel = "/".join(rest)
    return ticket_places.is_unscoped(rel, conf.tickets, conf.approved)


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 60 else text[:57] + "..."

"""Chrome 拡張（ADR-0093 段階 1）が Pyodide の上で呼ぶ入口。

拡張はホストの API でブランチの置き場を読み、その中身（Snapshot）をここへ渡す。ここは
ブランチごとの仮のツリーを MEMFS に組み、今の ccnavi（`--approve --preview --json`）を
そのまま動かす（ADR-0093 の 8.1）。承認待ちの一覧も、家族の見分けも、参照の閉包も、
互換の比べも Python が出し、拡張（TS）は並べるだけ（ADR-0035）。

段階 2a から、判定のコア（`ccnavi.core`）の `plan`・`withdraw`・`confirm` も呼べる
（`plan`・`withdraw`・`confirm` の操作）。どれも書くもの（Changes）を値で返すだけで、
ホストにもディスクにも書かない（fsio の控える段）。段階 3 から、拡張は `plan`（承認）と
`withdraw`（取り下げ）の答えを親のブランチへの 1 コミットにして書く（8.3・8.4）。
段階 4 から `confirm`（レビュー済み）の答えも同じく書く（8.9）。ボードの答えの `reviewable` が
候補のフェーズで、ホストのスレッドとレビューの写しは拡張が組んで `result` で渡す。

段階 3 から、仮のツリーに手元の取り込みの控え相当（`logs/state/sync/self/`。統合先の
`done/`・層・設定の写しと、閉包の家族の控え）も組む（3.3。段階 2c の「Chrome の入口で控えを
組む」）。手元と同じ判定のコードが、取り込み済みの家族として読む:

- ホストに在る家族（`P` と閉包の `P_X`）は `present`
- ホストに無く、統合先の `done/` でも閉じていない家族は `gone`（決まらない。3.3 の 3）
- 判定の入力に読めない（バイナリの）ファイルがあれば、何も判定せず「決まらない」で止める
  （6.2 の `NOT_FETCHED` と同じ考え。無いとも空とも読ませない）

書く操作（`plan`・`withdraw`）は、同梱の互換の版が統合先の `CCNAVI_COMPAT` と違えば受けない
（7.3・D31）。書く先は家族の親のブランチ `P` だけで、予約の名前・統合先の名前は受けない（8.5）。

呼び方は `handle(<要求の JSON>, root)`。`root` は仮のツリーを組む場所で、Pyodide では `/ws`、
手元の試験では一時ディレクトリ。答えは JSON の文字列。

Snapshot の形（拡張の `src/core/snapshot.ts` と対）:

    {
      "integration": {"name": "main", "source": "setting" | "default", "head": "<sha>"},
      "branches": {"<名前>": {"head": "<sha>", "files": {"<相対パス>": "<本文>"},
                              "binary": ["<相対パス>", ...]}},
      "absent": ["<ホストに無かったブランチ>", ...]
    }

統合先のブランチも `branches` に入る。読むのは置き場のサブツリーだけ（8.2）。

段階 5 から、プロジェクトのリポジトリ（手元で `projects/<名前>` に clone されるもの。3.3 の 7）
も読む。Snapshot に `project`（プロジェクト名）と `workspace`（ワークスペースのリポジトリの
統合先の中身。共通層・自身の層・`.claude/settings.json`・互換のマーカー）が付く。仮のツリーは
手元と同じ形で組む: ワークスペースルートにワークスペースの統合先、`projects/<名前>/` に
プロジェクトの統合先（`done/` と、D28 の計算の層）、家族は `projects/<名前>` のワークツリー
として `.claude/worktrees/<P>` に置く。控えは `sync/self/` と `sync/<名前>/` に分けて組む。

    "project": "<プロジェクト名>",
    "workspace": {"integration": {"name": ..., "source": ..., "head": ...},
                  "files": {...}, "binary": [...], "links": [...]}

段階 5 から「始める」（8.6）の `start` も答える。issue の番号から識別子を決め
（`ticket.issue_identifier`。3.1 の 11）、始められない理由（統合先の `done/` にある・同じ名前の
ブランチがある・開いた家族に同じ識別子がある・予約の名前・互換の版の違い）を返す。
ブランチを作るのは拡張（service worker）。
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import sys

from ccnavi import cli, configsync, core, fsio, history, lint, review, settings, syncstate, version
from ccnavi import ticket as ticket_mod

# 要求と答えの形の版。拡張の `PY_SCHEMA` と揃える。
SCHEMA = 1

# 控えの名前（ワークスペース自身）。段階 3 はワークスペースのリポジトリだけ（11 章）。
SELF_REPO = syncstate.SELF

# 参照の閉包で辿る家族の上限（ADR-0093 の 3.3 の 5）。
FAMILY_LIMIT = 16

# ワークツリーの置き場。tree.WORKTREES_DIR と同じ綴りを "/" で持つ。
WORKTREES = ".claude/worktrees"

# 統合先から読むもの（置き場の綴りに依らないもの）。3.3 の「手元の統合先」と同じ並び。
COMMON_LAYER = ".ccnavi/common"
SETTINGS_FILE = ".claude/settings.json"
COMPAT_FILE = lint.SH_COMPAT_FILE.replace(os.sep, "/")

# 置き場の綴りに効く環境変数。統合先の `.claude/settings.json` の `env` から読む（3.3 の 6）。
PLACEMENT_ENV = (settings.TICKETS_ENV, settings.APPROVED_ENV, settings.PROJECT_HOME_ENV)

# 取り込みの控え相当の置き場（仮のツリーの中。手元の既定の控えの置き場と同じ綴り）。
STATE_DIR = settings.DEFAULT_STATE.replace(os.sep, "/")

# 絶対パスの綴り。置き場がリポジトリの外を指すワークスペースは Chrome の対象外（3.1 の 12）。
_ABSOLUTE = re.compile(r"^(?:[/\\~]|[A-Za-z]:)")


class Refused(Exception):
    """要求そのものを受けない。答えの `error` に文面を入れて返す。"""


def handle(request: str, root: str = "/ws") -> str:
    """要求 1 つに答える。落ちても例外にせず `error` で返す（Worker が落ちないように）。"""
    try:
        req = json.loads(request)
        if not isinstance(req, dict) or req.get("schema") != SCHEMA:
            raise Refused(
                f"要求の形の版が違う（拡張と Python の組み立てがずれている。期待 {SCHEMA}）"
            )
        op = req.get("op")
        handler = _OPS.get(op) if isinstance(op, str) else None
        if handler is None:
            raise Refused(f"知らない操作: {op!r}")
        body = handler(req, root)
    except Refused as exc:
        body = {"error": str(exc)}
    return json.dumps({"schema": SCHEMA, **body}, ensure_ascii=False)


# ---- 置き場の綴り -------------------------------------------------------------------


def _env_from_settings(text: str | None) -> dict[str, str]:
    """統合先の `.claude/settings.json` の `env` のうち、置き場の綴りに効くものだけ。"""
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise Refused(f"統合先の {SETTINGS_FILE} を JSON として読めない ({exc})") from None
    env = data.get("env") if isinstance(data, dict) else None
    if not isinstance(env, dict):
        return {}
    return {k: v for k, v in env.items() if k in PLACEMENT_ENV and isinstance(v, str) and v}


def _placement(settings_text: str | None) -> dict:
    env = _env_from_settings(settings_text)
    absolute = sorted(k for k, v in env.items() if _ABSOLUTE.match(v))
    if absolute:
        raise Refused(
            "置き場の綴りがリポジトリの外を指している（"
            + ", ".join(f"{k}={env[k]}" for k in absolute)
            + "）。ブランチに乗らないので Chrome では読めない（ADR-0093 の 3.1 の 12）"
        )
    with _environ(env):
        conf, _ = settings.load("/nonexistent-ccnavi-root")
    tickets = conf.tickets.strip("/")
    approved = conf.approved.strip("/")
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("\\", "/").strip("/")
    for name, value in (("提案", tickets), ("承認済み", approved), ("層", home)):
        if not value or ".." in value.split("/"):
            raise Refused(f"{name}の置き場の綴りを読めない: {value!r}")
    own_layer = f"{home}/{settings.LAYER_CONFIG_DIR}"
    return {
        "tickets": tickets,
        "approved": approved,
        "env": env,
        # 統合先から読むもの。承認済みは閉じたものだけ（権威は `P`。3.3 の 1・4）。
        "integration_paths": sorted({f"{approved}/{ticket_mod.DONE}", COMMON_LAYER, own_layer}),
        "integration_files": [SETTINGS_FILE, COMPAT_FILE],
        # 家族のブランチから読むもの。
        "branch_paths": [approved, tickets],
        # プロジェクトのリポジトリ（段階 5）。ワークスペースの統合先から読むもの（共通層・自身の層・
        # 設定・互換のマーカー）と、プロジェクトの統合先から読むもの（閉じたもの・プロジェクトの層）
        "workspace_paths": sorted({COMMON_LAYER, own_layer}),
        "workspace_files": [SETTINGS_FILE, COMPAT_FILE],
        "project_paths": sorted({f"{approved}/{ticket_mod.DONE}", own_layer}),
        "layer_dir": own_layer,
    }


def _op_placement(req: dict, root: str) -> dict:
    return {"placement": _placement(_text_or_none(req.get("settings")))}


# ---- Snapshot の読み -------------------------------------------------------------------


def _snapshot(req: dict) -> dict:
    snap = req.get("snapshot")
    if not isinstance(snap, dict):
        raise Refused("snapshot が無い")
    integ = snap.get("integration")
    branches = snap.get("branches")
    if not isinstance(integ, dict) or not isinstance(branches, dict):
        raise Refused("snapshot の形が違う（integration と branches が要る）")
    name = integ.get("name")
    if not isinstance(name, str) or name not in branches:
        raise Refused("統合先のブランチの中身が snapshot に無い")
    for bname, branch in branches.items():
        _check_files(bname, branch)
    project = snap.get("project") or ""
    if project:
        if not isinstance(project, str) or not ticket_mod.is_valid_id(project):
            raise Refused(f"プロジェクト名が読めない: {project!r}")
        if settings.is_reserved_layer_name(project):
            raise Refused(f"プロジェクト名 {project} は層の名前として予約してある（common・self）")
        ws = snap.get("workspace")
        if not isinstance(ws, dict) or not isinstance(ws.get("integration"), dict):
            raise Refused("プロジェクトのリポジトリにはワークスペースの統合先（workspace）が要る")
        if not isinstance(ws["integration"].get("name"), str):
            raise Refused("ワークスペースの統合先の名前が無い")
        _check_files("(ワークスペース)", ws)
    return snap


def _check_files(bname: str, branch: object) -> None:
    files = branch.get("files") if isinstance(branch, dict) else None
    if not isinstance(files, dict):
        raise Refused(f"ブランチ {bname} の files が無い")
    for path, text in files.items():
        _check_rel(path)
        if not isinstance(text, str):
            raise Refused(f"{bname}:{path} の本文が文字列でない")


def _project(snap: dict) -> str:
    """このリポジトリのプロジェクト名（ワークスペース自身なら空。段階 5）。"""
    return str(snap.get("project") or "")


def _workspace_files(snap: dict) -> dict[str, str]:
    """ワークスペースの統合先の中身（共通層・設定・互換のマーカー）。ワークスペース自身なら統合先。"""
    if _project(snap):
        return snap["workspace"]["files"]
    return _files(snap, snap["integration"]["name"])


def _check_rel(path: object) -> str:
    """ホストから来た相対パスを、仮のツリーの外へ出ない形に限る。"""
    if not isinstance(path, str) or not path or _ABSOLUTE.match(path) or "\\" in path:
        raise Refused(f"読めないパス: {path!r}")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise Refused(f"読めないパス: {path!r}")
    return path


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _files(snap: dict, name: str) -> dict[str, str]:
    branch = snap["branches"].get(name)
    return branch["files"] if isinstance(branch, dict) else {}


def _tickets_in(files: dict[str, str], place: dict, states: tuple[str, ...] | None = None):
    """ブランチの置き場にあるチケット（提案と承認済み）。読めないものは飛ばす。"""
    dirs = [(place["tickets"], s) for s in ticket_mod.STATES] + [
        (place["approved"], ticket_mod.DOING),
        (place["approved"], ticket_mod.DONE),
    ]
    for base, state in dirs:
        if states is not None and state not in states:
            continue
        prefix = f"{base}/{state}/"
        for path, text in files.items():
            rest = path[len(prefix) :] if path.startswith(prefix) else ""
            if not rest.endswith(".md") or "/" in rest:
                continue
            t, _ = ticket_mod.parse(text)
            if t is not None and t.ticket == rest[:-3]:
                yield state, t


def family_of(ident: str) -> str:
    """識別子の家族の親（3.3 の 5）。子の形なら `parent`、そうでなければ自分。"""
    m = ticket_mod.child_pattern().match(ident)
    return m.group("parent") if m else ident


def _closed(snap: dict, place: dict) -> set[str]:
    """統合先の `done/` にある識別子（取り消し済みを含む）。"""
    files = _files(snap, snap["integration"]["name"])
    return {t.ticket for _, t in _tickets_in(files, place, (ticket_mod.DONE,))}


# ---- 家族 -------------------------------------------------------------------------------


def _op_families(req: dict, root: str) -> dict:
    """候補のブランチのうち、親のブランチ（家族）であるもの。

    家族 = そのブランチの置き場に、ブランチ名と同じ識別子の親の提案か写しがある（4.2 の見分け）。
    閉じた親（`done/`）しか無いブランチは数えない。
    """
    snap = _snapshot(req)
    place = _placement(_text_or_none(req.get("settings")))
    closed = _closed(snap, place)
    candidates = req.get("candidates")
    if not isinstance(candidates, list):
        raise Refused("candidates が無い")
    found = []
    for name in candidates:
        if not isinstance(name, str) or name == snap["integration"]["name"]:
            continue
        files = _files(snap, name)
        parents = [
            (state, t)
            for state, t in _tickets_in(files, place, (*ticket_mod.STATES, ticket_mod.DOING))
            if t.ticket == name and not t.is_child
        ]
        if not parents:
            continue
        state, t = parents[0]
        found.append({"name": name, "title": t.title, "state": state, "closed": name in closed})
    return {"families": found}


def _op_closure(req: dict, root: str) -> dict:
    """家族 `family` の判定に要る家族の閉包（3.3 の 5）。

    提案と写しの `predecessors` を辿り、統合先の `done/` にあるもので止める。
    まだ読んでいない家族は `need` で返し、拡張が読んでから呼び直す。
    """
    snap = _snapshot(req)
    place = _placement(_text_or_none(req.get("settings")))
    family = req.get("family")
    if not isinstance(family, str) or not family:
        raise Refused("family が無い")
    return _closure(snap, place, family)


def _closure(snap: dict, place: dict, family: str) -> dict:
    closed = _closed(snap, place)
    absent = set(snap.get("absent") or [])
    families = [family]
    need: list[str] = []
    queue = [family]
    while queue:
        name = queue.pop(0)
        if name not in snap["branches"]:
            if name not in absent:
                need.append(name)
            continue
        for _, t in _tickets_in(_files(snap, name), place):
            for ident in t.predecessors:
                fam = family_of(ident)
                if fam in families or ident in closed or fam in closed:
                    continue
                families.append(fam)
                queue.append(fam)
    over = len(families) > FAMILY_LIMIT
    return {
        "families": families,
        "need": [] if over else need,
        "absent": sorted(absent & set(families)),
        "over_limit": over,
        "message": (
            f"先行を辿ると {FAMILY_LIMIT} を超える家族に広がった（{', '.join(families)}）。"
            "Chrome では判定できない。計画を分けて先行を減らすか、手元で "
            "`--approve --preview --verify` を打って確かめる"
            if over
            else ""
        ),
    }


# ---- 仮のツリーと承認待ち ---------------------------------------------------------------


def _build(root: str, snap: dict, place: dict, families: list[str]) -> None:
    """統合先をワークスペースルートに、家族をワークツリーに置いた仮のツリーを組む。

    プロジェクトのリポジトリ（段階 5）は、ワークスペースの統合先をワークスペースルートに、
    プロジェクトの統合先を `projects/<名前>/` に置き、家族をそのプロジェクトのワークツリーにする。
    """
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, ".git", "worktrees"))
    integ = snap["integration"]["name"]
    project = _project(snap)
    owner = root
    if not project:
        # HEAD はブランチの名前を読む先（承認の記録の `source_tree`。D22）。
        _head(os.path.join(root, ".git"), integ)
        keep = tuple(p + "/" for p in place["integration_paths"])
        for path, text in _files(snap, integ).items():
            if path.startswith(keep) or path in place["integration_files"]:
                _write(root, path, text)
    else:
        ws = snap["workspace"]
        _head(os.path.join(root, ".git"), ws["integration"]["name"])
        keep = tuple(p + "/" for p in place["workspace_paths"])
        for path, text in ws["files"].items():
            if path.startswith(keep) or path in place["workspace_files"]:
                _write(root, path, text)
        owner = os.path.join(root, settings.DEFAULT_PROJECTS, project)
        os.makedirs(os.path.join(owner, ".git", "worktrees"))
        _head(os.path.join(owner, ".git"), integ)
        done = f"{place['approved']}/{ticket_mod.DONE}/"
        for path, text in _files(snap, integ).items():
            if path.startswith(done):
                _write(owner, path, text)
        for rel, text in project_layer(snap, place).items():
            _write(owner, rel, text)
    for name in families:
        if name not in snap["branches"] or name == integ:
            continue
        if not ticket_mod.is_valid_id(name):
            raise Refused(f"家族のブランチ名が識別子の形でない: {name!r}")
        tree = os.path.join(root, *WORKTREES.split("/"), name)
        os.makedirs(tree, exist_ok=True)
        registry = os.path.join(owner, ".git", "worktrees", name)
        os.makedirs(registry)
        gitfile = os.path.join(tree, ".git")
        with open(gitfile, "w", encoding="utf-8") as f:
            f.write(f"gitdir: {registry}\n")
        with open(os.path.join(registry, "gitdir"), "w", encoding="utf-8") as f:
            f.write(gitfile + "\n")
        _head(registry, name)
        for path, text in _files(snap, name).items():
            _write(tree, path, text)
    for rel, text in records(snap, place, families).items():
        _write(root, f"{STATE_DIR}/{rel}", text)


def project_layer(snap: dict, place: dict) -> dict[str, str]:
    """プロジェクトの層（プロジェクトからの相対パス → 中身）。ADR-0093 の D28 の計算。

    「プロジェクトの統合先の現在の層に、ワークスペースの統合先の共通層を `configsync.projected` で
    写したもの」。共通層にあるファイルだけを写し、無いファイルはプロジェクトの側を残す（着手の
    configsync と同じ）。`P` の上の層は読まない（3.3 の 6）。
    """
    ws = snap["workspace"]["files"]
    own = _files(snap, snap["integration"]["name"])
    with _environ(place["env"]):
        conf, _ = settings.load("/nonexistent-ccnavi-root")
    out: dict[str, str] = {}
    for kind, name in settings.LAYER_FILE_NAMES.items():
        rel = f"{place['layer_dir']}/{name}"
        common = ws.get(f"{COMMON_LAYER}/{name}")
        if common is not None:
            out[rel] = configsync.projected(conf, kind, common.encode("utf-8")).decode("utf-8")
        elif rel in own:
            out[rel] = own[rel]
    return out


def records(snap: dict, place: dict, families: list[str]) -> dict[str, str]:
    """取り込みの控え相当（控えの置き場からの相対パス → 中身）。手元の `ccnavi-sync.sh` が書く形。

    - 統合先の控え（`sync/self/integration/`）: 統合先の `done/`・共通層・自身の層・
      `.claude/settings.json` の写しと `head`
    - 家族の控え（`sync/self/families/<P>`）: ホストに在る家族は `present`、無い家族は `gone`

    先頭の sha と取り込んだ時刻は書かない。判定が読んだ中身（read_set）に入り、指紋が
    関係の無い push で変わるため（6.2「全ブランチの head_sha は入れない」）。手元の試験も
    これを控えの置き場に書き、同じ控えで判定させる。
    """
    integ = snap["integration"]
    project = _project(snap)
    base = f"sync/{project or SELF_REPO}"
    out: dict[str, str] = {
        f"{base}/integration/head": (
            f"remote origin\nbranch {integ['name']}\nsource {integ.get('source') or ''}\n"
        ),
    }
    if not project:
        keep = tuple(p + "/" for p in place["integration_paths"])
        for path, text in _files(snap, integ["name"]).items():
            if path.startswith(keep) or path == SETTINGS_FILE:
                out[f"{base}/integration/{path}"] = text
    else:
        # プロジェクトの統合先の控え（閉じたものとプロジェクトの層）と、ワークスペースの統合先の控え
        keep = tuple(p + "/" for p in place["project_paths"])
        for path, text in _files(snap, integ["name"]).items():
            if path.startswith(keep):
                out[f"{base}/integration/{path}"] = text
        ws = snap["workspace"]
        wsbase = f"sync/{SELF_REPO}"
        out[f"{wsbase}/integration/head"] = (
            f"remote origin\nbranch {ws['integration']['name']}\n"
            f"source {ws['integration'].get('source') or ''}\n"
        )
        keep = tuple(p + "/" for p in place["workspace_paths"])
        for path, text in ws["files"].items():
            if path.startswith(keep) or path == SETTINGS_FILE:
                out[f"{wsbase}/integration/{path}"] = text
    absent = set(snap.get("absent") or [])
    for name in families:
        if name == integ["name"]:
            continue
        if name in snap["branches"]:
            out[f"{base}/families/{name}"] = f"remote origin\nbranch {name}\nstate present\n"
        elif name in absent:
            out[f"{base}/families/{name}"] = (
                f"remote origin\nbranch {name}\nstate gone\n"
                f"reason 親のブランチ {name} がホストに無い\n"
            )
    return dict(sorted(out.items()))


def _head(gitdir: str, branch: str) -> None:
    with open(os.path.join(gitdir, "HEAD"), "w", encoding="utf-8") as f:
        f.write(f"ref: refs/heads/{branch}\n")


def _write(base: str, rel: str, text: str) -> None:
    full = os.path.join(base, *_check_rel(rel).split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write(text)


class _environ:
    """置き場の綴りの環境変数を、この呼び出しのあいだだけ入れる。他の ccnavi の変数は外す。"""

    def __init__(self, env: dict[str, str]):
        self.env = env
        self.saved: dict[str, str] = {}

    def __enter__(self):
        for k in list(os.environ):
            if k.startswith("CCNAVI_") or k == "CLAUDE_PROJECT_DIR":
                self.saved[k] = os.environ.pop(k)
        os.environ.update(self.env)
        return self

    def __exit__(self, *exc):
        for k in self.env:
            os.environ.pop(k, None)
        os.environ.update(self.saved)


def _preview(root: str, env: dict[str, str], only: list[str]) -> tuple[int, dict | None, str]:
    out, err = io.StringIO(), io.StringIO()
    with _environ(env):
        code = cli.run(
            io.StringIO(""), out, err, ["--root", root, "--approve", "--preview", "--json", *only]
        )
    try:
        body = json.loads(out.getvalue()) if code == 0 else None
    except ValueError:
        body = None
    return code, body, err.getvalue()


def _op_board(req: dict, root: str) -> dict:
    """家族 1 つの承認待ち（読み取りだけ）。判定の入力は統合先・`P`・閉包の `P_X`（D2）。"""
    snap = _snapshot(req)
    place = _placement(_text_or_none(req.get("settings")))
    family = req.get("family")
    if not isinstance(family, str) or family not in snap["branches"]:
        raise Refused("family のブランチが snapshot に無い")
    closure = _closure(snap, place, family)
    if closure["over_limit"]:
        return {"family": family, "undecided": closure["message"], "closure": closure}
    if closure["need"]:
        raise Refused(f"閉包の家族をまだ読んでいない: {', '.join(closure['need'])}")
    unreadable = _unreadable(snap, closure)
    if unreadable:
        return {"family": family, "undecided": unreadable, "closure": closure}
    _build(root, snap, place, closure["families"])

    code, first, err = _preview(root, place["env"], [])
    if first is None:
        return {"family": family, "closure": closure, "refused": _refused(root, code, err)}
    mine = [e["ticket"] for e in first["batch"] if (e.get("parent") or e["ticket"]) == family]
    body = first
    narrowed = bool(mine) and len(mine) != len(first["batch"])
    if narrowed:
        # 画面の本文と指紋を、この家族の分だけで組み直す（1 回の承認は 1 つの `P`。8.3）。
        code, body, err = _preview(root, place["env"], mine)
        if body is None:
            return {"family": family, "closure": closure, "refused": _refused(root, code, err)}
    batch = [_entry(root, e) for e in body["batch"] if e["ticket"] in mine]
    return {
        "family": family,
        "closure": closure,
        # 書けるか（互換の版・書く先の名前。7.3・8.5）。書けなければ表示だけにする。
        "write": {
            "allowed": not _write_refusal(snap, family),
            "reason": _write_refusal(snap, family),
        },
        "withdrawable": _withdrawable(req, root, place, family),
        # レビュー済みを付けられる候補（段階 4）。通るかは `confirm` がホストの写しで決める
        "reviewable": _reviewable(root, place, family),
        "batch": batch,
        # 画面の本文は提案をツリーからの相対パスで出す（D22）ので、手を加えずに返す。
        # 指紋はこの本文と写しの中身を覆い、手元の `--approve --preview` と同じ値になる。
        "text": body["text"] if batch else "",
        "digest": body["digest"] if batch else "",
        # 承認するときに `plan` へ渡す絞り（この指紋を出したときの絞り。絞らなければ null）
        "only": mine if narrowed else None,
        "rejected": [
            {"ticket": r["ticket"], "problems": [_relative(root, p) for p in r["problems"]]}
            for r in first["rejected"]
            if family_of(r["ticket"]) == family
        ],
        "problems": [_relative(root, p) for p in first["problems"]],
    }


def _unreadable(snap: dict, closure: dict) -> str:
    """判定の入力に本文を読めなかったファイルがあれば、止める理由。

    バイナリ（`binary`）とシンボリックリンク（`links`。中身は指す先の綴りで、手元の読み方と違う）は
    読まない。判定がそれを読むかは分からないので、無いとも空とも読ませず「決まらない」で止める
    （6.2 の `NOT_FETCHED`。段階 1 は言うだけだった。段階 3 で書くようになったので締めた）。
    リンクの家族は止まるので、リンクの綴りへ書くことも無い。
    """
    names = [snap["integration"]["name"], *closure["families"]]
    found = [
        f"{name}:{path}（{what}）"
        for name in dict.fromkeys(names)
        for key, what in (("binary", "バイナリ"), ("links", "シンボリックリンク"))
        for path in (snap["branches"].get(name) or {}).get(key) or []
    ]
    if _project(snap):
        ws = snap["workspace"]
        found += [
            f"(ワークスペース) {ws['integration']['name']}:{path}（{what}）"
            for key, what in (("binary", "バイナリ"), ("links", "シンボリックリンク"))
            for path in ws.get(key) or []
        ]
    if not found:
        return ""
    return (
        f"判定の入力に本文を読めないファイルがある（{', '.join(found)}）。"
        "この家族は決まらない。手元で `--approve --preview --verify` を打って確かめる"
    )


def _write_refusal(snap: dict, family: str) -> str:
    """この家族の `P` へ書けない理由（空なら書ける）。

    - 同梱の互換の版が統合先と違う（7.3・D31）。表示だけにする
    - 書く先が予約の名前か統合先の名前（8.5。保護されたブランチと統合先へは書かない）
    """
    compat = _compat(snap)
    if not compat["same"]:
        return (
            f"{compat['message']}。表示だけにして、承認と取り下げのボタンは出さない"
            "（ADR-0093 の 7.3）"
        )
    folded = family.casefold()
    if (
        folded in ticket_mod.RESERVED_BRANCH_IDS
        or folded.startswith("release-")
        or folded == snap["integration"]["name"].casefold()
    ):
        return f"{family} は予約の名前か統合先の名前なので、Chrome からは書かない"
    return ""


def _withdrawable(req: dict, root: str, place: dict, family: str) -> list[dict]:
    """作業中の写しごとの取り下げの可否（8.8）。仮のツリーは組んである前提。"""
    with _environ(place["env"]):
        conf, _ = settings.load(root)
        snapshot = core.read_fs(conf, root)
        found = core.withdrawable(snapshot, family)
    return [
        {"ticket": ident, "title": title, "problems": [_relative(root, p) for p in problems]}
        for ident, title, problems in found
    ]


def _refused(root: str, code: int, err: str) -> str:
    """`--approve --preview` が承認待ちを出せなかった理由（標準エラーの文面）。"""
    return _relative(root, err.strip()) or f"終了 {code}"


def _entry(root: str, entry: dict) -> dict:
    """一覧の 1 行に、ブランチの上の相対パスと本文（Markdown）を添える。"""
    path = entry.get("path") or ""
    t, _ = ticket_mod.load(path) if path else (None, [])
    return {
        **{k: v for k, v in entry.items() if k != "path"},
        "path": _relative(root, path),
        "body": t.body if t is not None else "",
    }


# 仮のツリーの綴りの前に来てよい字（行の頭・空白・引用符・括弧・区切り）。
# 途中の段（`wip/ws/`）は畳まない。
_BEFORE_ROOT = r"(?<![^\s'\"`(（「:：,、=])"


def _relative(root: str, text: str) -> str:
    """文面の中の仮のツリーの綴りを畳む。

    `<root>/.claude/worktrees/<P>/` を `<P>:` に、`<root>/` を空にする。畳むのは綴りの頭（行の頭か、
    空白・引用符・括弧・区切りの直後）に在るときだけで、パスの途中の同じ綴り（`wip/ws/x`）は
    畳まない。書くもの（Changes）のパスは既にツリーからの相対なので、ここを通さない。
    """
    base = re.escape(os.path.join(root, *WORKTREES.split("/")) + os.sep)
    out = re.sub(
        _BEFORE_ROOT + base + r"([A-Za-z0-9][A-Za-z0-9._-]*)" + re.escape(os.sep), r"\1:", text
    )
    out = re.sub(_BEFORE_ROOT + re.escape(root + os.sep), "", out)
    return out.replace(os.sep, "/") if os.sep != "/" else out


# ---- 判定のコア（段階 2a。書くものを値で返すだけ） ---------------------------------------


def _family_tree(req: dict, root: str) -> tuple[dict, dict, str, dict]:
    """家族の仮のツリーを組む。答えは (snapshot, 置き場, 家族, 閉包)。時刻は組む前に確かめる。"""
    _stamp(req)
    snap = _snapshot(req)
    place = _placement(_text_or_none(req.get("settings")))
    family = req.get("family")
    if not isinstance(family, str) or family not in snap["branches"]:
        raise Refused("family のブランチが snapshot に無い")
    closure = _closure(snap, place, family)
    if closure["over_limit"]:
        raise Refused(closure["message"])
    if closure["need"]:
        raise Refused(f"閉包の家族をまだ読んでいない: {', '.join(closure['need'])}")
    unreadable = _unreadable(snap, closure)
    if unreadable:
        raise Refused(unreadable)
    _build(root, snap, place, closure["families"])
    return snap, place, family, closure


def _unwritten(root: str, notes: str) -> None:
    """並べる段で跡を書けないと分かったら、書くものを出さずに止める（`core.withdraw` と揃える）。"""
    lines = [
        _relative(root, line.replace("ccnavi: 警告: ", "ccnavi: ", 1))
        for line in notes.splitlines()
        if line
    ]
    if lines:
        raise Refused("\n".join(lines))


def _writable(snap: dict, family: str) -> None:
    """書く操作の前に、書けない理由があれば受けない（`_write_refusal`）。"""
    why = _write_refusal(snap, family)
    if why:
        raise Refused(why)


def _one_branch(changes: dict, family: str) -> None:
    """書くものが親のブランチ `P` の中だけか（1 回の承認は 1 つの `P`。8.3・8.4）。

    提案が `P` 以外のブランチ（閉包の `P_X` など）にあると、消す先が別のブランチになる。
    その形は承認しない（8.4「P 以外のブランチにある提案は承認しない」）。
    """
    others = sorted(k for k in (changes.get("changes") or {}) if k != family)
    if others:
        raise Refused(
            f"書くものが親のブランチ {family} の外（{', '.join(others or ['?'])}）に及ぶ。"
            f"提案は {family} の上に置いてから承認を頼む（ADR-0093 の 3.2）"
        )


def _stamp(req: dict) -> str:
    """要求の時刻（`fsio.stamp` と同じ、オフセット付き）。読めなければ Refused。"""
    stamp = req.get("stamp")
    if not isinstance(stamp, str) or not stamp:
        raise Refused("stamp（オフセット付きの時刻）が無い")
    try:
        with fsio.clock(stamp):
            pass
    except ValueError as exc:
        raise Refused(str(exc)) from None
    return stamp


def _core_snapshot(req: dict, root: str) -> core.Snapshot:
    """`ccnavi.core` の入力。時刻（`stamp`）と誰が（`actor`）は要求から取る。"""
    actor = req.get("actor") if isinstance(req.get("actor"), dict) else {}
    conf, _ = settings.load(root)
    return core.read_fs(
        conf,
        root,
        _stamp(req),
        core.Actor(
            str(actor.get("account") or ""),
            history.VIA_CHROME,
            str(actor.get("version") or ""),
        ),
    )


def _changes(root: str, changes: core.Changes | None) -> dict:
    """Changes を JSON にする。中身は UTF-8 の本文、読めなければ base64。"""
    if changes is None:
        return {"changes": None, "lines": [], "stopped": None}
    out: dict[str, list[dict]] = {}
    for branch, entries in changes.per_branch().items():
        rows = []
        for e in entries:
            # per_branch のパスはツリーからの相対（ツリーの外は `""` の鍵で絶対パス。
            # `_one_branch` が断る）
            row = {"op": e["op"], "path": e["path"]}
            if "content" in e:
                try:
                    row["content"] = e["content"].decode("utf-8")
                except UnicodeDecodeError:
                    row["base64"] = base64.b64encode(e["content"]).decode("ascii")
            rows.append(row)
        out[branch] = rows
    stopped = changes.planned.stopped
    return {
        "changes": out,
        "lines": [_relative(root, line) for line in changes.lines],
        "stopped": (
            {"ticket": stopped[0], "reason": _relative(root, stopped[1])} if stopped else None
        ),
    }


def _op_plan(req: dict, root: str) -> dict:
    """家族 1 つの承認で書くもの（6.2 の judge → plan）。書かない。"""
    snap, place, family, _ = _family_tree(req, root)
    only = req.get("only")
    if only is not None and not (isinstance(only, list) and all(isinstance(i, str) for i in only)):
        raise Refused("only は識別子の並び")
    shown = req.get("shown")
    shown_ids = shown_digest = None
    if shown is not None:
        # 見せた一覧と指紋（8.3 の 2）。違えば書くものを出さず、preview からやり直させる。
        ids = shown.get("ids") if isinstance(shown, dict) else None
        digest = shown.get("digest") if isinstance(shown, dict) else None
        if not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)) or not (
            isinstance(digest, str) and digest
        ):
            raise Refused("shown は見せた識別子の並び（ids）と指紋（digest）")
        shown_ids, shown_digest = ids, digest
    _writable(snap, family)
    notes = io.StringIO()
    with _environ(place["env"]):
        snapshot = _core_snapshot(req, root)
        actor = snapshot.actor
        with history.session(actor.via or history.VIA_CHROME, notes, actor.account, actor.version):
            verdict = core.judge_approval(snapshot, only, shown_ids, shown_digest)
            refused = verdict.gathered.refused
            ready = verdict.batch and not refused and verdict.mismatch is None
            changes = core.plan(snapshot, verdict) if ready else None
    _unwritten(root, notes.getvalue())
    body = _changes(root, changes)
    _one_branch(body, family)
    return {
        "family": family,
        "identifiers": verdict.identifiers,
        "text": verdict.screen_text,
        "digest": verdict.digest,
        "mismatch": verdict.mismatch,
        "refused": _relative(root, refused),
        "rejected": [
            {"ticket": t.ticket, "problems": [_relative(root, str(p)) for p in complaints]}
            for t, complaints in verdict.rejected
        ],
        **body,
    }


def _op_withdraw(req: dict, root: str) -> dict:
    """承認の取り下げで書くもの（8.8）。`prior` は識別子ごとの承認コミットの親の提案の本文。"""
    snap, place, family, _ = _family_tree(req, root)
    _writable(snap, family)
    ids = req.get("ids")
    prior = req.get("prior")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise Refused("ids は識別子の並び")
    if not isinstance(prior, dict) or not all(isinstance(v, str) for v in prior.values()):
        raise Refused("prior は識別子ごとの本文")
    reason = req.get("reason") if isinstance(req.get("reason"), str) else ""
    with _environ(place["env"]):
        snapshot = _core_snapshot(req, root)
        checked = core.withdraw(
            snapshot, ids, {k: v.encode("utf-8") for k, v in prior.items()}, reason
        )
    body = _changes(root, checked.changes)
    _one_branch(body, family)
    return {
        "family": family,
        "problems": [_relative(root, p) for p in checked.problems],
        **body,
    }


def _op_confirm(req: dict, root: str) -> dict:
    """レビュー済みで書くもの（8.9。段階 4）。書かない。

    `result` は `ccnavi-review.sh` が組むのと同じ形の写し（`{host, mr, threads, reviews}`）。
    依頼の後に人が見るものが動いたかは、手元の confirm と同じ関数（`review.moved_since`）で決める。
    材料は読んだ `P` の先頭と、依頼時の先頭からの変更の一覧（`compare`）。依頼時の先頭は Python が
    マーカーから読み、一覧が要るのに無ければ `need_compare`（`{base, head}`）で返す。拡張は
    compare API で読んでから呼び直す（閉包の `need` と同じ形）。
    """
    snap, place, family, _ = _family_tree(req, root)
    _writable(snap, family)
    phase_no = req.get("phase")
    if not isinstance(phase_no, int) or isinstance(phase_no, bool):
        raise Refused("phase はフェーズの番号")
    result = review.Result.from_data(req.get("result"))
    if result.error:
        raise Refused(f"result を読めない: {result.error}")
    head = str(snap["branches"][family].get("head") or "")
    compare = req.get("compare")
    with _environ(place["env"]):
        snapshot = _core_snapshot(req, root)
        recorded = core.requested_head(snapshot, family, phase_no)
        if compare is None and recorded and recorded != head and review._is_sha(recorded):
            return {
                "family": family,
                "need_compare": {"base": recorded, "head": head},
                "problems": [],
                **_changes(root, None),
            }
        changed = core.moved_on_host(
            snapshot, family, phase_no, head, _compare_files(compare, recorded, head)
        )
        checked = core.confirm(snapshot, family, phase_no, result, changed)
    body = _changes(root, checked.changes)
    _one_branch(body, family)
    return {
        "family": family,
        "problems": [_relative(root, p) for p in checked.problems],
        **body,
    }


def _compare_files(compare: object, base: str | None, head: str) -> list[str] | None:
    """compare API の変更の一覧（`{base, head, files}`）。読めなければ None（動いたと数える）。

    比べた 2 つがマーカーの先頭と読んだ `P` の先頭でなければ使わない。`files` が null（一覧が
    打ち切られた・依頼時のコミットが祖先でない・ホストに無い）も None。
    """
    if compare is None:
        return None
    if not isinstance(compare, dict):
        raise Refused("compare は {base, head, files}")
    files = compare.get("files")
    if files is not None and not (
        isinstance(files, list) and all(isinstance(f, str) for f in files)
    ):
        raise Refused("compare の files はパスの並びか null")
    if compare.get("base") != base or compare.get("head") != head or files is None:
        return None
    return list(files)


def _reviewable(root: str, place: dict, family: str) -> list[dict]:
    """依頼済みでまだレビュー済みでないフェーズ（8.9）。仮のツリーは組んである前提。"""
    with _environ(place["env"]):
        conf, _ = settings.load(root)
        return core.reviewable(core.read_fs(conf, root), family)


# ---- 互換のマーカー（7.3） --------------------------------------------------------------------


def _op_compat(req: dict, root: str) -> dict:
    return {"compat": _compat(_snapshot(req))}


def _compat(snap: dict) -> dict:
    # 互換のマーカーはワークスペースの統合先にある
    # （プロジェクトのリポジトリはワークスペースのものを読む）
    text = _workspace_files(snap).get(COMPAT_FILE)
    ours = version.COMPAT
    found = lint._SH_COMPAT.search(text) if text else None
    theirs = int(found.group(1)) if found else None
    if theirs == ours:
        message = ""
    elif theirs is None:
        message = (
            f"統合先の {COMPAT_FILE} に互換の版（CCNAVI_COMPAT）が書かれていない。拡張は互換 {ours}"
        )
    else:
        hint = "拡張を更新する" if theirs > ours else "リポジトリの ccnavi の更新を待つ"
        message = f"拡張は互換 {ours}、リポジトリは互換 {theirs}。{hint}"
    return {"extension": ours, "repository": theirs, "same": theirs == ours, "message": message}


# ---- 「始める」（8.6。段階 5） -------------------------------------------------------------


def _op_start(req: dict, root: str) -> dict:
    """issue から始める親のブランチの名前と、始められない理由（8.6・3.1 の 4・5・7）。

    ブランチは作らない（拡張の service worker が作る）。入力は統合先（と、プロジェクトなら
    ワークスペースの統合先）と、拡張が見たブランチ（`snapshot.branches` と `taken` の名前）。
    """
    snap = _snapshot(req)
    place = _placement(_text_or_none(req.get("settings")))
    number = req.get("issue")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise Refused("issue は正の整数（issue の番号）")
    taken = req.get("taken") or []
    if not isinstance(taken, list) or not all(isinstance(n, str) for n in taken):
        raise Refused("taken はブランチ名の並び")
    project = _project(snap)
    ident = ticket_mod.issue_identifier(number, project)
    integ = snap["integration"]["name"]
    folded = ident.casefold()
    problems = list(
        ticket_mod.branch_name_problems(
            ticket_mod.Ticket(ticket=ident, issue=number, project=project), integ
        )
    )
    if folded in {i.casefold() for i in _closed(snap, place)}:
        problems.append(
            f"{ident} は統合先 {integ} の done/ で閉じている（閉じた識別子は使い直さない。"
            "ADR-0093 の 3.1 の 5）"
        )
    names = [n for n in [*snap["branches"], *taken] if n != integ]
    same = sorted({n for n in names if n.casefold() == folded})
    if same:
        problems.append(f"同じ名前のブランチが既にある（{', '.join(same)}）")
    for name in sorted(snap["branches"]):
        if name == integ:
            continue
        hits = sorted(
            {
                t.ticket
                for _, t in _tickets_in(_files(snap, name), place)
                if t.ticket.casefold() == folded or family_of(t.ticket).casefold() == folded
            }
        )
        if hits:
            problems.append(f"開いた家族 {name} に同じ識別子がある（{', '.join(hits)}）")
    compat = _compat(snap)
    if not compat["same"]:
        problems.append(f"{compat['message']}（ADR-0093 の 7.3）")
    if problems:
        problems.append(
            "この issue からは始められない。識別子を人が付けて（フォールバック）始める"
            "（ADR-0093 の 3.2・8.6）"
        )
    return {"identifier": ident, "integration": integ, "problems": problems}


def _op_version(req: dict, root: str) -> dict:
    return {"version": version.VERSION, "compat": version.COMPAT, "python": sys.version.split()[0]}


_OPS = {
    "placement": _op_placement,
    "families": _op_families,
    "closure": _op_closure,
    "board": _op_board,
    "plan": _op_plan,
    "withdraw": _op_withdraw,
    "confirm": _op_confirm,
    "compat": _op_compat,
    "start": _op_start,
    "version": _op_version,
}


def main(argv: list[str]) -> int:
    """手元の試験用: `python ccnavi_chrome.py <root>` で、標準入力の 1 行 1 要求に 1 行で答える。

    拡張の試験が、Pyodide と同じ要求を手元の CPython にも投げて答えを
    突き合わせる（ADR-0093 の 6.2）。
    """
    root = argv[1] if len(argv) > 1 else ""
    if not root:
        sys.stderr.write("使い方: ccnavi_chrome.py <仮のツリーを組む空のディレクトリ>\n")
        return 1
    for line in sys.stdin:
        if line.strip():
            sys.stdout.write(handle(line, os.path.realpath(root)) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

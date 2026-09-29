"""Chrome 拡張（ADR-0093 段階 1）が Pyodide の上で呼ぶ入口。

拡張はホストの API でブランチの置き場を読み、その中身（Snapshot）をここへ渡す。ここは
ブランチごとの仮のツリーを MEMFS に組み、今の ccnavi（`--approve --preview --json`）を
そのまま動かす（ADR-0093 の 8.1）。承認待ちの一覧も、家族の見分けも、参照の閉包も、
互換の比べも Python が出し、拡張（TS）は並べるだけ（ADR-0035）。

段階 2a から、判定のコア（`ccnavi.core`）の `plan`・`withdraw`・`confirm` も呼べる
（`plan`・`withdraw`・`confirm` の操作）。どれも書くもの（Changes）を値で返すだけで、
ホストにもディスクにも書かない（fsio の控える段）。拡張の画面はまだ使わず（承認は段階 3、
レビュー済みは段階 4）、手元の CPython と Pyodide が同じバイト列を出すことの試験に使う
（ADR-0093 の 6.2）。

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
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import sys

from ccnavi import cli, core, fsio, history, lint, review, settings, version
from ccnavi import ticket as ticket_mod

# 要求と答えの形の版。拡張の `PY_SCHEMA` と揃える。
SCHEMA = 1

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
        files = branch.get("files") if isinstance(branch, dict) else None
        if not isinstance(files, dict):
            raise Refused(f"ブランチ {bname} の files が無い")
        for path, text in files.items():
            _check_rel(path)
            if not isinstance(text, str):
                raise Refused(f"{bname}:{path} の本文が文字列でない")
    return snap


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
    """統合先をワークスペースルートに、家族をワークツリーに置いた仮のツリーを組む。"""
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(os.path.join(root, ".git", "worktrees"))
    integ = snap["integration"]["name"]
    # HEAD はブランチの名前を読む先（承認の記録の `source_tree`。D22）。
    _head(os.path.join(root, ".git"), integ)
    keep = tuple(p + "/" for p in place["integration_paths"])
    for path, text in _files(snap, integ).items():
        if path.startswith(keep) or path in place["integration_files"]:
            _write(root, path, text)
    for name in families:
        if name not in snap["branches"] or name == integ:
            continue
        if not ticket_mod.is_valid_id(name):
            raise Refused(f"家族のブランチ名が識別子の形でない: {name!r}")
        tree = os.path.join(root, *WORKTREES.split("/"), name)
        os.makedirs(tree, exist_ok=True)
        registry = os.path.join(root, ".git", "worktrees", name)
        os.makedirs(registry)
        gitfile = os.path.join(tree, ".git")
        with open(gitfile, "w", encoding="utf-8") as f:
            f.write(f"gitdir: {registry}\n")
        with open(os.path.join(registry, "gitdir"), "w", encoding="utf-8") as f:
            f.write(gitfile + "\n")
        _head(registry, name)
        for path, text in _files(snap, name).items():
            _write(tree, path, text)


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
    _build(root, snap, place, closure["families"])

    code, first, err = _preview(root, place["env"], [])
    if first is None:
        return {"family": family, "closure": closure, "refused": _refused(root, code, err)}
    mine = [e["ticket"] for e in first["batch"] if (e.get("parent") or e["ticket"]) == family]
    body = first
    if mine and len(mine) != len(first["batch"]):
        # 画面の本文と指紋を、この家族の分だけで組み直す（1 回の承認は 1 つの `P`。8.3）。
        code, body, err = _preview(root, place["env"], mine)
        if body is None:
            return {"family": family, "closure": closure, "refused": _refused(root, code, err)}
    batch = [_entry(root, e) for e in body["batch"] if e["ticket"] in mine]
    return {
        "family": family,
        "closure": closure,
        "batch": batch,
        # 画面の本文は提案をツリーからの相対パスで出す（D22）ので、手を加えずに返す。
        # 指紋はこの本文と写しの中身を覆い、手元の `--approve --preview` と同じ値になる。
        "text": body["text"] if batch else "",
        "digest": body["digest"] if batch else "",
        "rejected": [
            {"ticket": r["ticket"], "problems": [_relative(root, p) for p in r["problems"]]}
            for r in first["rejected"]
            if family_of(r["ticket"]) == family
        ],
        "problems": [_relative(root, p) for p in first["problems"]] + _binary(snap, closure),
    }


def _binary(snap: dict, closure: dict) -> list[str]:
    """本文を読めなかった（バイナリの）ファイル。判定がそれを読んだかは分からないので言う。"""
    names = [snap["integration"]["name"], *closure["families"]]
    return [
        f"{name}:{path} はバイナリで読めなかった"
        for name in dict.fromkeys(names)
        for path in (snap["branches"].get(name) or {}).get("binary") or []
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


def _relative(root: str, text: str) -> str:
    """仮のツリーの綴り（`<root>/.claude/worktrees/<P>/`）を `<P>:` に畳む。"""
    base = re.escape(os.path.join(root, *WORKTREES.split("/")) + os.sep)
    out = re.sub(base + r"([A-Za-z0-9][A-Za-z0-9._-]*)" + re.escape(os.sep), r"\1:", text)
    out = out.replace(root + os.sep, "")
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
    _build(root, snap, place, closure["families"])
    return snap, place, family, closure


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
            row = {"op": e["op"], "path": _relative(root, e["path"])}
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
    with _environ(place["env"]):
        snapshot = _core_snapshot(req, root)
        with history.session(snapshot.actor.via or history.VIA_CHROME, None):
            verdict = core.judge_approval(snapshot, only)
            refused = verdict.gathered.refused
            changes = core.plan(snapshot, verdict) if verdict.batch and not refused else None
    return {
        "family": family,
        "identifiers": verdict.identifiers,
        "text": verdict.screen_text,
        "digest": verdict.digest,
        "refused": _relative(root, refused),
        "rejected": [
            {"ticket": t.ticket, "problems": [_relative(root, str(p)) for p in complaints]}
            for t, complaints in verdict.rejected
        ],
        **_changes(root, changes),
    }


def _op_withdraw(req: dict, root: str) -> dict:
    """承認の取り下げで書くもの（8.8）。`prior` は識別子ごとの承認コミットの親の提案の本文。"""
    _, place, family, _ = _family_tree(req, root)
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
    return {
        "family": family,
        "problems": [_relative(root, p) for p in checked.problems],
        **_changes(root, checked.changes),
    }


def _op_confirm(req: dict, root: str) -> dict:
    """レビュー済みで書くもの（8.9）。`result` は `ccnavi-review.sh` が組むのと同じ形の写し。"""
    _, place, family, _ = _family_tree(req, root)
    phase_no = req.get("phase")
    if not isinstance(phase_no, int) or isinstance(phase_no, bool):
        raise Refused("phase はフェーズの番号")
    changed = req.get("changed") if isinstance(req.get("changed"), str) else ""
    result = review.Result.from_data(req.get("result"))
    if result.error:
        raise Refused(f"result を読めない: {result.error}")
    with _environ(place["env"]):
        snapshot = _core_snapshot(req, root)
        checked = core.confirm(snapshot, family, phase_no, result, changed)
    return {
        "family": family,
        "problems": [_relative(root, p) for p in checked.problems],
        **_changes(root, checked.changes),
    }


# ---- 互換の印（7.3） --------------------------------------------------------------------


def _op_compat(req: dict, root: str) -> dict:
    snap = _snapshot(req)
    text = _files(snap, snap["integration"]["name"]).get(COMPAT_FILE)
    ours = version.COMPAT
    found = lint._SH_COMPAT.search(text) if text else None
    theirs = int(found.group(1)) if found else None
    if theirs == ours:
        message = ""
    elif theirs is None:
        message = (
            f"統合先の {COMPAT_FILE} が互換の版（CCNAVI_COMPAT）を名乗らない。"
            f"拡張は互換 {ours}。表示だけにする"
        )
    else:
        hint = "拡張を更新する" if theirs > ours else "リポジトリの ccnavi の更新を待つ"
        message = f"拡張は互換 {ours}、リポジトリは互換 {theirs}。{hint}"
    return {
        "compat": {
            "extension": ours,
            "repository": theirs,
            "same": theirs == ours,
            "message": message,
        }
    }


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

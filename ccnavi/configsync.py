"""着手の前に、共通層の設定をプロジェクトの層へ写す（設計 §11.12、ADR-0084）。

共通層（`.ccnavi/common/`）は、共通の設定を各プロジェクトへ配るための定義で、正本は
各プロジェクトの `.ccnavi/config/`。共通層はワークスペースの git にあるので、プロジェクトだけを
clone した人からは見えない。そこで親チケットに着手するとき、共通層の各ファイルと、親の
ワークツリーにあるプロジェクトの層の同じ名前のファイルを比べ、違えば共通層で上書きする。

- 写すのは共通層にあるファイルだけ。共通層に無いファイル（このワークスペースでは
  `phases.yml`）は、プロジェクトの側を消さずに残す
- 配点の `script:` が指す共通層のスクリプトも写し、指す先をプロジェクトの層の綴りに直す。
  層から共通層のスクリプトは指せない（§11.4.2）ので、直さずに写すとプロジェクトの配点が壊れる
- 写す前に、プロジェクトの層として読めるかを確かめる。読めなければ何も写さず、着手しない
- 上書きで消える識別子と、中身の変わる識別子を名指しする
- 写す先に未コミットの変更があれば、人の書きかけを踏まないよう何も写さない
- 写したことは親の印 `config-sync.json` に残し、最初のレビューで知らせる。レビューが無いまま
  親を閉じようとしたら止め、人が端末で見たことを残すまで閉じさせない

写したファイルは、実行後の監視と控えと復元（どちらも `.ccnavi/` を守る）から見れば
エージェントの書き込みと区別が付かない。区別は内容で付ける（`is_synced_write`）。
誰が書いたかの台帳は持たない（ADR-0075 と同じ理由。印は git に乗って届く）。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import TextIO

import yaml

from . import approval, fsio, gitcmd, phasetypes, risk, rules, settings, tree

# 親ごとの印の名前。`phases/<親>/config-sync.json`。
MARK = "config-sync"
# 印の `files` で、配点が指すスクリプトを表す種類。
KIND_SCRIPT = "script"
# `is_synced_write` の `prior` を渡さなかった印。None は「その版に無い」の意味で使う。
UNSET = object()
# 端末で見たと残すときの `notified` の値。
NOTIFIED_TERMINAL = "terminal"


@dataclass
class Copied:
    """写す（写した）1 本。"""

    kind: str
    # 親のワークツリーからの相対。"/" 区切り。
    rel: str
    target: str
    content: bytes
    # 上書きする前の中身。無ければ None。戻すときに使う。
    before: bytes | None = None
    # 上書きで消える識別子と、同じ識別子のまま中身が変わるもの。
    lost: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    # 上書き前を識別子の並びとして読めなかった（消える識別子を名指しできない）。
    unparsed: bool = False

    @property
    def existed(self) -> bool:
        return self.before is not None

    @property
    def digest(self) -> str:
        return _digest(self.content)

    @property
    def before_digest(self) -> str | None:
        return _digest(self.before) if self.before is not None else None


def common_files(conf: settings.Settings) -> dict[str, str]:
    """共通層の 3 本のうち、置いてあるもの。kind → 絶対パス。"""
    found = {}
    for kind, path in (
        (settings.KIND_RULES, conf.rules),
        (settings.KIND_PHASES, conf.phases),
        (settings.KIND_RISK, conf.risk),
    ):
        if path and os.path.isfile(path):
            found[kind] = path
    return found


def projected(conf: settings.Settings, kind: str, content: bytes) -> bytes:
    """共通層の中身を、プロジェクトの層に置くときの形にする。

    配点だけ、`script:` の値の頭にある共通層の置き場を、プロジェクトの層の置き場へ直す。
    直すのは `script:` の値だけ。`glob` や `message` に同じ綴りがあっても触らない
    （「共通層のスクリプトの変更に点を付ける」項目が、意味ごと別の項目に変わるため）。
    """
    if kind != settings.KIND_RISK:
        return content
    common_home = re.escape(risk.SCRIPT_HOMES[0].encode())
    project_home = settings.layer_script_home(conf).encode()
    return re.sub(
        rb"(\bscript\s*:\s*[\"']?)" + common_home,
        lambda m: m.group(1) + project_home,
        content,
    )


def plan(conf: settings.Settings, root: str, tree_root: str) -> tuple[list[Copied], str]:
    """写すものの一覧。写せない理由があれば、それを返す（何も写さない）。"""
    out: list[Copied] = []
    scripts: list[tuple[str, str]] = []
    for kind, common in common_files(conf).items():
        raw, why = _read_strict(common)
        if why:
            return [], f"共通層の {os.path.basename(common)} を読めない ({why})"
        content = projected(conf, kind, raw or b"")
        why = _unreadable_as_layer(conf, kind, content)
        if why:
            name = os.path.basename(common)
            return [], f"共通層の {name} をプロジェクトの層として読めない: {why}"
        if kind == settings.KIND_RISK:
            scripts = _scripts_of(conf, root, raw or b"")
        copied, why = _compare(
            kind, tree_root, settings.layer_real_path(conf, tree_root, kind), content
        )
        if why:
            return [], why
        if copied is not None:
            out.append(copied)
    for source, rel in scripts:
        raw, why = _read_strict(source)
        if why or raw is None:
            return [], f"共通層の配点が指すスクリプト {source} を読めない ({why or '無い'})"
        target = os.path.join(tree_root, rel.replace("/", os.sep))
        copied, why = _compare(KIND_SCRIPT, tree_root, target, raw)
        if why:
            return [], why
        if copied is not None:
            out.append(copied)
    return out, ""


def dirty(tree_root: str, copied: list[Copied]) -> tuple[list[str], str]:
    """写す先のうち、未コミットの変更があるもの。git を読めなければ理由を返す。"""
    if not copied:
        return [], ""
    rc, out = gitcmd.output(
        tree_root,
        ["status", "--porcelain", "-z", "--", *[f":(literal){c.rel}" for c in copied]],
    )
    if rc != 0:
        return [], "git の状態を読めない"
    found = []
    for entry in out.split("\0"):
        if len(entry) > 3:
            found.append(entry[3:])
    return found, ""


def apply(approved_dir: str, parent: str, copied: list[Copied]) -> str:
    """印を置いてから上書きする。書けなかった理由を返す。書けたら空文字。

    先に印を置くのは、上書きのあとに着手が止まっても、上書きしたことが知らせに残るように
    するため。1 本でも書けなければ、書いた分と印を元に戻す。
    """
    path = approval.parent_mark_path(approved_dir, parent, MARK)
    previous = _read(path)
    mark = approval.read_parent_mark(approved_dir, parent, MARK) or {}
    entries = {str(f.get("path")): f for f in mark.get("files") or [] if isinstance(f, dict)}
    for c in copied:
        entries[c.rel] = {
            "kind": c.kind,
            "path": c.rel,
            "sha256": c.digest,
            "before_sha256": c.before_digest,
            "existed": c.existed,
            "lost": c.lost,
            "changed": c.changed,
            "unparsed": c.unparsed,
        }
    failed = approval.write_parent_mark(
        approved_dir,
        parent,
        MARK,
        {"files": list(entries.values()), "notified": "", "prepared": None},
    )
    if failed:
        return f"印を書けない: {failed}"
    written: list[Copied] = []
    for c in copied:
        failed = _replace(c.target, c.content)
        if failed:
            stuck = [w.rel for w in written if not _undo(w)]
            if not _restore(path, previous):
                stuck.append(path)
            if stuck:
                return f"{c.rel} を書けない: {failed}（戻せなかったもの: {', '.join(stuck)}）"
            return f"{c.rel} を書けない: {failed}（書いた分と印は戻した）"
        written.append(c)
    return ""


def is_synced_write(
    conf: settings.Settings,
    root: str,
    path: str,
    content: bytes | None = None,
    prior: bytes | None | object = UNSET,
) -> bool:
    """その変更が、親の着手で共通層を写したものだと読めるか。

    `content` は変更後の中身（渡さなければディスク上の今の中身）、`prior` は変更前として
    比べるコミット済みの中身（渡さなければそのワークツリーの HEAD。None はその版に無い）。
    コミットに入った分を見るときは、両方ともコミットされたもの（HEAD とターンの始まり）を
    渡すこと。ディスクで答えると、好きな中身でコミットしてからディスクだけ戻す形が外れる。

    読めるのは次が全部揃ったときだけ。

    1. 置き場が、プロジェクトから切った**承認済みの親チケットの**ワークツリー（名前が親の
       識別子で、そのチケットの `project:` がワークツリーのプロジェクトと同じ）の中。
       元リポジトリ、子のワークツリー、チケットの無いワークツリー、他のプロジェクトは外さない
    2. その綴りの途中にシンボリックリンクが無い。リンクで差し替えると、指す先の中身で答えてしまう
    3. その中身が、共通層の対応するもの（設定はプロジェクトの層の形に直したもの、
       スクリプトはそのまま）と同じ（改行の違いは見ない）
    4. **その親の**印 `config-sync.json` が、その相対パスとその中身の指紋を名指ししている
    5. 変更前のコミット済みの中身が、印に残した上書き前の中身と同じ。外すのは写してから
       コミットするまでの間だけで、人が直してコミットしたあとに共通層の中身へ戻す書き込みは外さない

    読めないものは外さない。
    """
    rel_home = settings.layer_script_home(conf)
    name = os.path.basename(path)
    parent_dir = os.path.basename(os.path.dirname(path))
    is_config = (
        parent_dir == settings.LAYER_CONFIG_DIR and name in settings.LAYER_FILE_NAMES.values()
    )
    if not is_config and f"/{rel_home}" not in path.replace(os.sep, "/"):
        return False
    where = tree.tree_of(root, _key(path), conf.projects)
    if where is None or not where.project or where.is_main:
        return False
    if _linked(where.root, path):
        return False
    if not _approved_parent(conf, root, where):
        return False
    rel = _rel(where.root, path)
    expected = _expected(conf, root, rel)
    if expected is None:
        return False
    now = content if content is not None else _read(path)
    if now is None or not _same(now, expected):
        return False
    home = approval.home_dir(conf, root, where.name, "")
    mark = approval.read_parent_mark(home, where.name, MARK) or {}
    entry = next(
        (
            f
            for f in mark.get("files") or []
            if isinstance(f, dict) and f.get("path") == rel and f.get("sha256") == _digest(now)
        ),
        None,
    )
    if entry is None:
        return False
    if prior is UNSET:
        prior, readable = gitcmd.blob(where.root, "HEAD", rel)
        if not readable:
            return False
    before = _digest(prior) if isinstance(prior, bytes) else None
    return entry.get("before_sha256") == before


def _approved_parent(conf: settings.Settings, root: str, where: tree.Tree) -> bool:
    """そのワークツリーが、同じプロジェクト向けの承認済みの親チケットのものか。"""
    copies, _ = approval.scan(conf, root)
    closed, _ = approval.scan(conf, root, closed=True)
    ticket = approval.by_id(copies + closed).get(where.name)
    return ticket is not None and not ticket.is_child and ticket.project == where.project


def pending(approved_dir: str, parent: str) -> dict | None:
    """まだ知らせていない印。無いか知らせ済みなら None。"""
    mark = approval.read_parent_mark(approved_dir, parent, MARK)
    if not mark or mark.get("notified") or not mark.get("files"):
        return None
    return mark


def notice(mark: dict) -> str:
    """最初のレビューの依頼の頭に置く文。"""
    lines = [
        "## プロジェクトの設定が変わった",
        "",
        "着手のときに、ワークスペースの共通層（`.ccnavi/common/`）とこのプロジェクトの "
        "`.ccnavi/` が違っていたので、共通層で上書きした。この変更もレビューの対象。",
        "",
    ]
    for f in mark.get("files") or []:
        state = "上書き" if f.get("existed") else "新しく置いた"
        line = f"- `{f.get('path')}`（{state}）"
        lost = [str(x) for x in f.get("lost") or []]
        changed = [str(x) for x in f.get("changed") or []]
        if lost:
            line += "。消えた識別子: " + ", ".join(f"`{x}`" for x in lost)
        if changed:
            line += "。中身が変わった識別子: " + ", ".join(f"`{x}`" for x in changed)
        if f.get("unparsed"):
            line += "。上書き前を読めなかったので、消えた識別子は名指しできていない"
        lines.append(line)
    lines += ["", ""]
    return "\n".join(lines)


def mark_prepared(approved_dir: str, parent: str, phase_no: int) -> str:
    """依頼の本文に知らせを載せた、と印に残す。`requested` はこれを見て知らせ済みにする。"""
    mark = approval.read_parent_mark(approved_dir, parent, MARK)
    if not mark:
        return ""
    mark["prepared"] = phase_no
    return approval.write_parent_mark(approved_dir, parent, MARK, mark)


def prepared_for(approved_dir: str, parent: str, phase_no: int) -> bool:
    """その番号の依頼の本文に、知らせを載せたか。"""
    mark = pending(approved_dir, parent)
    return mark is not None and mark.get("prepared") == phase_no


def mark_notified(approved_dir: str, parent: str, where: str) -> str:
    """知らせたことを印に残す。2 回目以降のレビューでは繰り返さない。"""
    mark = approval.read_parent_mark(approved_dir, parent, MARK)
    if not mark:
        return ""
    mark["notified"] = where
    return approval.write_parent_mark(approved_dir, parent, MARK, mark)


def acknowledge(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    parent: str,
) -> int:
    """レビューの無いまま閉じる親で、人が上書きを見たことを残す（`ccnavi --config-synced`）。"""
    home = approval.home_dir(conf, root, parent, "")
    mark = pending(home, parent)
    if mark is None:
        stdout.write(f"OK: {parent} に知らせていない設定の上書きは無い\n")
        return 0
    stdout.write(notice(mark))
    stdout.write("この上書きを見たものとして残してよいなら y、やめるならそれ以外: ")
    stdout.flush()
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 残さなかった\n")
        return 1
    failed = mark_notified(home, parent, NOTIFIED_TERMINAL)
    if failed:
        stderr.write(f"ccnavi: 印を書けない: {failed}\n")
        return 1
    stdout.write(f"OK: {parent} の設定の上書きを見たものとして残した\n")
    return 0


def _compare(kind: str, tree_root: str, target: str, content: bytes) -> tuple[Copied | None, str]:
    """写す先と比べる。同じなら None。写せない理由があればそれを返す。"""
    if not _inside(tree_root, target) or _linked(tree_root, target):
        return (
            None,
            f"{_rel_plain(tree_root, target)} がシンボリックリンクか、その下にある。写さない",
        )
    before, why = _read_strict(target)
    if why:
        return None, f"{_rel(tree_root, target)} を読めない ({why})"
    if before is not None and _same(before, content):
        return None, ""
    copied = Copied(
        kind=kind, rel=_rel(tree_root, target), target=target, content=content, before=before
    )
    if before is not None and kind != KIND_SCRIPT:
        old, new = _entries(kind, before), _entries(kind, content)
        if old is None or new is None:
            copied.unparsed = True
        else:
            copied.lost = [x for x in old if x not in new]
            copied.changed = [x for x in old if x in new and old[x] != new[x]]
    return copied, ""


def _scripts_of(conf: settings.Settings, root: str, raw: bytes) -> list[tuple[str, str]]:
    """共通層の配点が指すスクリプト。（共通層の実体, 写す先のツリーからの相対）。"""
    definition, _ = risk.parse(raw.decode("utf-8", errors="replace"), "(risk)")
    if definition is None:
        return []
    common_home = risk.SCRIPT_HOMES[0]
    found = []
    for f in definition.factors:
        value = str(f.value)
        if f.kind != risk.KIND_SCRIPT or not value.startswith(common_home):
            continue
        rel = settings.layer_script_home(conf) + value[len(common_home) :]
        pair = (os.path.join(root, value.replace("/", os.sep)), rel)
        if pair not in found:
            found.append(pair)
    return found


def _expected(conf: settings.Settings, root: str, rel: str) -> bytes | None:
    """ツリーからの相対 rel に写るはずの、共通層の中身。対応するものが無ければ None。"""
    for kind, common in common_files(conf).items():
        home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("\\", "/").strip("/")
        if rel == f"{home}/{settings.LAYER_CONFIG_DIR}/{settings.LAYER_FILE_NAMES[kind]}":
            raw = _read(common)
            return projected(conf, kind, raw) if raw is not None else None
    script_home = settings.layer_script_home(conf)
    if rel.startswith(script_home):
        source = os.path.join(root, (risk.SCRIPT_HOMES[0] + rel[len(script_home) :]))
        return _read(source.replace("/", os.sep))
    return None


def _unreadable_as_layer(conf: settings.Settings, kind: str, content: bytes) -> str:
    """プロジェクトの層として読めないなら、その理由。"""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "UTF-8 として読めない"
    if kind == settings.KIND_PHASES:
        types, problems = phasetypes.parse(text, "(phases)", refs=False)
        errors = [p for p in problems if p.severity == rules.SEVERITY_ERROR]
        if types is None or errors:
            return "; ".join(p.detail for p in errors) or "フェーズの種類として読めない"
        return ""
    if kind == settings.KIND_RISK:
        definition, problems = risk.parse(text, "(risk)", (settings.layer_script_home(conf),))
        errors = [p for p in problems if p.severity == rules.SEVERITY_ERROR]
        if definition is None or errors:
            return "; ".join(p.detail for p in errors) or "配点として読めない"
        return ""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return "YAML として読めない"
    if not isinstance(data, dict):
        return "キーと値の並びではない"
    _, problems = rules.parse(data)
    errors = [p for p in problems if p.severity == rules.SEVERITY_ERROR]
    return "; ".join(p.detail for p in errors)


def _entries(kind: str, content: bytes) -> dict[str, object] | None:
    """識別子ごとの定義。読めなければ None。"""
    try:
        data = yaml.safe_load(content.decode("utf-8"))
    except (ValueError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    if kind == settings.KIND_PHASES:
        phases = data.get("phases")
        return {str(k): v for k, v in phases.items()} if isinstance(phases, dict) else {}
    if kind == settings.KIND_RISK:
        items = [("factors", e) for e in data.get("factors") or []]
    else:
        items = [
            (section, e)
            for section in ("deny", "ask", "allow")
            if isinstance(data.get(section), list)
            for e in data.get(section)
        ]
    # ルールは、同じ id のまま deny から allow へ移るのも「中身が変わった」に数える。
    return {
        str(e["id"]): (section, e) for section, e in items if isinstance(e, dict) and e.get("id")
    }


def _linked(tree_root: str, path: str) -> bool:
    """tree_root から path までの途中（path 自身を含む）に、シンボリックリンクがあるか。

    綴りのままさかのぼり、実体が tree_root に着いたところで止める。tree_root より上の
    リンク（macOS の `/tmp` など）は数えない。着けなければ（外を指している）リンクと同じに扱う。
    """
    base = _key(tree_root)
    current = os.path.abspath(path)
    while _key(current) != base:
        if os.path.islink(current):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return True
        current = parent
    return False


def _rel_plain(base: str, path: str) -> str:
    """リンクを解かない相対。人に見せる綴り。"""
    return os.path.relpath(os.path.abspath(path), os.path.abspath(base)).replace(os.sep, "/")


def _inside(tree_root: str, target: str) -> bool:
    base = _key(tree_root)
    real = _key(target)
    return real == base or real.startswith(base + os.sep)


def _key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _same(a: bytes, b: bytes) -> bool:
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def _rel(base: str, path: str) -> str:
    return os.path.relpath(os.path.realpath(path), os.path.realpath(base)).replace(os.sep, "/")


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _read_strict(path: str) -> tuple[bytes | None, str]:
    """無ければ (None, "")。在るのに読めなければ (None, 理由)。"""
    try:
        with open(path, "rb") as f:
            return f.read(), ""
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, str(exc)


def _replace(path: str, content: bytes) -> str:
    """一時ファイルに書いてから置き換える。途中で止まっても半端な中身を残さない。"""
    temp = path + ".ccnavi-sync"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temp, "wb") as f:
            f.write(content)
        os.replace(temp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(temp)
        return str(exc)
    return ""


def _undo(c: Copied) -> bool:
    """写した 1 本を元へ戻す。戻せたか。"""
    if c.before is None:
        with contextlib.suppress(FileNotFoundError):
            try:
                os.remove(c.target)
            except OSError:
                return False
        return True
    return not _replace(c.target, c.before)


def _restore(path: str, previous: bytes | None) -> bool:
    """印を前の中身へ戻す。戻せたか。"""
    if previous is None:
        with contextlib.suppress(FileNotFoundError):
            try:
                os.remove(path)
            except OSError:
                return False
        return True
    return not _replace(path, previous)

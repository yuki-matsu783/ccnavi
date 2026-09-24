"""着手の前に、共通層の設定をプロジェクトの層へ写す（設計 §11.12、ADR-0084）。

共通層（`.ccnavi/common/`）は、共通の設定を各プロジェクトへ配るための定義で、正本は
各プロジェクトの `.ccnavi/config/`。共通層はワークスペースの git にあるので、プロジェクトだけを
clone した人からは見えない。そこで親チケットに着手するとき、共通層の各ファイルと、親の
ワークツリーにあるプロジェクトの層の同じ名前のファイルを比べ、違えば共通層で上書きする。

- 写すのは共通層にあるファイルだけ。共通層に無いファイル（このワークスペースでは
  `phases.yml`）は、プロジェクトの側を消さずに残す
- 上書きで消える識別子（ルールの id、フェーズの種類、配点の項目）は、写す前に名指しする
- 写したことは親の印 `config-sync.json` に残し、最初のレビューの依頼の頭に載せる

写したファイルは、実行後の監視と控えと復元（どちらも `.ccnavi/` を守る）から見れば
エージェントの書き込みと区別が付かない。区別は内容で付ける（`is_synced_write`）。
「共通層と同じ中身」かつ「印がその中身の指紋を名指ししている」ものだけを外す。
誰が書いたかの台帳は持たない（ADR-0075 と同じ理由。印は git に乗って届く）。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

import yaml

from . import approval, settings, tree

# 親ごとの印の名前。`phases/<親>/config-sync.json`。
MARK = "config-sync"


@dataclass
class Copied:
    """写す（写した）1 本。"""

    kind: str
    # 親のワークツリーからの相対。"/" 区切り。
    rel: str
    target: str
    common: str
    content: bytes
    # 上書きする前にプロジェクトの側にあったか。
    existed: bool
    # 上書きで消える識別子。
    lost: list[str] = field(default_factory=list)

    @property
    def digest(self) -> str:
        return _digest(self.content)


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


def plan(conf: settings.Settings, tree_root: str) -> tuple[list[Copied], str]:
    """写すものの一覧。読めない共通層があれば、その理由を返す（何も写さない）。"""
    out: list[Copied] = []
    for kind, common in common_files(conf).items():
        content = _read(common)
        if content is None:
            return [], f"共通層の {os.path.basename(common)} を読めない"
        target = settings.layer_real_path(conf, tree_root, kind)
        before = _read(target)
        if before is not None and _same(before, content):
            continue
        out.append(
            Copied(
                kind=kind,
                rel=_rel(tree_root, target),
                target=target,
                common=common,
                content=content,
                existed=before is not None,
                lost=_lost_ids(kind, before, content),
            )
        )
    return out, ""


def apply(
    conf: settings.Settings, tree_root: str, approved_dir: str, parent: str, copied: list[Copied]
) -> str:
    """上書きして印を置く。書けなかった理由を返す。書けたら空文字。"""
    for c in copied:
        failed = _write(c.target, c.content)
        if failed:
            return f"{c.rel} を書けない: {failed}"
    return approval.write_parent_mark(
        approved_dir,
        parent,
        MARK,
        {
            "files": [
                {
                    "kind": c.kind,
                    "path": c.rel,
                    "sha256": c.digest,
                    "existed": c.existed,
                    "lost": c.lost,
                }
                for c in copied
            ],
            "notified": "",
        },
    )


def is_synced_write(conf: settings.Settings, root: str, path: str) -> bool:
    """その変更が、着手のときに共通層を写した書き込みだと内容から読めるか。

    見るのはプロジェクトから切ったワークツリーの中だけ。読めるのは 2 つが揃ったときだけ。

    1. いまの中身が、共通層の同じ種類のファイルと同じ（改行の違いは見ない）
    2. どこかのツリーの印 `config-sync.json` が、その相対パスとその中身の指紋を名指ししている

    1 だけだと、エージェントが共通層の中身をシェルで写しても外れ、最初のレビューで
    知らせる印が残らない。2 だけだと、印を置いてから中身を書き換える形が外れる。
    読めないものは外さない。
    """
    # 呼ばれるのは変更 1 件ごと。設定の綴りでないものは、ツリーを引く前に落とす。
    if os.path.basename(path) not in settings.LAYER_FILE_NAMES.values():
        return False
    if os.path.basename(os.path.dirname(path)) != settings.LAYER_CONFIG_DIR:
        return False
    full = _key(path)
    where = tree.tree_of(root, full, conf.projects)
    # 外すのはワークツリーの中だけ。上書きするのは親のワークツリーで、判定が読むのは
    # 元リポジトリに checkout されている版（設計 §11.2）。元リポジトリの設定を外すと、
    # 共通層の中身と印をシェルで置くだけで、判定が読む設定の書き換えが報告から消える。
    if where is None or not where.project or where.is_main:
        return False
    for kind, common in common_files(conf).items():
        if _key(settings.layer_real_path(conf, where.root, kind)) != full:
            continue
        now = _read(full)
        wanted = _read(common)
        if now is None or wanted is None or not _same(now, wanted):
            return False
        return _named_by_a_mark(conf, root, _rel(where.root, full), _digest(now))
    return False


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
        "`.ccnavi/config/` が違っていたので、共通層で上書きした。この変更もレビューの対象。",
        "",
    ]
    for f in mark.get("files") or []:
        state = "上書き" if f.get("existed") else "新しく置いた"
        line = f"- `{f.get('path')}`（{state}）"
        lost = [str(x) for x in f.get("lost") or []]
        if lost:
            line += "。消えた識別子: " + ", ".join(f"`{x}`" for x in lost)
        lines.append(line)
    lines += ["", ""]
    return "\n".join(lines)


def mark_notified(approved_dir: str, parent: str, where: str) -> str:
    """知らせたことを印に残す。2 回目以降のレビューでは繰り返さない。"""
    mark = approval.read_parent_mark(approved_dir, parent, MARK)
    if not mark:
        return ""
    mark["notified"] = where
    return approval.write_parent_mark(approved_dir, parent, MARK, mark)


def _named_by_a_mark(conf: settings.Settings, root: str, rel: str, digest: str) -> bool:
    for t in approval.trees(conf, root):
        base = os.path.join(settings.approved_dir(conf, t.root), approval.PHASES_DIR)
        try:
            parents = os.listdir(base)
        except OSError:
            continue
        for parent in parents:
            mark = approval.read_parent_mark(settings.approved_dir(conf, t.root), parent, MARK)
            for f in (mark or {}).get("files") or []:
                if f.get("path") == rel and f.get("sha256") == digest:
                    return True
    return False


def _lost_ids(kind: str, before: bytes | None, after: bytes) -> list[str]:
    """上書きで消える識別子。読めない側があれば空（名指しできない）。"""
    if before is None:
        return []
    old, new = _ids(kind, before), _ids(kind, after)
    if old is None or new is None:
        return []
    return [x for x in old if x not in new]


def _ids(kind: str, content: bytes) -> list[str] | None:
    try:
        data = yaml.safe_load(content.decode("utf-8"))
    except (ValueError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    if kind == settings.KIND_PHASES:
        phases = data.get("phases")
        return [str(k) for k in phases] if isinstance(phases, dict) else []
    if kind == settings.KIND_RISK:
        entries = data.get("factors")
    else:
        entries = [
            e
            for section in ("deny", "ask", "allow")
            for e in (data.get(section) or [])
            if isinstance(data.get(section), list)
        ]
    if not isinstance(entries, list):
        return []
    return [str(e["id"]) for e in entries if isinstance(e, dict) and e.get("id")]


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


def _write(path: str, content: bytes) -> str:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
    except OSError as exc:
        return str(exc)
    return ""

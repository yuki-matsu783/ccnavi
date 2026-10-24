"""共通レイヤーのミラーが、ワークスペースの共通レイヤーとずれていないかを調べ、ずれを同期する。

`ccnavi user mirror` の中身。親チケットの着手がミラーを揃えるのは、そのプロジェクトの親チケットに
着手するときだけなので、間が空くとミラー（プロジェクトを単体で clone した人が共通レイヤーとして
読むもの）が古いまま残る。ここでは、プロジェクトのメインのチェックアウト（`projects/<名前>/`）の
ミラーを、ユーザが見たいときに調べ、押したときに揃える。

- 調べる（`prepare`）: 何も書かない。プロジェクトごとに、ワークスペースの共通レイヤーとの差分を
  `configsync.plan` で求める。差分が無ければ `synced`、あれば `differs`、調べられなければ `error`
- 揃える（`apply`）: 見せた差分と同じ内容のときだけ書く。`expect`（差分の内容から決めた値）が
  今の差分と一致しなければ、何も書かない。書くのはワークツリーだけで、コミットはしない
- ワークスペースの共通レイヤーが無い、または空なら、コピーするものが無い（`common_empty`）。
  ミラーは消さない（共通を消し忘れたときに、ミラーを全部失わないため）

書き込みの中身は `configsync`。cli を読まない。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from ..infra import settings, tree
from . import configsync

JSON_VERSION = 1

STATE_SYNCED = "synced"
STATE_DIFFERS = "differs"
STATE_ERROR = "error"

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"

# 見せた内容と、書く直前の内容がずれたときの文面。拡張は、この語で読み直しを判断する。
STALE = "内容が更新されました。最新の差分を読み直してから、もう一度実行してください"


def action_of(change: configsync.Change) -> str:
    """変更の種類。消すなら delete、元が無ければ create、あれば update。"""
    if change.deletes:
        return ACTION_DELETE
    return ACTION_CREATE if change.before is None else ACTION_UPDATE


@dataclass
class Project:
    """プロジェクト 1 つのミラーの状態。"""

    name: str
    root: str
    state: str = STATE_SYNCED
    changes: list[configsync.Change] = field(default_factory=list)
    error: str = ""
    # 選ばれたか（名前を渡したときは、渡したものだけ）。
    selected: bool = True
    # ミラーの置き場（絶対）。中身の差を人が見るときの案内に使う。
    mirror_path: str = ""

    @property
    def expect(self) -> str:
        """このプロジェクトだけを揃えるときの照合の値。差分が無ければ空。"""
        return expect_of([self]) if self.state == STATE_DIFFERS else ""


@dataclass
class Plan:
    projects: list[Project] = field(default_factory=list)
    # 全体を止める誤り（共通レイヤーが読めない、名前がプロジェクトに無いなど）。
    error: str = ""
    # 共通レイヤーにコピーするものが無い（正常。揃えても何も起きない）。
    common_empty: bool = False
    # ワークスペースの共通レイヤーの置き場（絶対）。
    common_path: str = ""

    @property
    def chosen(self) -> list[Project]:
        """揃える対象。選ばれた、差分のあるプロジェクト。"""
        return [p for p in self.projects if p.selected and p.state == STATE_DIFFERS]

    @property
    def expect(self) -> str:
        """対象をまとめて揃えるときの照合の値。対象が無ければ空。"""
        chosen = self.chosen
        return expect_of(chosen) if chosen else ""


def _framed(value: str) -> bytes:
    raw = value.encode("utf-8", errors="surrogatepass")
    return len(raw).to_bytes(4, "big") + raw


def expect_of(projects: list[Project]) -> str:
    """揃える対象の照合の値。名前順に、名前と変更（パス・種類・中身）を連結した SHA-256。

    見せた差分と、書く直前の差分が同じときだけ同じ値になる。中身まで入れるのは、ファイル名が
    同じでも共通レイヤーの中身が変わったときに、見せていない内容を書かないため。
    """
    digest = hashlib.sha256()
    for project in sorted(projects, key=lambda p: p.name):
        digest.update(_framed(project.name))
        for change in sorted(project.changes, key=lambda c: c.rel):
            digest.update(_framed(change.rel))
            digest.update(_framed(action_of(change)))
            digest.update(hashlib.sha256(change.content or b"").digest())
    return digest.hexdigest()


def prepare(conf: settings.Settings, root: str, wanted: list[str] | None = None) -> Plan:
    """ミラーの状態を調べる。何も書かない。

    処理フロー:
    1. 共通レイヤーが読めるかを `configsync.sources` で確かめる。読めなければ全体の誤り
    2. コピーするものが無ければ `common_empty` として、全プロジェクトを `synced` にする
    3. プロジェクトごとに `configsync.plan` で差分を求める。求められなければ、そのプロジェクトだけ
       `error` にする
    4. 名前を渡されたときは、渡されたプロジェクトだけを選ぶ。無い名前があれば全体の誤り
    """
    plan = Plan(common_path=configsync.common_dir(conf, root))
    found = tree.projects(conf.projects)
    # 名前は大文字小文字を区別せずに比べる（Windows と macOS では同じディレクトリのため）。
    names = {t.name.casefold() for t in found}
    if wanted:
        missing = [n for n in wanted if n.casefold() not in names]
        if missing:
            plan.error = f"プロジェクトがありません: {', '.join(missing)}"
            return plan
    wanted_set = {n.casefold() for n in (wanted or names)}
    wanted_files, why = configsync.sources(conf, root)
    if why:
        plan.error = why
        return plan
    plan.common_empty = not wanted_files
    for entry in found:
        project = Project(
            entry.name,
            entry.root,
            selected=entry.name.casefold() in wanted_set,
            mirror_path=configsync.mirror_dir(conf, entry.root),
        )
        plan.projects.append(project)
        if plan.common_empty:
            continue
        changes, problem = configsync.plan(conf, root, entry.root)
        if problem:
            project.state = STATE_ERROR
            project.error = problem
        elif changes:
            project.state = STATE_DIFFERS
            project.changes = changes
    return plan


@dataclass
class Applied:
    ok: bool = True
    error: str = ""
    synced: list[tuple[str, list[str]]] = field(default_factory=list)


def apply(conf: settings.Settings, plan: Plan, expect: str) -> Applied:
    """対象のミラーを揃える。`expect` が今の差分と一致するときだけ書く。

    処理フロー:
    1. 全体の誤りがあれば、何も書かずに返す
    2. 対象が無ければ、何も書かずに返す（空の `expect` を通すと、見せていない内容を書くため）
    3. `expect` が今の差分の値と違えば、`STALE` で何も書かずに返す
    4. プロジェクトごとに `configsync.apply` で書く。1 つが失敗しても、ほかのプロジェクトは書く。
       書けなかった理由は、プロジェクトの名前を添えてまとめて返す
    """
    if plan.error:
        return Applied(False, plan.error)
    chosen = plan.chosen
    if not chosen:
        return Applied(False, "揃えるミラーはありません。何も書いていません")
    if not expect or expect != plan.expect:
        return Applied(False, STALE)
    done = Applied()
    failures: list[str] = []
    for project in chosen:
        failed = configsync.apply(project.changes, configsync.mirror_dir(conf, project.root))
        if failed:
            failures.append(f"{project.name}: {failed}")
            continue
        done.synced.append((project.name, [c.rel for c in project.changes]))
    if failures:
        done.ok = False
        done.error = "; ".join(failures)
    return done


def project_json(project: Project) -> dict:
    return {
        "name": project.name,
        "state": project.state,
        "changes": [{"rel": c.rel, "action": action_of(c)} for c in project.changes],
        "error": project.error,
        "expect": project.expect,
    }


def plan_json(plan: Plan) -> dict:
    """見るだけの結果の JSON。載せるのは選ばれたプロジェクトだけ（名前を渡さなければ全部）。"""
    if plan.error:
        return {
            "version": JSON_VERSION,
            "ok": False,
            "error": plan.error,
            "common_empty": False,
            "projects": [],
            "expect": "",
        }
    return {
        "version": JSON_VERSION,
        "ok": True,
        "error": "",
        "common_empty": plan.common_empty,
        "projects": [project_json(p) for p in plan.projects if p.selected],
        "expect": plan.expect,
    }


def applied_json(done: Applied) -> dict:
    return {
        "version": JSON_VERSION,
        "ok": done.ok,
        "error": done.error,
        "synced": [{"name": name, "files": files} for name, files in done.synced],
    }


def relative(root: str, path: str) -> str:
    """表示用のパス（ワークスペースルートからの相対）。"""
    return os.path.relpath(path, root).replace(os.sep, "/")

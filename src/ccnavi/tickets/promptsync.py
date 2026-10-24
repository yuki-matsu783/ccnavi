"""ユーザの発言のたびに、共通レイヤーと工程スキルをプロジェクトへコピーする。

コピー先は、`project:` を持つ着手済みの親チケットのワークツリー。
プロジェクトのメインのチェックアウトは対象外で、
ユーザが押す `ccnavi user mirror`（`mirrorsync`）が受け持つ。コピーするものとコピー方は
`configsync`（共通レイヤーのミラー `.ccnavi/config/common/`）と `skillsync`（工程スキルの
`skills/<工程>/`）が決める。ここは、どのワークツリーに、いつコピーするかだけを持つ。

コピーするのは UserPromptSubmit だけで、ツール呼び出しの中ではコピーしない。呼び出し側は、ここを
`selfguard.accept` と `post.at_prompt` の前に呼ぶ。コピーした分は「前のターンの終わりからここまでの
変更」に入り、ユーザの手による変更と同じに受け入れられる。ツール呼び出しの中でコピーすると、
実行後チェックとバックアップと復元から見てエージェントの書き込みと区別が付かず、
中身で見分ける仕掛けが要った。

発言のたびに走るので、差分が無いときは何も読まず何も書かない。コピーする元とコピー先のファイルの
名前・大きさ・更新時刻から指紋を作り（`_signature`）、前に揃えたときの指紋と同じなら、そこで
抜ける。指紋の確認は `stat` だけで済む。違えば、検証つきの全体の比較（`configsync.plan`・
`skillsync.plan`）を行い、差分があるぶんだけ書く。コピーできなかったときは指紋を残さないので、
直るまで毎回報告する。全部を一律に書き直すと、読み込みの検証と全ファイルの書き込みが毎回要り、
更新時刻も動くので採らない。指紋の置き場は state の置き場の `mirror-<親の識別子>.json`
（セッションをまたいで共有してよい。同じ内容を書くだけ）。
"""

from __future__ import annotations

import hashlib
import os

from ..infra import fsio, settings, tree
from . import approval, configsync, skillsync

SIGNATURE_PREFIX = "mirror-"


def sync(conf: settings.Settings, root: str) -> list[str]:
    """コピーした結果の行を返す。コピーするものが無い、揃っているときは空。

    親チケットごとに共通レイヤーのミラー、工程スキルの順にコピーする。コピーできない理由（読めない、
    リンクなど）はその行を返し、ほかの親と、もう一方のコピーは続ける。止めても発言は
    進めるので、呼び手は行をそのままモデルに渡せばよい。
    """
    if not conf.tickets_enabled:
        return []
    tickets, _ = approval.scan(conf, root)
    lines: list[str] = []
    seen: set[str] = set()
    for found in tickets:
        if found.is_child or not found.project or not found.started_at or found.ticket in seen:
            continue
        seen.add(found.ticket)
        worktree = tree.worktree_path(root, found.ticket)
        # 承認済みチケットの `project` が指すリポジトリのワークツリーであること。
        if not tree.is_worktree_of(tree.project_root(conf.projects, found.project), worktree):
            continue
        lines += _sync_one(conf, root, found.ticket, found.project, worktree)
    return lines


def _signature_path(conf: settings.Settings, ticket: str) -> str:
    return os.path.join(conf.state, f"{SIGNATURE_PREFIX}{fsio.safe_name(ticket)}.json")


def _sources(conf: settings.Settings, root: str) -> list[str]:
    """指紋に入れる、コピーする元の置き場（共通レイヤー・工程スキル）。"""
    return [configsync.common_dir(conf, root), os.path.join(root, *skillsync.SOURCE_DIR.split("/"))]


def _targets(conf: settings.Settings, worktree: str) -> list[str]:
    """指紋に入れる、コピー先の置き場（ミラー・skills）。"""
    return [
        configsync.mirror_dir(conf, worktree),
        os.path.join(worktree, *skillsync.SKILLS_DIR.split("/")),
    ]


def _signature(places: list[str]) -> str:
    """置き場の下のファイルの名前・種類・大きさ・時刻・inode の指紋。`stat` だけで作る。

    中身は読まない。更新時刻を元に戻して同じ大きさで書き換えても、変更時刻（ctime）は
    利用者側から戻せないので、指紋は変わる。リンクはたどらず、リンクであること自体を指紋に入れる
    （リンクに差し替わったら変わる）。置き場が無いときも指紋は決まる（無いこと自体が指紋）。
    """
    digest = hashlib.sha256()
    for place in places:
        digest.update(b"\0place\0" + place.encode("utf-8", errors="surrogatepass"))
        for parent, dirs, names in os.walk(place):
            dirs.sort()
            for name in sorted(names) + dirs:
                path = os.path.join(parent, name)
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                kind = "l" if os.path.islink(path) else "d" if name in dirs else "f"
                row = (
                    f"{os.path.relpath(path, place)}\0{kind}\0{info.st_size}\0{info.st_mtime_ns}"
                    f"\0{info.st_ino}\0{info.st_ctime_ns}"
                )
                digest.update(row.encode("utf-8", errors="surrogatepass") + b"\n")
    return digest.hexdigest()


def _sync_one(
    conf: settings.Settings, root: str, ticket: str, project: str, worktree: str
) -> list[str]:
    """1 つの親のワークツリーにコピーする。

    処理フロー:
    1. 指紋が前に揃えたときと同じなら、何もせず抜ける（`_signature`）
    2. 共通レイヤーのミラーの差分を `configsync.plan` で求め、あれば `apply` で書く
    3. 工程スキルのコピーも同じに `skillsync.plan` と `apply` で書く
    4. どちらもコピーできないことが無かったときで、
       比べている間にコピーする元が動いていないときだけ、
       書いた後の指紋を残す（元が動いていたら残さず、次の発言で比べ直す）
    """
    lines: list[str] = []
    committable: list[str] = []
    failed_any = False
    sources = _sources(conf, root)
    places = [*sources, *_targets(conf, worktree)]
    sig_path = _signature_path(conf, ticket) if conf.state else ""
    before = _signature(places)
    sources_before = _signature(sources)
    if sig_path:
        saved, failed = fsio.read_json(sig_path)
        if failed is None and isinstance(saved, dict) and saved.get("signature") == before:
            return []

    changes, why = configsync.plan(conf, root, worktree)
    if why:
        failed_any = True
        lines.append(f"ccnavi: {ticket} の共通レイヤーをミラーできない: {why}")
    elif changes:
        failed = configsync.apply(changes, configsync.mirror_dir(conf, worktree))
        if failed:
            failed_any = True
            lines.append(f"ccnavi: {ticket} の共通レイヤーをミラーできない: {failed}")
        else:
            lines.append(
                f"共通レイヤーとプロジェクト {project} のミラー（.ccnavi/config/common/）が"
                "違っていたので、共通レイヤーの中身に揃えた。変えたのは次のファイル。"
            )
            lines += _describe(changes)
            committable += [c.rel for c in changes]

    changes, why = skillsync.plan(root, worktree)
    if why:
        failed_any = True
        lines.append(f"ccnavi: {ticket} の工程スキルをコピーできない: {why}")
    elif changes:
        failed = skillsync.apply(changes)
        if failed:
            failed_any = True
            lines.append(f"ccnavi: {ticket} の工程スキルをコピーできない: {failed}")
        else:
            lines += skillsync.describe(project, changes)
            committable += [c.rel for c in changes]

    if sig_path and not failed_any and _signature(sources) == sources_before:
        # 書いたあとの指紋を残す。次の発言は、元も先も動いていなければ `stat` だけで抜ける。
        # 比べている間に元が動いていたら残さない（動いたあとの元を「揃った」と記録しないため）。
        fsio.write_json_atomic(sig_path, {"signature": _signature(places)})
    if committable:
        git_sh = settings.script_command(root, "ccnavi-git.sh")
        lines.append(
            f"  作業を始める前に、{worktree} で {', '.join(committable)} を"
            f" '{git_sh} add' してコミットしてください"
        )
    return lines


def _describe(changes: list[configsync.Change]) -> list[str]:
    lines = []
    for c in changes:
        state = "削除した" if c.deletes else ("上書き" if c.before is not None else "新しく置いた")
        lines.append(f"  - {c.rel}（{state}）")
    return lines

"""`--lint` のうち、設定のレイヤーをまたぐ検査。

ワークツリーの ccnavi ディレクトリに元リポジトリに無いファイルが
無いか、取得済みの `origin/<統合先>` のレイヤーが手元のレイヤーと食い違っていないかを見る。
1 つのレイヤーの 1 本ずつの検査（ルールの読み・phases / risk の合成）は
`tickets.config.check` にあり、保護対象の下書きの取り込み（`user handoff`）も同じものを呼ぶ。
"""

from __future__ import annotations

import os

from ..infra import fsio, gitcmd, pathcase, settings, tree
from ..policy import ruleload
from ..policy.rules import SEVERITY_INFO, SEVERITY_WARN, Problem
from ..tickets import ops_stop_integrate
from ..tickets.approval import store as approval
from ..tickets.config import check as config_check
from ..tickets.model import archive, standing, ticket_model
from ..tickets.model import ticket as ticket_mod

# 苦情の出どころの表記。VS Code 拡張がこの前置きでプロジェクトを引く。
layer_where = config_check.layer_where


def _worktree_layers(conf: settings.Settings, root: str) -> list[Problem]:
    """ワークツリーの ccnavi ディレクトリに、元リポジトリに無いファイルがあるか。

    判定が読むのは元リポジトリに checkout されている版だけ。
    ワークツリーの `.ccnavi/` に足したファイルは、そのブランチが統合されるまで使われない。
    使われないものを書いたユーザは、書いたとおりに使われていると思ったまま進む。統合の前に
    気づけるように、ここで名前を挙げる。

    足したファイルを問題にしているのではない。設定を書き進める場所はワークツリーでよく、
    そこから統合するのも普通の手順。言うのは「今はまだ使われていない」という 1 点だけ。

    同じパスのファイルが両方に在れば、それは編集で、git の
    差分が拾う。ここが拾うのは、元リポジトリに無くて差分にも出ない新しいパスのほう。

    承認済みの領域（承認済みチケット・マーカー・子の記録・フロー）は数えない（ユーザの決定）。
    承認済みチケットは親のワークツリーに置かれ、判定もフローの案内もそのツリーの版を読む。
    「統合されるまで使われない」は該当せず、言えば誤った案内になる。
    """
    problems: list[Problem] = []
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("/", os.sep)
    for work in tree.worktrees(root, conf.projects):
        origin = tree.project_root(conf.projects, work.project) if work.project else root
        approved = pathcase.fold(os.path.normpath(settings.approved_dir(conf, work.root)))
        for rel in _files_under(os.path.join(work.root, home)):
            if os.path.exists(os.path.join(origin, home, rel.replace("/", os.sep))):
                continue
            if work.project and rel.startswith(f"{settings.COMMON_DIR}/"):
                # プロジェクトのワークツリーの `.ccnavi/config/common/` は、
                # 発言のときの同期がミラーした共通レイヤー。
                # ワークスペースの中では読まれないので、言わない。
                continue
            where = pathcase.fold(
                os.path.normpath(os.path.join(work.root, home, rel.replace("/", os.sep)))
            )
            if where.startswith(approved + os.sep):
                continue
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    f"({tree.WORKTREES_DIR.replace(os.sep, '/')}/{work.name})",
                    f"{conf.project_home}/{rel} はワークツリーにしかない。判定が読むのは"
                    "元リポジトリの版なので、このファイルは統合されるまで"
                    "有効にならない",
                )
            )
    return problems


def _sync(conf: settings.Settings, root: str) -> list[Problem]:
    """親のワークツリーの置き場の検査と、レイヤーのずれ（`--lint` 用）。

    - 親のワークツリー（名前が親の識別子）なのに HEAD が別のブランチ: warn（移行の検査）
    - 手元のレイヤーが `origin/<統合先>` のレイヤーと違う: warn（`layer_drift`）。
      `origin/<統合先>` が手元に無いリポジトリは、比べられなかったことを info 1 行で言う
    """
    problems = _parent_trees_off_branch(conf, root)
    unchecked: list[str] = []
    for project in _repos(conf):
        found, checked = _drift(conf, root, project)
        problems.extend(found)
        if not checked:
            unchecked.append(tree.repo_key(project))
    if unchecked:
        problems.append(
            Problem(
                SEVERITY_INFO,
                "(layers)",
                "統合先のブランチ（origin/<統合先>）が手元に無く、レイヤーのずれを確かめていない"
                f"（{', '.join(unchecked)}）。取得すると確かめる",
            )
        )
    return problems


def _parent_trees_off_branch(conf: settings.Settings, root: str) -> list[Problem]:
    """名前が親の識別子なのに、HEAD のブランチ名を変換した識別子が名前と合わない親のワークツリー。

    ブランチ名の `/` を `-` に置き換えたものが、親のワークツリーのフォルダ名になる。"""
    problems: list[Problem] = []
    for work in tree.worktrees(root, conf.projects):
        if not _holds_parent(conf, work, root):
            continue
        branch = tree.branch_of(work.root)
        if branch and standing.head_branch(work.root, work.name):
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                f"({tree.WORKTREES_DIR.replace(os.sep, '/')}/{work.name})",
                f"親 {work.name} のワークツリーが {branch or '（ブランチの外）'} の上にある。"
                f"親のブランチ名の `/` を `-` に置き換えたものがフォルダ名（{work.name}）になる。"
                "取り込み（ccnavi-sync.sh）と正とする側の検査は、そのブランチだけを見る。"
                "親が閉じるのを待ってから、ブランチを切り替えてください",
            )
        )
    return problems


def _holds_parent(conf: settings.Settings, work: tree.Tree, root: str = "") -> bool:
    """そのツリーに `ticket: <ツリーの名前>` の親の承認済みチケットか提案があるか。

    sh の `ccnavi_parent_tree` と同じ見方。ready の後はツリーから親が消えて手元の退避
    （`logs/archive/<リポジトリ>/done/`）へ移るので、そこに親が在っても親のワークツリーとして扱う。
    """
    approved = settings.approved_dir(conf, work.root)
    proposals = os.path.join(work.root, conf.tickets.replace("/", os.sep))
    for path in (
        approval.copy_path(approved, work.name),
        approval.closed_path(approved, work.name),
        os.path.join(proposals, ticket_model.TODO, f"{work.name}.md"),
        os.path.join(proposals, ticket_model.REVIEW, f"{work.name}.md"),
    ):
        if not os.path.isfile(path):
            continue
        t, _ = ticket_mod.load(path)
        if t is not None and t.ticket == work.name and not t.parent:
            return True
    held = archive.archived_fields(root, work.project, work.name) if root else None
    return held is not None and held.ticket == work.name and not held.parent


def _layer_files(conf: settings.Settings, home_rel: str) -> list[tuple[str, str]]:
    """比べるレイヤーのファイル（種類, ツリーからの相対 "/" 区切り）。
    共通レイヤーと自身のレイヤー。"""
    config = f"{home_rel}/{settings.LAYER_CONFIG_DIR}"
    out = []
    for kind in settings.LAYER_KINDS:
        name = settings.LAYER_FILE_NAMES[kind]
        out.append((kind, f"{config}/{name}"))
    return out


def _common_rel(kind: str) -> str:
    return f".ccnavi/config/common/{settings.LAYER_FILE_NAMES[kind]}"


def _same_text(a: bytes | None, b: bytes | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def _read_plain(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _repos(conf: settings.Settings) -> list[str]:
    """レイヤーを比べるリポジトリ（ワークスペース自身 `""` と、プロジェクト）。"""
    return ["", *[p.name for p in tree.projects(conf.projects)]]


def layer_drift(conf: settings.Settings, root: str, project: str) -> list[Problem]:
    """手元のレイヤーが `origin/<統合先>` のレイヤー（同じパス）と違う、という warn。

    `project` は空ならワークスペース自身。`origin/<統合先>` が手元に無ければ黙る
    （`--lint` はそれを `_sync` が info で言う）。`--lint` と `sync check` が使う。
    """
    return _drift(conf, root, project)[0]


def _drift(conf: settings.Settings, root: str, project: str) -> tuple[list[Problem], bool]:
    """`layer_drift` の本体。`(warn, 済んだか)`。

    済んでいないのは、origin を持つのに `origin/<統合先>` が手元に無いとき（取得すれば比べられる）。
    origin を持たないリポジトリは、比べる相手が無いので済んだ扱い（黙る）。

    手元の判定は元リポジトリのチェックアウトのレイヤーを読み、統合先へは切り替えない
    （統合先のレイヤーが緩いときに通るものが増えるため）。違いは、統合先に入るまで他の機械と
    Chrome の判定に使われないという知らせ。読むのは取得済みの `refs/remotes/origin/<統合先>`
    だけで、ネットワークには出ない。
    """
    home = tree.project_root(conf.projects, project) if project else root
    if not home or not os.path.isdir(home):
        return [], True
    branch = ops_stop_integrate.integration_name(home)
    ref = f"refs/remotes/origin/{branch}" if branch else ""
    if (
        not ref
        or not gitcmd.run(home, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]).ok
    ):
        # origin が無いリポジトリ（git でない、リモートを持たない）は、取得で解けないので言わない。
        return [], not gitcmd.run(home, ["remote", "get-url", "origin"]).ok
    home_rel = fsio.slashed(conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")
    rels = [rel for _, rel in _layer_files(conf, home_rel)]
    if not project:
        rels = [_common_rel(kind) for kind in settings.LAYER_KINDS] + rels
    where = layer_where(project or ruleload.LAYER_SELF)
    problems: list[Problem] = []
    for rel in rels:
        remote, read = gitcmd.blob(home, ref, rel)
        if not read:
            continue
        local = _read_plain(os.path.join(home, *rel.split("/")))
        if _same_text(local, remote):
            continue
        if local is None:
            how = "ワークツリーにありません。"
        elif remote is None:
            how = "統合先にありません。"
        else:
            how = "中身が違う"
        problems.append(
            Problem(
                SEVERITY_WARN,
                where,
                f"ワークツリーの {rel} が統合先（origin/{branch}）と違う（{how}）。"
                "統合先に取り込まれるまで、他の機械と Chrome の判定には使われない。"
                f"手元の origin/{branch} が古いだけなら、先に取得（fetch）してから見直す",
            )
        )
    return problems, True


def _files_under(base: str) -> list[str]:
    """base の下のファイルを、base からの相対（"/" 区切り）で並べる。順は文字列の順。"""
    found = []
    for parent, _, names in os.walk(base):
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(parent, name), base)
            found.append(rel.replace(os.sep, "/"))
    return sorted(found)

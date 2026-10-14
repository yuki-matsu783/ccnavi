"""`--lint` のうち、設定のレイヤー（共通レイヤー・自身のレイヤー・プロジェクトのレイヤー）の検査。

レイヤーどうしの食い違い、各レイヤーの phases / risk が共通レイヤーと合成できるか、ワークツリーの
ccnavi ディレクトリに元リポジトリに無いファイルが無いか、取り込み状態と統合先のレイヤーが
手元のレイヤーと食い違っていないかを見る。
"""

from __future__ import annotations

import os
from typing import TextIO

from ..infra import fsio, settings, tree
from ..policy import ruleload
from ..policy.rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, Problem
from ..tickets import approval, archive, phase, risk, syncstate, ticket_model
from ..tickets import ticket as ticket_mod
from . import lint_rules


def layer_where(name: str) -> str:
    """そのレイヤーの苦情の出どころの表記。VS Code 拡張がこの前置きでプロジェクトを引く。"""
    if name == ruleload.LAYER_COMMON:
        return "(rules)"
    if name == ruleload.LAYER_SELF:
        return "(self)"
    return f"(projects/{name})"


def _layers(stderr: TextIO, conf: settings.Settings, root: str) -> list[Problem]:
    """レイヤーに食い違いが無いか。

    見るのは 2 つ。レイヤーのファイルが読めることと、レイヤーをまたいだ重複と同名の衝突。
    `.ccnavi/config/project/` が無いことは言わない。
    無いのは正常（無いレイヤー = 空）で、言うと本当に言うべきものが埋もれる。

    共通レイヤーは `_rules` が別に見ているので、ここではレイヤーの 2 つ目以降だけを回す。
    """
    problems: list[Problem] = []
    for view in ruleload.survey(stderr, conf, root)[1:]:
        where = layer_where(view.name)
        if view.unreadable:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    where,
                    f"{view.path} を読めない ({view.unreadable})。"
                    "このレイヤーは空として扱っている。"
                    "共通レイヤーだけで判定しているので、ここに書いた宣言は 1 件も使われていない",
                )
            )
            continue
        if view.missing:
            continue
        from_file = lint_rules._rules(
            view.path,
            root,
            home=_layer_home(conf, root, view.name),
            layer=True,
            project=view.name != ruleload.LAYER_SELF,
        )
        for c in from_file:
            problems.append(Problem(c.severity, f"{where} {c.rule}".rstrip(), c.detail))
        # `survey` はレイヤーのファイルの苦情も `problems` に入れている。`_rules` が同じファイルを
        # 読んで言ったものは数えない。数えると同じ苦情が 2 度並び、件数も水増しされる。
        told = {(c.severity, c.rule, c.detail) for c in from_file}
        for c in view.problems:
            if (c.severity, c.rule, c.detail) in told:
                continue
            problems.append(Problem(c.severity, f"{where} {c.rule}".rstrip(), c.detail))
    return problems


def _legacy_places(conf: settings.Settings, root: str) -> list[Problem]:
    """旧い置き場（`.ccnavi/common/`・`.ccnavi/config/{rules,phases,risks}.yml`）に設定が残っていないか。

    設定の置き場は `.ccnavi/config/common/` と `.ccnavi/config/project/` に移った。旧い置き場の
    ファイルは読まれないので、書いたルール・配点・フェーズ定義が黙って効かなくなる。
    ワークスペースと、その下のプロジェクトの両方を見る。移すのはユーザで、ここは名指しするだけ。
    """
    problems: list[Problem] = []
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("\\", "/").strip("/")
    names = tuple(settings.LAYER_FILE_NAMES.values())
    bases = [("", root)] + [(f"projects/{t.name} ", t.root) for t in tree.projects(conf.projects)]
    for label, base in bases:
        for old, new in (
            (f"{home}/common", f"{home}/{settings.COMMON_DIR}"),
            (f"{home}/config", f"{home}/{settings.LAYER_CONFIG_DIR}"),
        ):
            for name in names:
                path = os.path.join(base, *old.split("/"), name)
                if not os.path.isfile(path):
                    continue
                where = f"({label.strip() or 'legacy'})"
                shown = f"{label.replace(' ', '/')}{old}/{name}"
                problems.append(
                    Problem(
                        SEVERITY_WARN,
                        where,
                        f"{shown} は旧い置き場で、読まれていない。"
                        f"{label.replace(' ', '/')}{new}/{name} へ移す",
                    )
                )
    return problems


def _layer_configs(conf: settings.Settings, root: str) -> list[Problem]:
    """各レイヤーの phases / risk が、共通レイヤーと合成できるか。

    見るのは合成したあとの内容。phases は同 `id` で中身が違う、`title` が共通レイヤーと重なる、
    risk は `levels` が逆転する、`script:` がレイヤーの外を指すか指す先が無い、を error で言い、
    phases の全欄一致で捨てた重複を info で言う。共通レイヤー自身の苦情は `lint._phases` /
    `lint._risk` が別に言うので、ここではレイヤーの側だけを数える。自身のレイヤーとプロジェクトの
    レイヤーどうしは合成しないので、その間の重なりは言わない。

    `.ccnavi/config/project/` が無いことは言わない。無いのは正常（無いレイヤー = 空）。
    """
    problems: list[Problem] = []
    names = [ruleload.LAYER_SELF]
    names += [
        p.name for p in tree.projects(conf.projects) if not settings.is_reserved_layer_name(p.name)
    ]
    for name in names:
        where = layer_where(name)
        project = "" if name == ruleload.LAYER_SELF else name
        _, notes = phase.layer_types(conf, root, project)
        for p in notes:
            problems.append(Problem(p.severity, f"{where} (phases) {p.rule}".rstrip(), p.detail))
        definition, notes = risk.layer_definition(conf, root, project)
        for p in [*notes, *risk.script_problems(definition, name)]:
            problems.append(Problem(p.severity, f"{where} (risk) {p.rule}".rstrip(), p.detail))
    return problems


def _worktree_layers(conf: settings.Settings, root: str) -> list[Problem]:
    """ワークツリーの ccnavi ディレクトリに、元リポジトリに無いファイルがあるか。

    判定が読むのは元リポジトリに checkout されている版だけ。
    ワークツリーの `.ccnavi/` に足したファイルは、そのブランチが統合されるまで使われない。
    使われないものを書いたユーザは、書いたとおりに使われていると思ったまま進む。統合の前に
    気づけるように、ここで名前を挙げる。

    足したファイルを問題にしているのではない。設定を書き進める場所はワークツリーでよく、
    そこから統合するのも普通の手順。言うのは「今はまだ使われていない」という 1 点だけ。

    中身の違いは見ない。同じパスのファイルが両方に在れば、それは編集で、git の
    差分が拾う。ここが拾うのは、元リポジトリに無くて差分にも出ない新しいパスのほう。

    承認済みの領域（承認済みチケット・マーカー・子の記録・フロー）は数えない（ユーザの決定）。
    承認済みチケットは親のワークツリーに置かれ、判定もフローの案内もそのツリーの版を読む。
    「統合されるまで使われない」は当てはまらず、言えば誤った案内になる。
    """
    problems: list[Problem] = []
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("/", os.sep)
    for work in tree.worktrees(root, conf.projects):
        origin = tree.project_root(conf.projects, work.project) if work.project else root
        approved = os.path.normcase(os.path.normpath(settings.approved_dir(conf, work.root)))
        for rel in _files_under(os.path.join(work.root, home)):
            if os.path.exists(os.path.join(origin, home, rel.replace("/", os.sep))):
                continue
            if work.project and rel.startswith(f"{settings.COMMON_DIR}/"):
                # プロジェクトのワークツリーの `.ccnavi/config/common/` は、親の着手がミラーした
                # 共通レイヤー。ワークスペースの中では読まれないので、言わない。
                continue
            where = os.path.normcase(
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
    """取り込み状態と、取り込み済みの親子のチケットで正とする側（親のブランチ上のチケットだけを正とする）。

    - 親のワークツリー（名前が親の識別子）なのに HEAD が別のブランチ: warn（移行の検査）
    - 親子のチケットの取り込み状態が不正である・gone・blocked、
      `present` なのに親のワークツリーが無い: error（その親子のチケットは決まらないので、
      承認も状態の操作も止まる）。閉じた親子のチケットは、
      親のワークツリーが残っていれば info（片付けてよい）、
      片付いていれば何も言わない（削除せずに残す取り込み状態）
    - 統合先の取り込み結果が不正である・無い・読めない: error（識別子の再利用を確かめられない）
    - 作業ツリーのレイヤーと統合先の取り込み結果のレイヤーが違う: warn

    親のワークツリーの外にしか無いチケット（移行の検査）は、チケットの `blocked` として
    `_copy_problems` が error で言う。取り込み状態の無い親子のチケットには、
    最初の 1 つのほかは何も言わない。
    """
    problems = _parent_trees_off_branch(conf, root)
    fams = syncstate.Families(conf, root)
    if not fams.active:
        return problems
    for repo, name in syncstate.family_names(conf.state):
        st = fams.standing(name, syncstate.project_of_key(repo))
        if not st.imported:
            continue
        where = f"(sync/{repo}/{name})"
        hint = " / ".join(syncstate.guidance(root, st))
        if st.closed:
            if st.home is not None:
                problems.append(Problem(SEVERITY_INFO, where, f"{st.stop}。{hint}"))
        elif st.stop:
            problems.append(Problem(SEVERITY_ERROR, where, f"{st.stop}。{hint}"))
    for repo in syncstate.repos(conf.state):
        integ = fams.integration(repo)
        if integ is None:
            continue
        where = f"(sync/{repo}/integration)"
        if integ.broken:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    where,
                    f"統合先の取り込み結果を読めない（{integ.broken}）。クローズ済みの識別子の再利用を確かめられない"
                    "ので、このリポジトリの新規の提案は承認しない。オンラインで"
                    f" '{settings.script_command(root, 'ccnavi-sync.sh')}' を再実行してください",
                )
            )
            continue
        home = root if repo == syncstate.SELF else tree.project_root(conf.projects, repo)
        if home and os.path.isdir(home):
            problems.extend(_layer_drift(conf, integ, home, where))
    return problems


def _parent_trees_off_branch(conf: settings.Settings, root: str) -> list[Problem]:
    """名前が親の識別子なのに、HEAD のブランチ名を変換した識別子が名前と合わない親のワークツリー。

    ブランチ名の `/` を `-` に置き換えたものが、親のワークツリーのフォルダ名になる。"""
    problems: list[Problem] = []
    for work in tree.worktrees(root, conf.projects):
        if not _holds_parent(conf, work, root):
            continue
        branch = tree.branch_of(work.root)
        if branch and syncstate.head_branch(work.root, work.name):
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


def _layer_drift(
    conf: settings.Settings, integ: syncstate.Integration, home: str, where: str
) -> list[Problem]:
    """作業ツリーのレイヤーと、統合先の取り込み結果のレイヤー（同じパス）が違うか。

    手元の判定は作業ツリーのレイヤーを読み、統合先の取り込み結果へは切り替えない（統合先のレイヤーが作業ツリーより
    緩いときに通るものが増えるため）。違いは、
    統合先に入るまで他の機械と Chrome の判定に使われないという知らせ。
    """
    home_rel = fsio.slashed(conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")
    rels = [rel for _, rel in _layer_files(conf, home_rel)]
    if integ.repo == syncstate.SELF:
        rels = [_common_rel(kind) for kind in settings.LAYER_KINDS] + rels
    problems: list[Problem] = []
    for rel in rels:
        snapshot, why = integ.file(rel)
        if why:
            problems.append(
                Problem(SEVERITY_ERROR, where, f"統合先の取り込み結果を読めない（{why}）")
            )
            continue
        local = _read_plain(os.path.join(home, *rel.split("/")))
        if _same_text(local, snapshot):
            continue
        if local is None:
            how = "作業ツリーにありません。"
        elif snapshot is None:
            how = "統合先にありません。"
        else:
            how = "中身が違う"
        problems.append(
            Problem(
                SEVERITY_WARN,
                where,
                f"作業ツリーの {rel} が統合先（{integ.branch or '?'}）の取り込み結果と違う"
                f"（{how}）。"
                "統合先に取り込まれるまで、他の機械と Chrome の判定には使われない",
            )
        )
    return problems


def _files_under(base: str) -> list[str]:
    """base の下のファイルを、base からの相対（"/" 区切り）で並べる。順は文字列の順。"""
    found = []
    for parent, _, names in os.walk(base):
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(parent, name), base)
            found.append(rel.replace(os.sep, "/"))
    return sorted(found)


def _layer_home(conf: settings.Settings, root: str, name: str) -> str:
    if name == ruleload.LAYER_SELF:
        return root
    return tree.project_root(conf.projects, name)

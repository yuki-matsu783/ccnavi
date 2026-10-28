"""`--lint` のうち、チケットのブランチの検査。ブランチ名・連番・接頭辞の設定・
既にあるブランチ・ワークツリーを見る。
"""

from __future__ import annotations

import os
from collections.abc import Callable

from ..infra import gitcmd, settings, tree
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from ..tickets import next_id
from ..tickets.model import ticket_ids, ticket_model
from ..tickets.proposal import agree_candidates


def _branch_name_problems(
    proposals: list,
    copies: list,
    closed: list,
    review: list,
    integration_of: Callable[[str], str] = lambda project: "",
    prefixes=settings.DEFAULT_BRANCH_PREFIXES,
    adopted: frozenset = frozenset(),
    conf: settings.Settings | None = None,
    root: str = "",
) -> list[Problem]:
    """新しく切る親の識別子を、ブランチ名として使える形にできるか（warn だけ。拒否は
    `ccnavi-git.sh` が受け持つ）。

    親の識別子は親のブランチ名の `/` を `-` に置き換えたもの。新しく切る名前は、そのまま
    ブランチ名にもなる。そのために次を名指しする。

    既存のブランチを採用した親（`adopted`。親のワークツリーが在り、HEAD のブランチ名を変換した
    ものが識別子と一致する）には、名前の形式（`ticket_ids.branch_name_problems`）と番号の重なりを
    言わない。ブランチ名は採用の時点で決まっていて、こちらでは選べないため。

    - 新規の提案（`todo/` にあって、承認済みでも閉じてもいないもの）の識別子の形
      （`ticket_ids.branch_name_problems`）。承認済みの識別子はもう変えられないので言わない
    - 大文字小文字だけが違う識別子。Windows と macOS の既定のファイルシステムでは
      ブランチもワークツリーも同じ名前になる
    - 末尾が `-<2 桁>` の親の識別子。`-<2 桁>-<2 桁>` で終われば子の形（`<親>-<フェーズ>-<連番>`）に
      ヒットし、親子のチケットを引くとき別の親の子と読まれる。
      `-<2 桁>` だけでも、別の親の子の識別子の
      途中（`<親>-<フェーズ>`）と紛れる

    承認と判定はまだ変えない。止めるのは後の段階で、ここで先に数を見ておく。
    `integration_of` はチケットの `project`（空ならワークスペース）から、そのリポジトリの統合先の
    名前（`ops_stop_integrate.integration_name`）を引く関数。決まらなければ固定のリストだけを見る。
    """
    problems: list[Problem] = []
    everyone = list(copies) + list(closed) + list(review) + list(proposals)
    settled = {t.ticket for t in list(copies) + list(closed) + list(review)}
    serial = ticket_ids.next_serial([t.ticket for t in everyone], prefixes)
    if conf is not None:
        # `ticket next-id` と同じ集め方（手元のブランチとワークツリーも数える）。読み込み済みの
        # 一覧を渡すので、置き場は読み直さない。数えきれなければ、番号を添えずにそう言う。
        names, counting = next_id.serial_names(conf, root, (copies, closed, review, proposals))
        if counting:
            serial = 0
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"次の通し番号を数えきれなかった（{'。'.join(counting)}）。番号を決めずに、"
                    "ユーザに確かめる（親のブランチ名の規則）",
                )
            )
        else:
            serial = ticket_ids.next_serial(names, prefixes, skip_children=False)
    said: set[str] = set()
    fresh: list = []
    for t in proposals:
        if t.state != ticket_model.TODO or t.ticket in settled or t.ticket in said:
            continue
        said.add(t.ticket)
        if t.ticket in adopted:
            continue
        fresh.append(t)
        for text in ticket_ids.branch_name_problems(t, integration_of(t.project), serial, prefixes):
            problems.append(
                Problem(SEVERITY_WARN, "(ticket)", f"{t.ticket}: {text}（親のブランチ名の規則）")
            )
    problems.extend(_number_problems(everyone, fresh, prefixes, serial))

    spellings: dict[str, set[str]] = {}
    for t in everyone:
        spellings.setdefault(t.ticket.casefold(), set()).add(t.ticket)
    for names in spellings.values():
        if len(names) > 1:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{' と '.join(sorted(names))} は大文字小文字だけが違う。"
                    "大文字小文字を区別しないファイルシステムでは、ブランチとワークツリーの"
                    "名前がぶつかる（親のブランチ名の規則）",
                )
            )

    child = ticket_ids.child_pattern()
    tail = ticket_ids.child_tail_pattern()
    parents = sorted({t.ticket for t in everyone if not t.is_child})
    for name in parents:
        if tail.search(name) is None:
            continue
        matched = child.match(name)
        if matched is not None:
            said_how = (
                f"識別子が子の形（`<親>-<2 桁のフェーズ番号>-<2 桁の連番>`）と一致する。"
                f"親子のチケットをまとめるとき {matched.group('parent')} の子として扱われる"
            )
        else:
            said_how = (
                "識別子の末尾が `-<2 桁>` で、別の親の子の識別子の途中"
                "（`<親>-<2 桁のフェーズ番号>`）と紛れる"
            )
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f"{name} は親なのに、{said_how}。"
                "親の識別子の末尾を `-<2 桁>` にしないでください（親のブランチ名の規則）",
            )
        )
    return problems


def _number_problems(everyone: list, fresh: list, prefixes, serial: int) -> list[Problem]:
    """新規の提案の親の識別子の番号が、同じリポジトリの別の親と重なるか（warn）。

    番号は issue の番号で、別のリポジトリの issue や直接作業の通し番号と同じ数になりうる。
    issue の番号はリポジトリごとなので、比べるのは同じリポジトリ（`project`）の親どうしだけ。
    数えるのは `<先頭の語>-<番号>-<slug>` の形の番号だけ。
    """
    problems: list[Problem] = []
    owners: dict[tuple[str, int], set[str]] = {}
    for t in everyone:
        if t.is_child:
            continue
        number = ticket_ids.identifier_number(t.ticket, prefixes)
        if number is not None:
            owners.setdefault((t.project, number), set()).add(t.ticket)
    for t in fresh:
        if t.is_child:
            continue
        number = ticket_ids.identifier_number(t.ticket, prefixes)
        if number is None:
            continue
        others = sorted(owners.get((t.project, number), set()) - {t.ticket})
        if others:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket}: 識別子の番号 {number} が {', '.join(others)} と重なる。"
                    "別のリポジトリの issue や直接作業の通し番号と同じ数になっていないか確かめる"
                    "（親のブランチ名の規則）",
                )
            )
    return problems


def _prefix_setting_problems(conf: settings.Settings) -> list[Problem]:
    """`CCNAVI_BRANCH_PREFIXES` に書かれた、先頭の語に使えない語（warn）。"""
    if not conf.branch_prefixes_rejected:
        return []
    return [
        Problem(
            SEVERITY_WARN,
            "(settings)",
            f"{settings.BRANCH_PREFIXES_ENV} の {', '.join(conf.branch_prefixes_rejected)} は"
            "先頭の語に指定できないので読まない（英小文字で始まり、英小文字と数字だけでできた語。main・master・"
            "develop・release は指定できない）。"
            f"使うリストは {', '.join(conf.branch_prefixes)}（親のブランチ名の規則）",
        )
    ]


def _existing_branch_problems(
    root: str, conf: settings.Settings, proposals: list, copies: list, closed: list, review: list
) -> list[Problem]:
    """新規の提案（親）のブランチ名が、そのリポジトリの手元か origin に既にあるか（warn）。

    親のブランチ名は識別子そのもの。既にあるブランチと同じ名前で承認すると、別の作業のブランチを
    親のブランチとして取り込み・送ることになる。承認の前に名指しする。
    提案がそのブランチの上で書かれている（`.claude/worktrees/<識別子>` がそのブランチを
    チェックアウトしていて、提案がその中にある）ときは、そのブランチが親のブランチなので言わない。
    """
    return [
        Problem(SEVERITY_WARN, "(ticket)", f"{text}（親のブランチ名の規則）")
        for text in agree_candidates.existing_branch_warnings(
            root, conf, proposals, copies, closed, review
        )
    ]


def _worktree_problems(
    root: str,
    conf: settings.Settings,
    worktrees: list,
    index: dict,
    copies: list,
    known: frozenset = frozenset(),
) -> list[Problem]:
    """ワークツリーの側。チケットの無いツリー、迷い込んだ承認済みチケット、元リポジトリの食い違い、ツリーの無い承認済みチケット。

    `known` は未承認・閉じた・審査中を含む、チケットの識別子の集まり。
    直接作業（どのチケットでもないツリー）の名前だけを、`known` と照らして見る。
    """
    problems: list[Problem] = []
    for t in worktrees:
        if t.name not in index:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"ワークツリー {t.name} にチケットがありません。"
                    "そこへの書き込みはルールだけで判定する",
                )
            )
            text = ticket_ids.direct_name_warning(t.name, conf.branch_prefixes, root)
            if text and t.name not in known:
                problems.append(Problem(SEVERITY_WARN, "(ticket)", text))

        bound = index.get(t.name)
        if bound is not None:
            parent = index.get(bound.parent) if bound.is_child else None
            owner = parent.project if parent is not None else bound.project
            if owner != t.project:
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        "(ticket)",
                        f"ワークツリー {t.name} の元リポジトリ（{t.project or 'ワークスペース'}）が"
                        "承認済みチケットの "
                        f"project（{owner or 'ワークスペース'}）と違う。そこへの書き込みは止まる。"
                        "承認済みチケットが指すリポジトリから切り直してください",
                    )
                )
    names = {t.name for t in worktrees}
    for t in copies:
        if t.ticket not in names:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket} は承認済みだがワークツリー "
                    f"{tree.worktree_path(root, t.ticket)} がありません。"
                    "ワークツリーを作るまで範囲は有効にならない",
                )
            )
    return problems


def _place_problems(
    root: str,
    proposals: list,
    copies: list,
    closed: list,
    review: list,
    repo_of: dict[str, str],
) -> list[Problem]:
    """未承認の親の提案の置き場所と中身の形（warn）。

    - 親の提案が、親の識別子と同名でないワークツリーに在る。子の提案が、親の識別子と同名の
      ワークツリーに無い。親が取り込み済みにならず、承認済みチケットの自動コミットが動かない。
      ワークスペースルートとプロジェクトの元ツリーの提案は言わない
    - 計画（plan）を持たない親。`ticket start` が止まる（子や子の提案があっても同じ）
    """
    settled = {t.ticket for t in list(copies) + list(closed) + list(review)}
    # 親のワークツリーは ccnavi-start.sh が作る（MR の元ブランチを直接チェックアウトする）。
    # 識別子の名前のブランチは作らない。
    cut = f"sh {root or '<ルート>'}/.ccnavi/scripts/ccnavi-start.sh --mr <MR の番号>"
    problems: list[Problem] = []
    said: set[tuple[str, str]] = set()
    for t in proposals:
        if t.state != ticket_model.TODO:
            continue
        at = t.tree or ""
        if at and repo_of.get(at) == at:
            at = ""  # プロジェクトの元ツリー
        if t.is_child:
            if not at or at == t.parent or t.parent in settled or not t.parent:
                continue
            key = (t.ticket, at)
            if key in said:
                continue
            said.add(key)
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket}: 子の提案を {at} に書いている。親 {t.parent} のワークツリーは"
                    f"識別子と同じ名前にし、子の提案はそこに置く。ワークツリーが無ければ "
                    f"`{cut}`（MR が無ければ `--issue <issue の番号>`）で作り、"
                    "提案を移して元を削除します。"
                    "このままだと親が取り込み済みにならない",
                )
            )
            continue
        if t.ticket in settled:
            continue
        if at and at != t.ticket:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket}: 提案 {t.ticket} を {at} に書いている。親のワークツリーは"
                    f"識別子と同じ名前にする。`{cut}`（MR が無ければ `--issue <issue の番号>`）で"
                    "作り、提案を移して元を削除します。このままだと親が取り込み済みにならない",
                )
            )
        if not t.has_plan:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"{t.ticket}: 親 {t.ticket} に plan がありません。"
                    "plan の無い親は `ticket start` が止まる（子や子の提案があっても同じ）。"
                    "`ccnavi-launcher.sh --docs --keyword 提案書式` と `--keyword phases` で"
                    "書式と型の決め方を引き、"
                    "phases.yml の定義を使う plan を追加します。",
                )
            )
    return problems


def _unregistered_worktree_problems(root: str, worktrees: list) -> list[Problem]:
    """`.claude/worktrees/` 直下の、どこにも登録の無いディレクトリ（warn）。

    ワークツリーの一覧（`tree.worktrees`）はファイルシステムだけで決める（実行前チェックは
    git を起こさない）。ここは `--lint` の中だけなので `git worktree list` にも聞く。
    他のリポジトリ（プロジェクトなど）の `.git/worktrees` に登録されたものは、ここでは数えない
    ので言わない。git に聞けなければ何も言わない。
    """
    base = os.path.join(root, tree.WORKTREES_DIR)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    known = {t.name for t in worktrees}
    pending = [
        n
        for n in names
        if tree.nfc(n) not in known
        and os.path.isdir(os.path.join(base, n))
        and not n.startswith(".")
    ]
    if not pending:
        return []
    rc, out = gitcmd.output(root, ["worktree", "list", "--porcelain"], 10)
    if rc != 0:
        return []
    listed = {
        os.path.realpath(line[len("worktree ") :].strip())
        for line in out.splitlines()
        if line.startswith("worktree ")
    }
    registry = os.path.realpath(os.path.join(root, ".git", "worktrees"))
    problems: list[Problem] = []
    for name in pending:
        path = os.path.join(base, name)
        if os.path.realpath(path) in listed or _registered_elsewhere(path, registry):
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(ticket)",
                f".claude/worktrees/{name} は git に登録がありません。中身を確かめ、不要なら"
                f"ユーザに確かめてから ccnavi-clean.sh {name}",
            )
        )
    return problems


def _registered_elsewhere(path: str, registry: str) -> bool:
    """`.git` が、ワークスペースのものではない `.git/worktrees/` を指しているか。"""
    try:
        with open(os.path.join(path, ".git"), encoding="utf-8") as f:
            line = f.read().strip()
    except OSError:
        return False
    if not line.startswith("gitdir:"):
        return False
    gitdir = line[len("gitdir:") :].strip().replace("\\", "/")
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(path, gitdir)
    gitdir = os.path.realpath(gitdir)
    return "/.git/worktrees/" in gitdir.replace("\\", "/") and not gitdir.startswith(
        registry + os.sep
    )

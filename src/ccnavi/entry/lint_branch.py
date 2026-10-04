"""`--lint` のうち、チケットのブランチの検査。ブランチ名・連番・接頭辞の設定・
既にあるブランチ・ワークツリーを見る。
"""

from __future__ import annotations

from ..infra import settings, tree
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from ..tickets import agree, ticket_ids, ticket_model


def _branch_name_problems(
    proposals: list,
    copies: list,
    closed: list,
    review: list,
    integration: str = "",
    prefixes=settings.DEFAULT_BRANCH_PREFIXES,
) -> list[Problem]:
    """識別子を親のブランチ名にできるか（warn だけ。拒否は `ccnavi-git.sh` が受け持つ）。

    親のブランチ名は親の識別子そのものにする。そのために次を名指しする。

    - 新規の提案（`todo/` にあって、承認済みでも閉じてもいないもの）の識別子の形
      （`ticket_ids.branch_name_problems`）。承認済みの識別子はもう変えられないので言わない
    - 大文字小文字だけが違う識別子。Windows と macOS の既定のファイルシステムでは
      ブランチもワークツリーも同じ名前になる
    - 末尾が `-<2 桁>` の親の識別子。`-<2 桁>-<2 桁>` で終われば子の形（`<親>-<フェーズ>-<連番>`）に
      当たり、親子のチケットを引くとき別の親の子と読まれる。`-<2 桁>` だけでも、別の親の子の識別子の
      途中（`<親>-<フェーズ>`）と紛れる

    承認と判定はまだ変えない。止めるのは後の段階で、ここで先に数を見ておく。
    `integration` はその時点の統合先の名前で、`--integration-branch` が無ければ `ccnavi-sync.sh` が
    取り込み結果に書いた名前。どちらも無ければ固定のリストだけを見る。
    """
    problems: list[Problem] = []
    everyone = list(copies) + list(closed) + list(review) + list(proposals)
    settled = {t.ticket for t in list(copies) + list(closed) + list(review)}
    serial = ticket_ids.next_serial([t.ticket for t in everyone], prefixes)
    said: set[str] = set()
    fresh: list = []
    for t in proposals:
        if t.state != ticket_model.TODO or t.ticket in settled or t.ticket in said:
            continue
        said.add(t.ticket)
        fresh.append(t)
        for text in ticket_ids.branch_name_problems(t, integration, serial, prefixes):
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

    番号は issue の番号か ccnavi の通し番号で、両方が同じ数になりうる。issue の番号はリポジトリ
    ごとなので、比べるのは同じリポジトリ（`project`）の親どうしだけ。前の形（`i0055`）の番号も数える。
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
                    "issue の番号と通し番号が同じ数になっていないか確かめる。"
                    f"issue が無いなら次の通し番号は {serial}（親のブランチ名の規則）",
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
            "先頭の語に使えないので読まない（英小文字で始まる英小文字と数字。main・master・"
            "develop・release は使えない）。"
            f"使うリストは {', '.join(conf.branch_prefixes)}（親のブランチ名の規則）",
        )
    ]


def _existing_branch_problems(
    root: str, conf: settings.Settings, proposals: list, copies: list, closed: list, review: list
) -> list[Problem]:
    """新規の提案（親）のブランチ名が、そのリポジトリの手元か origin に既にあるか（warn）。

    親のブランチ名は識別子そのもの。既にあるブランチと同じ名前で承認すると、別の作業のブランチを
    親のブランチとして取り込み・送ることになる。止めはせず、承認の前に名指しする。
    提案がそのブランチの上で書かれている（`.claude/worktrees/<識別子>` がそのブランチを
    チェックアウトしていて、提案がその中にある）ときは、そのブランチが親のブランチなので言わない。
    """
    return [
        Problem(SEVERITY_WARN, "(ticket)", f"{text}（親のブランチ名の規則）")
        for text in agree.existing_branch_warnings(root, conf, proposals, copies, closed, review)
    ]


def _worktree_problems(
    root: str, conf: settings.Settings, worktrees: list, index: dict, copies: list
) -> list[Problem]:
    """ワークツリーの側。チケットの無いツリー、迷い込んだ承認済みチケット、元リポジトリの食い違い、ツリーの無い承認済みチケット。"""
    problems: list[Problem] = []
    for t in worktrees:
        if t.name not in index:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    "(ticket)",
                    f"ワークツリー {t.name} にチケットが無い。"
                    "そこへの書き込みはルールだけで判定する",
                )
            )

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
                    f"{tree.worktree_path(root, t.ticket)} が無い。"
                    "ワークツリーを作るまで範囲は効かない",
                )
            )
    return problems

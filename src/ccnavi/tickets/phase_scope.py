"""子チケットの範囲の当て方。書き込み先がどの子の範囲に入るかと、範囲の外の変更の洗い出し。

サブエージェントの終わりと実行後チェックが使う。phase から分けた。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..infra import gitcmd, settings, tree
from ..policy import rules
from . import (
    phase,
    phasetypes,
    ticket_model,
    ticket_places,
)
from . import ticket as ticket_mod

# 範囲の外へ出した上限の名前（ScopeVerdict.limit）。
LIMIT_TICKET = "ticket"
LIMIT_PARENT = "parent"
LIMIT_TYPE = "type"
# チケット自体が信頼できない（`Ticket.blocked`）。範囲を当てる前に止める。
LIMIT_BLOCKED = "blocked"

# 範囲の外として止める判定。
_OUTSIDE = (ticket_model.OUTSIDE, rules.DENY)


@dataclass
class ScopeVerdict:
    """子のワークツリーの 1 つのパスについて、範囲の上限を合わせた判定。

    上限は子自身・親・フェーズ定義の 3 つで、厳しい側を採る。どれが外へ出したかを
    持つのは、文面でその上限を名指しするため。名指しが無いと、承認で見た範囲の中なのに
    止まった理由を、止められた側が読めない。
    """

    # ALLOW / ASK / DENY / OUTSIDE
    verdict: str
    # 外へ出した上限。中なら空。
    limit: str = ""
    type: phasetypes.PhaseType | None = None

    @property
    def outside(self) -> bool:
        return self.verdict in _OUTSIDE


def scope_verdict(
    child: ticket_model.Ticket,
    parent: ticket_model.Ticket | None,
    pt: phasetypes.PhaseType | None,
    rel: str,
) -> ScopeVerdict:
    """子の範囲を、親の範囲と定義の上限で切り詰める。

    範囲を当てる 3 か所（実行前チェック、実行後チェック、SubagentStop の差し戻し）はこれを
    通す。別に書くと、同じ書き込みが実行前は通って実行後に範囲外と報告される。承認は範囲の超過を
    警告で通すので、超えた分を止めるのはここだけになる。

    順は 子 → 親 → 定義。厳しい側を採るので順は判定を変えないが、`limit` は最初に
    外へ出した上限を名指しする。定義の上限は allow か外しか言わない。子が ask と書いた
    場所が定義の中なら ask のまま。

    その前に `blocked` を見る。承認のときにしか当たらなかった構造の検査に引っかかった
    チケットは、範囲を当てても意味が無い（親が引けない子は、どの範囲で切り詰めるかが
    決まらない）。範囲の中でも外でも止める。
    """
    if child.blocked:
        return ScopeVerdict(rules.DENY, LIMIT_BLOCKED, pt)
    verdict = child.decide(rel)
    limit = LIMIT_TICKET if verdict in _OUTSIDE else ""
    if parent is not None:
        verdict = ticket_mod.combine(verdict, parent.decide(rel))
        if not limit and verdict in _OUTSIDE:
            limit = LIMIT_PARENT
    if pt is not None and not pt.inherits_scope:
        verdict = ticket_mod.combine(verdict, pt.decide(rel))
        if not limit and verdict in _OUTSIDE:
            limit = LIMIT_TYPE
    return ScopeVerdict(verdict, limit, pt)


def plan_item(
    child: ticket_model.Ticket, parent: ticket_model.Ticket | None
) -> ticket_model.PlanItem | None:
    """子の番号が指す、親の計画の項。親が計画を持たない、番号が無い、計画に無いなら None。"""
    if parent is None or not parent.has_plan or child.phase is None:
        return None
    return parent.item_at(child.phase)


def type_for(
    conf: settings.Settings,
    root: str,
    child: ticket_model.Ticket,
    parent: ticket_model.Ticket | None,
    types: dict[str, phasetypes.PhaseType] | None = None,
) -> phasetypes.PhaseType | None:
    """子の番号の定義。親が計画を持たない、番号が無い、定義が引けないなら None。

    `types` を渡せばそこから引き、ファイルは読まない（実行後チェックはレイヤーごとに 1 度だけ
    読んで持つ）。渡さなければ、親が計画を持つときだけ親の `project:` のレイヤーを読む。
    """
    item = plan_item(child, parent)
    if item is None or parent is None:
        return None
    if types is None:
        types = phase.load_types(conf, root, parent.project)
    return (types or {}).get(item.type)


def unread_type(
    conf: settings.Settings,
    root: str,
    child: ticket_model.Ticket,
    parent: ticket_model.Ticket | None,
    types: dict[str, phasetypes.PhaseType] | None,
) -> str:
    """子の番号の定義が読めないなら、その定義の id。読めた、または読むものが無ければ空。

    `types` は `load_types` が返したもの（None を含む）。phases.yml がどのレイヤーにも無いのは
    番号だけの挙動で、読めないのではないので何も言わない。ファイルは在るのに定義が
    引けない（壊れた・定義を消した）ときだけ返す。そのとき判定は定義では切り詰めない。
    deny にすると、ユーザが phases.yml を直している間、全部の子のワークツリーで書き込みが止まる。
    """
    item = plan_item(child, parent)
    if item is None or parent is None:
        return ""
    if types is not None:
        return "" if item.type in types else item.type
    files = (conf.phases, phase.types_path(conf, root, parent.project))
    return item.type if any(p and os.path.exists(p) for p in files) else ""


def scope_findings(
    root: str,
    conf: settings.Settings,
    child: ticket_model.Ticket,
    parent: ticket_model.Ticket | None,
) -> tuple[list[tuple[str, ScopeVerdict]], str]:
    """子のワークツリーに残っている範囲外の変更と、その判定。2 つめは読めなかった理由。

    見るのは `base_sha..HEAD` のコミット済みの差分と、未コミットの変更の両方。
    未コミットだけ見る検査では、範囲外を書いてコミットしたものが反映されない。
    範囲は実行前チェックと同じく、親の範囲と定義の上限で切り詰める（scope_verdict）。
    """
    worktree = tree.worktree_path(root, child.ticket)
    if not os.path.isdir(worktree):
        return [], "ワークツリーが無い"
    # NUL 区切りで読む。既定の出力は非 ASCII と空白を含むパスを引用符で囲んで 8 進で
    # エスケープするので、そのまま照らし合わせると範囲の中の日本語のファイルが必ず範囲外になる。
    #
    # `--no-renames` と `--ignore-submodules` は、変更が差分の行に出なくなるのを防ぐ。改名を
    # 1 行にまとめられると移動元のパスが出力に出ず、範囲外のファイルを範囲の中へ改名したものが
    # 範囲外と判定されない。`.gitmodules` の `ignore = all` は submodule の進みを差分にまったく
    # 出さなくする
    # （`.gitmodules` は追跡されるので、外から届く）。
    #
    # status だけ `dirty` なのは、submodule の中の未コミットの変更は親のコミットに乗らないから。
    # 乗るのはポインタの移動で、`dirty` ではそれが出力に出る。`none` にすると、submodule の
    # 中に置かれた生成物まで範囲外として報告することになる。
    paths: set[str] = set()
    if child.base_sha:
        rc, out = _git(
            worktree,
            [
                "diff",
                "--name-only",
                "--no-renames",
                "--ignore-submodules=none",
                "-z",
                f"{child.base_sha}..HEAD",
            ],
        )
        if rc != 0:
            return [], "基準点からの差分を読めない"
        paths.update(p for p in out.split("\0") if p)
    rc, out = _git(
        worktree,
        [
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--ignore-submodules=dirty",
        ],
    )
    if rc != 0:
        return [], "ワークツリーの状態を読めない"
    for entry in out.split("\0"):
        if len(entry) > 3 and entry[2] == " ":
            paths.add(entry[3:])
    pt = type_for(conf, root, child, parent)
    outside = []
    for rel in sorted(paths):
        # git の `-z` の表記をそのまま使う。git はどの OS でも区切りを `/` で返すので、
        # `\` を `/` に直す必要は無い。直すと Linux / macOS で `wip\eli5\x.py` や `src\x.py` という
        # 名前のファイル 1 個が、置き場の中や範囲の中のパスと判定され、範囲外として報告されない
        # （実行前チェックも直さない）。
        # 外すのはチケットの置き場だけ。下書きの置き場（`scratchpad/`）はここでは外さない。
        # 見ているのは `base_sha..HEAD` の差分（追跡ファイルだけ）と `git status`
        # （`--ignored` を付けない）で、追跡から外れている `scratchpad/` はどちらにも現れない。
        # 現れたということはそのツリーの git が `scratchpad/` を追跡しているということで、
        # 外してよい根拠（追跡されないので統合先へ乗らない）が崩れている。範囲外のものが
        # コミットに乗って統合先へ入る経路を見ているのはここだけなので、そこは除外して報告を
        # 消すことはしない。
        if ticket_places.is_ticket_place(rel, conf.tickets, conf.approved):
            continue
        # ELI5 の置き場は追跡されるので、ここでも外す（実行前チェックと揃える）。
        if ticket_places.is_eli5_place(rel):
            continue
        found = scope_verdict(child, parent, pt, rel)
        if found.outside:
            outside.append((rel, found))
    return outside, ""


def _git(cwd: str, args: list[str]) -> tuple[int, str]:
    return gitcmd.output(cwd, args, phase.TIMEOUT_SECONDS, raw_paths=True)

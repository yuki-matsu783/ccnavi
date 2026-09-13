"""フェーズの種類。`.claude/ccnavi/phases.yml` を読む（設計 §24.15.1）。

## 種類は人が持つ

エージェントが種類を書けると、レビュー不要の種類を作ってから使える。だから置き場は
ルールの `guard-ccnavi-config` の内側で、作業ツリー側の設定も含めてエージェントの Write は
止まる。組み込みの既定は持たない。
既定を組み込むと、意図せずレビューの要否が決まる。ファイルが無ければ、フェーズは
番号だけの今までの挙動で、親の `plan` も読めない。

## 用語

**フェーズの種類**はここで定義される名前付きの型。**フェーズ**は親の計画に並ぶ番号付きの
実体で、子が `phase: N` で指す。N 番目が何の種類かは親の計画が言う。

## 書式

    version: 1
    phases:
      research:
        kind: work            # work | feedback
        title: 調査
        review: none          # none | mr
        scope: ["wip/research/*"]   # 子の範囲の上限。inherit なら親の範囲
        deliverables: ["wip/research/summary.md"]
        overlap: [design]     # 並行してよい種類（対称）
        requires: [design]    # 計画に置くなら一緒に要る種類
        agent: explorer       # 案内にだけ使う
        when: 既存の振る舞いが分からないとき   # 案内にだけ使う
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml

from . import globmatch, rules
from . import ticket as ticket_mod
from .rules import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, Problem

VERSION = 1

KIND_WORK = "work"
KIND_FEEDBACK = "feedback"
KINDS = (KIND_WORK, KIND_FEEDBACK)

REVIEW_NONE = "none"
REVIEW_MR = "mr"
REVIEWS = (REVIEW_NONE, REVIEW_MR)

# 種類の範囲が「親の範囲そのまま」であることを言う綴り。
INHERIT = "inherit"

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class PhaseType:
    id: str
    title: str = ""
    kind: str = KIND_WORK
    review: str = REVIEW_MR
    # None なら inherit（親の範囲そのまま）。
    scope: list[ticket_mod.Entry] | None = None
    scope_globs: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    overlap: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    agent: str = ""
    when: str = ""
    # source はこの種類が書いてある層の名前（`common` / `self` / プロジェクト名）。
    # id は裸のままで、層は記録と `--explain` の欄に出す（設計 §25.4.1）。
    source: str = ""

    @property
    def inherits_scope(self) -> bool:
        return self.scope is None

    def key(self) -> tuple:
        """層をまたいで「同じ定義か」を比べるための全欄。`source` は含めない。

        含めると、どの層から来たかだけが違う写しが衝突扱いになる。比べたいのは
        中身で、置いてある場所ではない。
        """
        return (
            self.id,
            self.title,
            self.kind,
            self.review,
            None if self.scope is None else tuple(self.scope_globs),
            tuple(self.deliverables),
            tuple(self.overlap),
            tuple(self.requires),
            self.agent,
            self.when,
        )

    def decide(self, rel: str) -> str:
        """この種類の範囲が、作業ツリーのルートからの相対パスをどう扱うか。inherit なら常に中。"""
        if self.scope is None:
            return rules.ALLOW
        for entry in self.scope:
            if entry.matches(rel):
                return rules.ALLOW
        return ticket_mod.OUTSIDE

    def overlaps(self, other: PhaseType) -> bool:
        """並行してよい組か。どちらかが相手を挙げていれば対称に効く。"""
        return other.id in self.overlap or self.id in other.overlap


def load(path: str, refs: bool = True) -> tuple[dict[str, PhaseType] | None, list[Problem]]:
    """種類を読む。ファイルが無ければ None（種類を使わない）。壊れていれば None と苦情。

    `refs` を False にすると `overlap` / `requires` が指す先の確認を飛ばす。層の
    ファイルを単独で読むときに使う。層は共通層の種類を指してよく（設計 §25.4.1）、
    その相手はファイルの中に居ないので、1 本だけで確かめると必ず落ちる。確かめる
    のは合成したあと（`merge`）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None, []
    except OSError as exc:
        return None, [Problem(SEVERITY_ERROR, "(phases)", f"{path} を読めない ({exc})")]
    return parse(text, path, refs)


def parse(
    text: str, where: str = "(phases)", refs: bool = True
) -> tuple[dict[str, PhaseType] | None, list[Problem]]:
    problems: list[Problem] = []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [Problem(SEVERITY_ERROR, where, f"YAML として読めない: {exc}")]
    if not isinstance(data, dict):
        return None, [Problem(SEVERITY_ERROR, where, "最上位が辞書ではない")]
    if data.get("version") != VERSION:
        return None, [
            Problem(
                SEVERITY_ERROR,
                where,
                f"版 {data.get('version')!r} は扱えない（このビルドが読むのは {VERSION}）",
            )
        ]
    raw = data.get("phases")
    if not isinstance(raw, dict) or not raw:
        return None, [Problem(SEVERITY_ERROR, where, "`phases` が辞書として無い")]

    types: dict[str, PhaseType] = {}
    titles: dict[str, str] = {}
    for key, body in raw.items():
        ident = str(key).strip()
        if rules.ID_SEPARATOR in ident:
            # 層の名前を添えた形（`lib:build`）と見分けが付かない。共通層に書けば
            # lib の定義に見え、記録を読んだ人がどのファイルを直すのか決められない。
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    ident,
                    f"識別子に `{rules.ID_SEPARATOR}` は書けない。"
                    f"層の名前を添えた形（`self{rules.ID_SEPARATOR}id` / "
                    f"`<プロジェクト名>{rules.ID_SEPARATOR}id`）と見分けが付かない",
                )
            )
            continue
        if not _ID.match(ident):
            problems.append(Problem(SEVERITY_ERROR, ident, "識別子に使えない文字がある"))
            continue
        if not isinstance(body, dict):
            problems.append(Problem(SEVERITY_ERROR, ident, "種類の中身が辞書ではない"))
            continue
        pt, own = _one(ident, body)
        problems.extend(own)
        if pt is None:
            continue
        # 表示名も一意。人は表示名で見るので、同じ名前が 2 つあると承認画面で見分けられない。
        if pt.title in titles:
            problems.append(
                Problem(
                    SEVERITY_ERROR, ident, f"表示名 `{pt.title}` が `{titles[pt.title]}` と重なる"
                )
            )
            continue
        titles[pt.title] = ident
        types[ident] = pt

    if refs:
        problems.extend(reference_problems(types.values(), types))
    if any(p.severity == SEVERITY_ERROR for p in problems):
        return None, problems
    return types, problems


def reference_problems(checked, pool: dict[str, PhaseType]) -> list[Problem]:
    """`overlap` / `requires` が指す先が、その集合の中に居るか。

    見るのは `checked` の側だけで、居てよい先は `pool` 全部。層の種類が共通層の
    種類を指す形（設計 §25.4.1）は、合成した集合を `pool` に渡せばそのまま通る。
    """
    problems: list[Problem] = []
    for pt in checked:
        for name in pt.overlap + pt.requires:
            if name not in pool:
                problems.append(
                    Problem(
                        SEVERITY_ERROR, pt.id, f"`{name}` という種類は無い（overlap / requires）"
                    )
                )
    return problems


def mark_source(types: dict[str, PhaseType] | None, layer: str) -> None:
    """この集合の種類が、どの層から来たかを名乗らせる。記録の `source` になる。"""
    for pt in (types or {}).values():
        pt.source = layer


def merge(
    common: dict[str, PhaseType] | None, extra: dict[str, PhaseType] | None, layer: str
) -> tuple[dict[str, PhaseType], list[Problem]]:
    """共通層の種類に、行き先の層の種類を id ごとに足す（設計 §25.4.1）。

    足すだけで、後ろの層が前の層を上書きすることはない。同 `id` で全欄が一致する
    ものは写しとみなして後ろを捨て（info）、中身が違えば error。`title` の重なりも
    層をまたいで error（人は表示名で見るので、承認画面で見分けられない）。

    error があるとき、その層は空として扱い、共通層の種類だけを返す。衝突した片方を
    黙って採ると、どちらの `review:` が効いているかを人が読めない。止まる側に倒す。

    `overlap` / `requires` が指す先は合成後の集合で確かめる。層から共通層の種類を
    指すのは正しい形なので、層 1 本の中では確かめられない。
    """
    base = dict(common or {})
    problems: list[Problem] = []
    titles = {pt.title: ident for ident, pt in base.items()}
    added: dict[str, PhaseType] = {}
    for ident, pt in (extra or {}).items():
        pt.source = layer
        prior = base.get(ident)
        if prior is not None:
            if prior.key() == pt.key():
                problems.append(
                    Problem(
                        SEVERITY_INFO,
                        ident,
                        f"`{ident}` は前の層と全欄が同じなので、{layer} の側を捨てた。"
                        "判定は前の層の 1 本で行う",
                    )
                )
            else:
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        ident,
                        f"`{ident}` は前の層（{prior.source or 'common'}）と同じ id で中身が違う。"
                        f"{layer} の層は空として扱う。どちらの `review:` が効いているかを"
                        "人が読めないので、片方を黙って採らない",
                    )
                )
            continue
        if pt.title in titles:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    ident,
                    f"表示名 `{pt.title}` が `{titles[pt.title]}` と重なる。"
                    f"{layer} の層は空として扱う",
                )
            )
            continue
        titles[pt.title] = ident
        added[ident] = pt
    merged = dict(base)
    merged.update(added)
    problems.extend(reference_problems(added.values(), merged))
    if any(p.severity == SEVERITY_ERROR for p in problems):
        return base, problems
    return merged, problems


def _one(ident: str, body: dict) -> tuple[PhaseType | None, list[Problem]]:
    problems: list[Problem] = []
    pt = PhaseType(id=ident)

    pt.title = str(body.get("title") or "").strip() or ident
    kind = str(body.get("kind") or KIND_WORK).strip()
    if kind not in KINDS:
        problems.append(Problem(SEVERITY_ERROR, ident, f"`kind` は {' か '.join(KINDS)}"))
        return None, problems
    pt.kind = kind
    review = str(body.get("review") or REVIEW_MR).strip()
    if review not in REVIEWS:
        problems.append(Problem(SEVERITY_ERROR, ident, f"`review` は {' か '.join(REVIEWS)}"))
        return None, problems
    pt.review = review
    if kind == KIND_FEEDBACK and review != REVIEW_MR:
        # フィードバック対応の結果を人が見ない道は作らない。
        problems.append(
            Problem(
                SEVERITY_ERROR, ident, "フィードバック対応の種類は `review: mr` でなければならない"
            )
        )
        return None, problems

    scope = body.get("scope", INHERIT)
    if scope == INHERIT or scope is None:
        pt.scope = None
    elif isinstance(scope, list):
        entries, bad = _globs(ident, "scope", scope)
        problems.extend(bad)
        if bad:
            return None, problems
        pt.scope = entries
        pt.scope_globs = [e.glob for e in entries]
    else:
        problems.append(Problem(SEVERITY_ERROR, ident, "`scope` は glob の並びか `inherit`"))
        return None, problems

    for key in ("deliverables", "overlap", "requires"):
        raw = body.get(key)
        if raw is None:
            continue
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            problems.append(Problem(SEVERITY_ERROR, ident, f"`{key}` は文字列の並び"))
            return None, problems
        setattr(pt, key, [x.strip() for x in raw if x.strip()])
    for glob in pt.deliverables:
        if ".." in glob or os.path.isabs(glob):
            problems.append(
                Problem(SEVERITY_ERROR, ident, f"`deliverables` の `{glob}` は作業ツリーの中で書く")
            )
            return None, problems
    if ident in pt.overlap or ident in pt.requires:
        problems.append(Problem(SEVERITY_WARN, ident, "自分自身を overlap / requires に挙げている"))

    pt.agent = str(body.get("agent") or "").strip()
    pt.when = str(body.get("when") or "").strip()
    return pt, problems


def _globs(ident: str, key: str, raw: list) -> tuple[list[ticket_mod.Entry], list[Problem]]:
    """範囲の glob を、子チケットの範囲と同じ規則で式にする。"""
    problems: list[Problem] = []
    entries: list[ticket_mod.Entry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            problems.append(Problem(SEVERITY_ERROR, ident, f"`{key}[{i}]` が文字列ではない"))
            continue
        glob = item.strip()
        if ".." in glob or "~" in glob or "$" in glob or os.path.isabs(glob):
            problems.append(
                Problem(SEVERITY_ERROR, ident, f"`{key}[{i}]` の `{glob}` は作業ツリーの中で書く")
            )
            continue
        glob = glob.replace("\\", "/").strip("/")
        # 子チケットの範囲と同じく、大文字小文字は区別しない（`ticket.entries`）。
        # 機械ごとに変えると、同じ提案が Linux では「種類の上限を超えている」で
        # 承認を拒まれ、Windows では通る。範囲は人が宣言する意図なので、機械の
        # 都合ではなく綴りの意味で読む。子 ⊆ 種類 ⊆ 親 の 3 つを 1 つの規則で揃える。
        try:
            compiled = re.compile("^" + globmatch.translate(glob), re.IGNORECASE)
        except re.error as exc:
            problems.append(Problem(SEVERITY_ERROR, ident, f"`{key}[{i}]` を式にできない: {exc}"))
            continue
        entries.append(ticket_mod.Entry(decision=rules.ALLOW, glob=glob, compiled=compiled))
    return entries, problems


def scope_problems(child: ticket_mod.Ticket, pt: PhaseType) -> list[Problem]:
    """子の範囲が種類の上限を超えている項を名指しする。子 ⊆ 種類。"""
    if pt.inherits_scope:
        return []
    problems: list[Problem] = []
    for entry in child.entries:
        if entry.decision == rules.DENY:
            continue
        if entry.regex:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    child.ticket,
                    f"子の範囲に regex `{entry.regex}` は書けない。"
                    "種類の上限に入るかを確かめられない",
                )
            )
            continue
        probe = entry.prefix() + "x" if entry.glob != entry.prefix() else entry.glob
        if pt.decide(probe) != rules.ALLOW:
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    child.ticket,
                    f"`{entry.glob}` は種類 {pt.title}（{pt.id}）の範囲 "
                    f"{', '.join(pt.scope_globs)} を超えている",
                )
            )
    return problems

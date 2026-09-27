"""フェーズの種類。`.ccnavi/common/phases.yml` を読む（設計 9.7）。

## 種類は人が持つ

エージェントが種類を書けると、レビュー不要の種類を作ってから使える。だから置き場は
ルールの `guard-ccnavi-config` の内側で、ワークツリー側の設定も含めてエージェントの Write は
止まる。組み込みの既定は持たない。
既定を組み込むと、意図せずレビューの要否が決まる。ファイルが無ければ、フェーズは
番号だけの今までの挙動で、親の `plan` も読めない。

## 用語

**フェーズの種類**はここで定義される名前付きの型。**フェーズ**は親の計画に並ぶ番号付きの
実体で、子が `phase: N` で指す。N 番目が何の種類かは親の計画が言う。

## 書式

    version: 1
    order: dag                # sequential（既定）| dag。全体計画の待ち方
    phases:
      research:
        kind: work            # work | feedback
        title: 調査
        review: none          # none | chat | mr
        scope: ["wip/research/*"]   # 子の範囲の上限。inherit なら親の範囲
        deliverables: ["wip/research/summary.md"]
        overlap: [design]     # 並行してよい種類（対称）
        requires: [design]    # 計画に置くなら一緒に要る種類
        after: [design]       # order: dag のとき、先に閉じてレビューが済んでいるべき種類
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
# chat は、このセッションで人が差分を見る。ホストへは出ない。先へ進めるのは
# 端末から打つ `ccnavi --reviewed <N> --chat` で、エージェントには打てない
# （DENY_TICKET_APPROVAL_CLI）。mr はホストのマージリクエストで見る（設計 9.8）。
REVIEW_CHAT = "chat"
REVIEW_MR = "mr"
REVIEWS = (REVIEW_NONE, REVIEW_CHAT, REVIEW_MR)

# 見る場所の強さ。厳しい側を採るときに使う（none < chat < mr）。
REVIEW_RANK = {REVIEW_NONE: 0, REVIEW_CHAT: 1, REVIEW_MR: 2}


def stricter(a: str, b: str) -> str:
    """見る場所の厳しい側。どちらかが知らない綴りなら mr として扱う。"""
    if a not in REVIEW_RANK or b not in REVIEW_RANK:
        return REVIEW_MR
    return a if REVIEW_RANK[a] >= REVIEW_RANK[b] else b


# 全体計画の待ち方（設計 9.7、ADR-0078）。sequential は一直線、dag は種類の `after` を辺にする。
ORDER_SEQUENTIAL = "sequential"
ORDER_DAG = "dag"
ORDERS = (ORDER_SEQUENTIAL, ORDER_DAG)


class PhaseTypes(dict):
    """種類の集合。id → 種類の辞書に、ファイルの頭の `order` を持たせたもの。"""

    def __init__(self, *args, order: str = ORDER_SEQUENTIAL, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order

    def ancestors(self, ident: str) -> set[str]:
        """`after` を推移的に辿った祖先の id。自分は含めない。循環していても止まる。"""
        found: set[str] = set()
        stack = list(self[ident].after) if ident in self else []
        while stack:
            name = stack.pop()
            if name in found or name not in self:
                continue
            found.add(name)
            stack.extend(self[name].after)
        found.discard(ident)
        return found


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
    after: list[str] = field(default_factory=list)
    agent: str = ""
    when: str = ""
    # source はこの種類が書いてある層の名前（`common` / `self` / プロジェクト名）。
    # id は裸のままで、層は記録と `--explain` の欄に出す（設計 11.4.1）。
    source: str = ""

    @property
    def inherits_scope(self) -> bool:
        return self.scope is None

    def key(self) -> tuple:
        """層をまたいで「同じ定義か」を比べるための全欄。`source` は含めない。

        含めると、中身は同じで出どころの層だけが違う定義が衝突扱いになる。比べたいのは
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
            tuple(self.after),
            self.agent,
            self.when,
        )

    def decide(self, rel: str) -> str:
        """この種類の範囲が、ワークツリーのルートからの相対パスをどう扱うか。inherit なら常に中。"""
        if self.scope is None:
            return rules.ALLOW
        for entry in self.scope:
            if entry.matches(rel):
                return rules.ALLOW
        return ticket_mod.OUTSIDE

    def overlaps(self, other: PhaseType) -> bool:
        """並行してよい組か。どちらかが相手を挙げていれば対称に効く。"""
        return other.id in self.overlap or self.id in other.overlap


def load(path: str, refs: bool = True) -> tuple[PhaseTypes | None, list[Problem]]:
    """種類を読む。ファイルが無ければ None（種類を使わない）。壊れていれば None と苦情。

    `refs` を False にすると `overlap` / `requires` / `after` が指す先の確認を飛ばす。層の
    ファイルを単独で読むときに使う。層は共通層の種類を指してよく（設計 11.4.1）、
    その相手はファイルの中に居ないので、1 本だけで確かめると必ず落ちる。確かめる
    のは合成したあと（`merge`）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None, []
    except (OSError, ValueError) as exc:
        # UTF-8 として読めない（UnicodeDecodeError は ValueError の側）ものも、壊れた
        # ファイルとして苦情付きで返す。上げると、判定（実行前・レビューで止めるところ・
        # 実行後の監視）が例外で落ち、読めない種類を「種類では切り詰めない」として扱う道に
        # 届かない。
        return None, [Problem(SEVERITY_ERROR, "(phases)", f"{path} を読めない ({exc})")]
    return parse(text, path, refs)


def parse(
    text: str, where: str = "(phases)", refs: bool = True
) -> tuple[PhaseTypes | None, list[Problem]]:
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
    order = str(data.get("order") or ORDER_SEQUENTIAL).strip()
    if order not in ORDERS:
        return None, [Problem(SEVERITY_ERROR, where, f"`order` は {' か '.join(ORDERS)}")]

    types = PhaseTypes(order=order)
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
        problems.extend(cycle_problems(types))
        problems.extend(conflict_problems(types))
    if any(p.severity == SEVERITY_ERROR for p in problems):
        return None, problems
    return types, problems


def reference_problems(checked, pool: dict[str, PhaseType]) -> list[Problem]:
    """`overlap` / `requires` / `after` が指す先が、その集合の中に居るか。

    `after` の先は `kind: work` の種類でなければならない。

    見るのは `checked` の側だけで、居てよい先は `pool` 全部。層の種類が共通層の
    種類を指す形（設計 11.4.1）は、合成した集合を `pool` に渡せばそのまま通る。
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
        for name in pt.after:
            target = pool.get(name)
            if target is None:
                problems.append(
                    Problem(SEVERITY_ERROR, pt.id, f"`{name}` という種類は無い（after）")
                )
            elif target.kind != KIND_WORK:
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        pt.id,
                        f"`after` の `{name}` は kind `{target.kind}`。"
                        f"指せるのは `{KIND_WORK}` だけ",
                    )
                )
    return problems


def conflict_problems(pool: dict[str, PhaseType]) -> list[Problem]:
    """同じ組を `after`（待つ）と `overlap`（並行してよい）の両方に挙げていないか。

    両方あると、待ち方の計算は `overlap` を採って待たず、書いた依存が黙って消える。
    どちらのつもりかを人に決めさせる。
    """
    problems: list[Problem] = []
    for pt in pool.values():
        for name in pt.after:
            other = pool.get(name)
            if other is not None and pt.overlaps(other):
                problems.append(
                    Problem(
                        SEVERITY_ERROR,
                        pt.id,
                        f"`{name}` を after と overlap の両方に挙げている。"
                        "待つか並行かを 1 つにする",
                    )
                )
    return problems


def cycle_problems(pool: dict[str, PhaseType]) -> list[Problem]:
    """`after` の循環。循環のある集合は、祖先が決まらないので読まない。"""
    problems: list[Problem] = []
    state: dict[str, int] = {}  # 1 = 辿っている途中、2 = 済み
    for start in sorted(pool):
        if state.get(start):
            continue
        path: list[str] = []
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            ident, i = stack.pop()
            if i == 0:
                state[ident] = 1
                path.append(ident)
            after = [a for a in pool[ident].after if a in pool]
            if i < len(after):
                stack.append((ident, i + 1))
                nxt = after[i]
                if state.get(nxt) == 1:
                    loop = path[path.index(nxt) :] + [nxt]
                    problems.append(
                        Problem(
                            SEVERITY_ERROR, nxt, f"`after` が循環している（{' → '.join(loop)}）"
                        )
                    )
                elif not state.get(nxt):
                    stack.append((nxt, 0))
                continue
            state[ident] = 2
            path.pop()
    return problems


def mark_source(types: dict[str, PhaseType] | None, layer: str) -> None:
    """この集合の種類が、どの層から来たかを名乗らせる。記録の `source` になる。"""
    for pt in (types or {}).values():
        pt.source = layer


def merged_order(*layers: PhaseTypes | None) -> str:
    """層を合わせた `order`。ファイルを持つ層が全部 `dag` と書いたときだけ `dag`（設計 9.7）。"""
    present = [t.order for t in layers if t is not None]
    if present and all(o == ORDER_DAG for o in present):
        return ORDER_DAG
    return ORDER_SEQUENTIAL


def merge(
    common: PhaseTypes | None, extra: PhaseTypes | None, layer: str
) -> tuple[PhaseTypes, list[Problem]]:
    """共通層の種類に、行き先の層の種類を id ごとに足す（設計 11.4.1）。

    足すだけで、後ろの層が前の層を上書きすることはない。同 `id` で全欄が一致する
    ものは重複とみなして後ろを捨て（info）、中身が違えば error。`title` の重なりも
    層をまたいで error（人は表示名で見るので、承認画面で見分けられない）。

    error があるとき、その層は空として扱い、共通層の種類だけを返す。衝突した片方を
    黙って採ると、どちらの `review:` が効いているかを人が読めない。止まる側を採る。

    `overlap` / `requires` / `after` が指す先は合成後の集合で確かめる。層から共通層の種類を
    指すのは正しい形なので、層 1 本の中では確かめられない。

    `order` は、ファイルを持つ層が全部 `dag` と書いたときだけ `dag`（`merged_order`）。
    食い違いは warn。プロジェクトの層 1 本で緩む側へ切り替えられないようにする。
    """
    order = merged_order(common, extra)
    base = PhaseTypes(common or {}, order=order)
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
    if common is not None and extra is not None and common.order != extra.order:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(phases)",
                f"`order` が層で食い違う（共通層 {common.order}、{layer} {extra.order}）。"
                f"`{ORDER_SEQUENTIAL}` で待たせる",
            )
        )
    merged = PhaseTypes(base, order=order)
    merged.update(added)
    problems.extend(reference_problems(added.values(), merged))
    problems.extend(cycle_problems(merged))
    problems.extend(conflict_problems(merged))
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
    if kind == KIND_FEEDBACK and review == REVIEW_NONE:
        # フィードバック対応の結果を人が見ない道は作らない。見る場所は chat でも mr でもよい。
        problems.append(
            Problem(
                SEVERITY_ERROR,
                ident,
                f"フィードバック対応の種類に `review: {REVIEW_NONE}` は書けない"
                f"（`{REVIEW_CHAT}` か `{REVIEW_MR}`）",
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

    for key in ("deliverables", "overlap", "requires", "after"):
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
                Problem(
                    SEVERITY_ERROR, ident, f"`deliverables` の `{glob}` はワークツリーの中で書く"
                )
            )
            return None, problems
    if ident in pt.overlap or ident in pt.requires:
        problems.append(Problem(SEVERITY_WARN, ident, "自分自身を overlap / requires に挙げている"))
    if pt.after and kind != KIND_WORK:
        problems.append(
            Problem(SEVERITY_ERROR, ident, f"`after` を持てるのは kind `{KIND_WORK}` の種類だけ")
        )
        return None, problems

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
                Problem(SEVERITY_ERROR, ident, f"`{key}[{i}]` の `{glob}` はワークツリーの中で書く")
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
    """子の範囲が種類の上限を超えている項を名指しする。子 ⊆ 種類。

    超えていても承認は止めない（warn）。判定が種類の上限でも切り詰める（phase.scope_verdict）。
    """
    if pt.inherits_scope:
        return []
    problems: list[Problem] = []
    for entry in child.entries:
        if entry.decision == rules.DENY:
            continue
        if entry.regex:
            problems.append(
                Problem(SEVERITY_WARN, child.ticket, ticket_mod.regex_overflow_detail(entry.regex))
            )
            continue
        probe = entry.prefix() + "x" if entry.glob != entry.prefix() else entry.glob
        if pt.decide(probe) != rules.ALLOW:
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    child.ticket,
                    f"`{entry.glob}` は種類 {pt.title} / {pt.id} の範囲 "
                    f"{', '.join(pt.scope_globs)} を超えている",
                )
            )
    return problems

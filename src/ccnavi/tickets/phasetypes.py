"""フェーズ定義。親の `project:` が指す config の `phases.yml` を 1 本だけ読む（設計 9.7）。

## 定義はユーザが持つ

エージェントが定義を書けると、レビュー不要の定義を作ってから使える。だから置き場は
ルールの `guard-ccnavi-config` の内側で、ワークツリー側の設定も含めてエージェントの Write は
止まる。組み込みの既定は持たない。
既定を組み込むと、意図せずレビューの要否が決まる。ファイルが無ければ、フェーズは
番号だけの今までの挙動で、親の `plan` も読めない。

## 用語

フェーズ定義はここに書く名前付きの型。フェーズは親の計画に並ぶ番号付きの
実体で、子が `phase: N` で指す。N 番目が何の定義かは親の計画が言う。

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
        overlap: [design]     # 並行してよい定義（対称）
        requires: [design]    # 計画に置くなら一緒に要る定義
        after: [design]       # order: dag のとき、先に閉じてレビューが済んでいるべき定義
        agent: explorer       # 案内にだけ使う
        when: 既存の振る舞いが分からないとき   # 案内にだけ使う
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml

from ..infra import fsio, globmatch, settings, tree, yamlread
from ..policy import rules
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from . import ticket as ticket_mod
from . import ticket_model

VERSION = 1

KIND_WORK = "work"
KIND_FEEDBACK = "feedback"
KINDS = (KIND_WORK, KIND_FEEDBACK)

REVIEW_NONE = "none"
# chat は、このセッションでユーザが差分を見る。ホストへは出ない。先へ進めるのは
# 端末から打つ `ccnavi-review.sh chat <N>`（中身は `ccnavi --reviewed <N> --chat`）で、
# エージェントには打てない
# （DENY_TICKET_APPROVAL_CLI）。mr はホストのマージリクエストで見る（設計 9.8）。
REVIEW_CHAT = "chat"
REVIEW_MR = "mr"
REVIEWS = (REVIEW_NONE, REVIEW_CHAT, REVIEW_MR)

# 見る場所の強さ。厳しい側を採るときに使う（none < chat < mr）。
REVIEW_RANK = {REVIEW_NONE: 0, REVIEW_CHAT: 1, REVIEW_MR: 2}


def stricter(a: str, b: str) -> str:
    """見る場所の厳しい側。どちらかが知らない表記なら mr として扱う。"""
    if a not in REVIEW_RANK or b not in REVIEW_RANK:
        return REVIEW_MR
    return a if REVIEW_RANK[a] >= REVIEW_RANK[b] else b


# 全体計画の待ち方（設計 9.7）。sequential は一直線、dag は定義の `after` を辺にする。
ORDER_SEQUENTIAL = "sequential"
ORDER_DAG = "dag"
ORDERS = (ORDER_SEQUENTIAL, ORDER_DAG)


class PhaseTypes(dict):
    """定義の集合。id → 定義の辞書に、ファイルの頭の `order` を持たせたもの。"""

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


# 定義の範囲が「親の範囲そのまま」であることを言う表記。
INHERIT = "inherit"

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class PhaseType:
    id: str
    title: str = ""
    kind: str = KIND_WORK
    review: str = REVIEW_MR
    # None なら inherit（親の範囲そのまま）。
    scope: list[ticket_model.Entry] | None = None
    scope_globs: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    overlap: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    agent: str = ""
    when: str = ""
    # source はこの定義が書いてあるレイヤーの名前（`common` / `self` / プロジェクト名）。
    # id は裸のままで、レイヤーは記録と `--explain` の欄に出す（設計 11.4.1）。
    source: str = ""

    @property
    def inherits_scope(self) -> bool:
        return self.scope is None

    def key(self) -> tuple:
        """レイヤーをまたいで「同じ定義か」を比べるための全欄。`source` は含めない。

        含めると、中身は同じで出どころのレイヤーだけが違う定義が衝突扱いになる。比べたいのは
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
        """この定義の範囲が、ワークツリーのルートからの相対パスをどう扱うか。inherit なら常に中。"""
        if self.scope is None:
            return rules.ALLOW
        for entry in self.scope:
            if entry.matches(rel):
                return rules.ALLOW
        return ticket_model.OUTSIDE

    def overlaps(self, other: PhaseType) -> bool:
        """並行してよい組か。どちらかが相手を挙げていれば対称に当てはまる。"""
        return other.id in self.overlap or self.id in other.overlap


def load(path: str, refs: bool = True) -> tuple[PhaseTypes | None, list[Problem]]:
    """定義を読む。ファイルが無ければ None（定義を使わない）。破損していれば None と苦情。

    `refs` を False にすると `overlap` / `requires` / `after` が指す先の確認を飛ばす。レイヤーの
    ファイルを単独で読むときに使う。レイヤーは共通レイヤーの定義を指してよく（設計 11.4.1）、
    その相手はファイルの中に無いので、1 本だけで確かめると必ず失敗する。確かめる
    のは合成したあと（`merge`）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        fsio.note_read(path, None)
        return None, []
    except (OSError, ValueError) as exc:
        # UTF-8 として読めない（UnicodeDecodeError は ValueError の側）ものも、破損した
        # ファイルとして苦情付きで返す。上げると、判定（実行前・レビューで止めるところ・
        # 実行後チェック）が例外で異常終了し、読めない定義を「定義では切り詰めない」として
        # 扱う処理まで進まない。
        return None, [Problem(SEVERITY_ERROR, "(phases)", f"{path} を読めない ({exc})")]
    # 承認のダイジェスト（read_set）に入れる。定義は待ち方と止め方を決める判定の入力。
    fsio.note_read(path, text)
    return parse(text, path, refs)


def parse(
    text: str, where: str = "(phases)", refs: bool = True
) -> tuple[PhaseTypes | None, list[Problem]]:
    problems: list[Problem] = []
    try:
        data = yamlread.safe_load(text)
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
        return None, [Problem(SEVERITY_ERROR, where, "`phases` が無いか空か、辞書ではない")]
    order = str(data.get("order") or ORDER_SEQUENTIAL).strip()
    if order not in ORDERS:
        return None, [Problem(SEVERITY_ERROR, where, f"`order` は {' か '.join(ORDERS)}")]

    types = PhaseTypes(order=order)
    titles: dict[str, str] = {}
    for key, body in raw.items():
        ident = str(key).strip()
        if rules.ID_SEPARATOR in ident:
            # レイヤーの名前をつけた形（`lib:build`）と見分けが付かない。共通レイヤーに書けば
            # lib の定義に見え、記録を読んだユーザがどのファイルを直すのか決められない。
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    ident,
                    f"識別子に `{rules.ID_SEPARATOR}` は書けない。"
                    f"レイヤーの名前を添えた形（`self{rules.ID_SEPARATOR}id` / "
                    f"`<プロジェクト名>{rules.ID_SEPARATOR}id`）と見分けが付かない",
                )
            )
            continue
        if not _ID.match(ident):
            problems.append(Problem(SEVERITY_ERROR, ident, "識別子に使えない文字がある"))
            continue
        if not isinstance(body, dict):
            problems.append(Problem(SEVERITY_ERROR, ident, "定義の中身が辞書ではない"))
            continue
        pt, own = _one(ident, body)
        problems.extend(own)
        if pt is None:
            continue
        # 表示名も一意。ユーザは表示名で見るので、同じ名前が 2 つあると承認画面で見分けられない。
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
    """`overlap` / `requires` / `after` が指す先が、その集合の中にあるか。

    `after` の先は `kind: work` の定義でなければならない。

    見るのは `checked` の側だけで、あってよい先は `pool` 全部。レイヤーの定義が共通レイヤーの
    定義を指す形（設計 11.4.1）は、合成した集合を `pool` に渡せばそのまま通る。
    """
    problems: list[Problem] = []
    for pt in checked:
        for name in pt.overlap + pt.requires:
            if name not in pool:
                problems.append(
                    Problem(
                        SEVERITY_ERROR, pt.id, f"`{name}` という定義は無い（overlap / requires）"
                    )
                )
        for name in pt.after:
            target = pool.get(name)
            if target is None:
                problems.append(
                    Problem(SEVERITY_ERROR, pt.id, f"`{name}` という定義は無い（after）")
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

    両方あると、待ち方の計算は `overlap` を採って待たず、書いた依存が気づかないうちに消える。
    どちらのつもりかをユーザに決めさせる。
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
                        "待つか並行かを 1 つにしてください",
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
    """この集合の定義に、どのレイヤーから来たかを持たせる。記録の `source` になる。"""
    for pt in (types or {}).values():
        pt.source = layer


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
        # フィードバック対応の結果をユーザが見ない経路は作らない。見る場所は chat でも mr でもよい。
        problems.append(
            Problem(
                SEVERITY_ERROR,
                ident,
                f"フィードバック対応の定義に `review: {REVIEW_NONE}` は書けない"
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
        problems.append(Problem(SEVERITY_ERROR, ident, "`scope` は glob のリストか `inherit`"))
        return None, problems

    for key in ("deliverables", "overlap", "requires", "after"):
        raw = body.get(key)
        if raw is None:
            continue
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            problems.append(Problem(SEVERITY_ERROR, ident, f"`{key}` は文字列のリスト"))
            return None, problems
        setattr(pt, key, [x.strip() for x in raw if x.strip()])
    for glob in pt.deliverables:
        if ".." in glob or os.path.isabs(glob):
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    ident,
                    f"`deliverables` の `{glob}` はワークツリーの中を指す形で書く",
                )
            )
            return None, problems
    if ident in pt.overlap or ident in pt.requires:
        problems.append(Problem(SEVERITY_WARN, ident, "自分自身を overlap / requires に挙げている"))
    if pt.after and kind != KIND_WORK:
        problems.append(
            Problem(SEVERITY_ERROR, ident, f"`after` を持てるのは kind `{KIND_WORK}` の定義だけ")
        )
        return None, problems

    pt.agent = str(body.get("agent") or "").strip()
    pt.when = str(body.get("when") or "").strip()
    return pt, problems


def _globs(ident: str, key: str, raw: list) -> tuple[list[ticket_model.Entry], list[Problem]]:
    """範囲の glob を、子チケットの範囲と同じ規則で式にする。"""
    problems: list[Problem] = []
    entries: list[ticket_model.Entry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            problems.append(Problem(SEVERITY_ERROR, ident, f"`{key}[{i}]` が文字列ではない"))
            continue
        glob = item.strip()
        if ".." in glob or "~" in glob or "$" in glob or os.path.isabs(glob):
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    ident,
                    f"`{key}[{i}]` の `{glob}` はワークツリーの中を指す形で書く",
                )
            )
            continue
        glob = glob.replace("\\", "/").strip("/")
        # 子チケットの範囲と同じく、大文字小文字は区別しない（`ticket.entries`）。
        # 機械ごとに変えると、同じ提案が Linux では「定義の上限を超えている」で
        # 承認を拒まれ、Windows では通る。範囲はユーザが宣言する意図なので、機械の
        # 都合ではなく書かれたパスの意味で読む。子 ⊆ 定義 ⊆ 親 の 3 つを 1 つの規則で揃える。
        try:
            compiled = re.compile("^" + globmatch.translate(glob), re.IGNORECASE)
        except re.error as exc:
            problems.append(Problem(SEVERITY_ERROR, ident, f"`{key}[{i}]` を式にできない: {exc}"))
            continue
        entries.append(ticket_model.Entry(decision=rules.ALLOW, glob=glob, compiled=compiled))
    return entries, problems


def scope_problems(child: ticket_model.Ticket, pt: PhaseType) -> list[Problem]:
    """子の範囲が定義の上限を超えている項を名指しする。子 ⊆ 定義。

    超えていても承認は止めない（warn）。判定が定義の上限でも切り詰める（phase_scope.scope_verdict）。
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
                    f"`{entry.glob}` は定義 {pt.title} / {pt.id} の範囲 "
                    f"{', '.join(pt.scope_globs)} を超えている",
                )
            )
    return problems


# ---- レイヤーごとの定義の読み込み（設計 11.4）


def types_path(conf: settings.Settings, root: str, project: str) -> str:
    """そのプロジェクトのレイヤーの phases.yml。空の `project` はワークスペース自身のレイヤー。

    予約名（`common` / `self`）のプロジェクトはレイヤーとして数えないので、パスを持たない
    （設計 11.4）。名前で引くと `project or LAYER_SELF` がワークスペース自身のレイヤーの
    名前と一致し、そのプロジェクトの phases がワークスペースのレイヤーとして合成される。
    """
    if settings.is_reserved_layer_name(project):
        return ""
    home = tree.project_root(conf.projects, project) if project else root
    if not home:
        return ""
    return settings.layer_path(conf, home, settings.KIND_PHASES, project or settings.LAYER_SELF)


def layer_types(
    conf: settings.Settings, root: str, project: str = ""
) -> tuple[dict[str, PhaseType] | None, list[rules.Problem]]:
    """使うフェーズ定義（親の `project:` が指す config の 1 本）と、その苦情（設計 11.4.1）。

    足し算はしない。使うのは親の承認済みチケットの `project:` が指すレイヤーの `phases.yml` だけで、
    空ならワークスペース自身のレイヤー（単体 clone ではそのプロジェクトの `.ccnavi/config/`）。
    共通レイヤーの `phases.yml` は置けないので、あっても読まない（`--lint` が error で言う）。

    ファイルが無いのは正常（`None`、苦情なし）。破損していれば空として扱い（`None`）、苦情を返す。
    `id` の重複・`overlap` / `requires` / `after` の参照先・`title` の重なり・循環は、その 1 本の
    読み込みで確かめる。
    """
    path = types_path(conf, root, project)
    if not path or not os.path.exists(path):
        return None, []
    types, notes = load(path)
    mark_source(types, project or settings.LAYER_SELF)
    return types, list(notes)


def load_types(
    conf: settings.Settings, root: str = "", project: str = ""
) -> dict[str, PhaseType] | None:
    """判定が使うフェーズ定義。無いか読めなければ None（番号だけの挙動）。"""
    types, _ = layer_types(conf, root, project)
    return types

"""フェーズ定義。親の `project:` が指す config の `phases.yml` を 1 本だけ読む（設計 9.7）。

## 定義はユーザが持つ

エージェントが定義を書けると、レビュー不要の定義を作ってから使える。だから置き場は
ルールの `guard-ccnavi-config` の内側で、ワークツリー側の設定も含めてエージェントの Write は
止まる。組み込みの既定は持たない。
既定を組み込むと、意図せずレビューの要否が決まる。ファイルが無ければ、フェーズは
番号だけの今までの挙動で、親の `plan` も読めない。

## 用語

**フェーズ定義**はここに書く名前付きの型。**フェーズ**は親の計画に並ぶ番号付きの
実体で、子が `phase: N` で指す。N 番目が何の定義かは親の計画が言う。

## 書式

    version: 1
    phases:
      research:
        kind: work            # work | feedback
        title: 調査
        review: none          # none | chat | mr
        scope: ["wip/research/*"]   # 子の範囲の上限。inherit なら親の範囲
        deliverables: ["wip/research/summary.md"]
        agent: explorer       # 案内にだけ使う
        when: 既存の振る舞いが分からないとき   # 案内にだけ使う

## 承認のあとは親の写しを読む

計画を持つ親は、計画が使う定義だけの写しを frontmatter の `phases:` に持つ（`read_copy`・
`types_of`・`copy_problems`）。`phases.yml` を読むのは提案の承認の検査・`--plan-order <親>
--fill-phases`・`--lint` の食い違いの warn だけで、承認のあとの判定は写しを読む。`scope` と
`deliverables` は 20 件まで（`MAX_ENTRIES`）。

## 順序は定義に書かない

どのフェーズがどれを待つかは、親の計画の項の `after` が決める（`workflow`）。前の版の
`order`（ファイルの頭）・`after`・`overlap`・`requires`（定義の欄）が残っていれば、読まずに通し、
warn で言う（`OLD_FIELDS`）。error にしないのは、更新した直後に全部の計画の承認と `--lint` が
止まらないようにするため。
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


# 前の版の順序の欄。読まずに通し、warn で言う。順序は親の計画の項の `after` で決める。
OLD_FILE_FIELDS = ("order",)
OLD_FIELDS = ("after", "overlap", "requires")
OLD_FIELD_NOTE = "は読まない。順序は親の計画の項の `after` で決める"


class PhaseTypes(dict):
    """定義の集合。id → 定義の辞書。"""


# 定義の `scope` と `deliverables` の件数の上限。チケットの範囲と同じ。定義は親の `phases:` に
# 写すので、ここで縛らないと親の大きさが縛れない。ユーザがレビューしきれない数を並べ、その中に
# 広い範囲を紛れ込ませる手口も防ぐ。
MAX_ENTRIES = ticket_mod.MAX_SCOPE_ENTRIES

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


def load(path: str) -> tuple[PhaseTypes | None, list[Problem]]:
    """定義を読む。ファイルが無ければ None（定義を使わない）。壊れていれば None と苦情。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        fsio.note_read(path, None)
        return None, []
    except (OSError, ValueError) as exc:
        # UTF-8 として読めない（UnicodeDecodeError は ValueError の側）ものも、壊れた
        # ファイルとして苦情付きで返す。上げると、判定（実行前・レビューで止めるところ・
        # 実行後チェック）が例外で落ち、読めない定義を「定義では切り詰めない」として扱う処理まで
        # 進まない。
        return None, [Problem(SEVERITY_ERROR, "(phases)", f"{path} を読めない ({exc})")]
    # 承認のダイジェスト（read_set）に入れる。定義は待ち方と止め方を決める判定の入力。
    fsio.note_read(path, text)
    return parse(text, path)


def parse(text: str, where: str = "(phases)") -> tuple[PhaseTypes | None, list[Problem]]:
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
    for name in OLD_FILE_FIELDS:
        if name in data:
            problems.append(Problem(SEVERITY_WARN, where, f"`{name}` {OLD_FIELD_NOTE}"))

    types = _read_defs(raw, problems)

    if any(p.severity == SEVERITY_ERROR for p in problems):
        return None, problems
    return types, problems


def _read_defs(raw: dict, problems: list[Problem]) -> PhaseTypes:
    """定義の辞書（id → 欄）を読む。`phases.yml` の `phases` と、親の `phases:` の両方が使う。

    読めない定義は入れずに苦情を足す。表示名も一意にする（ユーザは表示名で見るので、同じ名前が
    2 つあると承認画面で見分けられない）。
    """
    types = PhaseTypes()
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

    return types


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
        if len(scope) > MAX_ENTRIES:
            problems.append(_too_many(ident, "scope", len(scope)))
            return None, problems
        entries, bad = _globs(ident, "scope", scope)
        problems.extend(bad)
        if bad:
            return None, problems
        pt.scope = entries
        pt.scope_globs = [e.glob for e in entries]
    else:
        problems.append(Problem(SEVERITY_ERROR, ident, "`scope` は glob のリストか `inherit`"))
        return None, problems

    for key in OLD_FIELDS:
        if key in body:
            problems.append(Problem(SEVERITY_WARN, ident, f"`{key}` {OLD_FIELD_NOTE}"))
    raw = body.get("deliverables")
    if raw is not None:
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            problems.append(Problem(SEVERITY_ERROR, ident, "`deliverables` は文字列のリスト"))
            return None, problems
        if len(raw) > MAX_ENTRIES:
            problems.append(_too_many(ident, "deliverables", len(raw)))
            return None, problems
        pt.deliverables = [x.strip() for x in raw if x.strip()]
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
    pt.agent = str(body.get("agent") or "").strip()
    pt.when = str(body.get("when") or "").strip()
    return pt, problems


def _too_many(ident: str, key: str, count: int) -> Problem:
    return Problem(
        SEVERITY_ERROR,
        ident,
        f"`{key}` が {count} 件ある（上限 {MAX_ENTRIES} 件）。まとめるか、定義を分けてください",
    )


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


# ---- 親に固定する定義（親の `phases:`）
#
# 計画を持つ親は、計画（`plan:` / `feedback:`）が使う定義**だけ**の写しを frontmatter の
# `phases:` に持つ。欄は `phases.yml` の定義と同じで、定義の id をキーにした辞書。承認したときの
# 定義が親に固定され、承認のあとの判定は写しを読み、`phases.yml` を読まない（直しても進行中の親の
# 範囲・見る場所・成果物は変わらない）。写しが `phases.yml` と同じかを確かめるのは承認
# （`agree_candidates.plan_problems`）で、手で動かした承認ではその検査は働かず写しがそのまま効く
# （承認を本物とするのは置き場で、置き場へ動かしたユーザが中身を承認したと読む）。

COPY_KEY = ticket_mod.PHASES_KEY

# 写しの欄の名前。`PhaseType.key()` の並びと同じ（`id` を除く）。
_KEY_FIELDS = ("title", "kind", "review", "scope", "deliverables", "agent", "when")


def read_copy(raw) -> tuple[PhaseTypes, list[Problem]]:
    """親の `phases:` を定義に読む。読めない定義は入れずに苦情を返す。

    読み方は `phases.yml` の定義と同じ関数（`_read_defs`）で、形の誤りも同じ文で言う。苦情の
    `rule` は定義の id（辞書でないときは `(phases)`）。読み込みでは落とさない（判定の `blocked` の
    理由にする。`copy_problems`）。
    """
    if not isinstance(raw, dict):
        return PhaseTypes(), [
            Problem(
                SEVERITY_ERROR,
                "(phases)",
                "`phases:` は定義の id をキーにした辞書で書く（欄は phases.yml の定義と同じ）",
            )
        ]
    problems: list[Problem] = []
    return _read_defs(raw, problems), problems


def types_of(t: ticket_model.Ticket) -> dict[str, PhaseType]:
    """チケットの写しの定義（id → 定義）。写しが無ければ空。

    読んだものは `t.phase_types` に持たせて使い回す（読み込みの state 層は生の値しか持たない）。
    """
    if t.phase_types is None:
        t.phase_types = read_copy(t.phases_raw)[0] if t.phases_raw is not None else PhaseTypes()
    return t.phase_types


def has_copy(t: ticket_model.Ticket) -> bool:
    """`phases:` を書いているか（空の値は書いていないとみなす）。"""
    return t.phases_raw is not None


def same(a: PhaseType, b: PhaseType) -> bool:
    """読んだ形で同じ定義か。YAML の書き方・キーの順・既定値を省いた書き方の違いは同じとみなす。"""
    return a.key() == b.key()


def differing(a: PhaseType, b: PhaseType) -> list[str]:
    """2 つの定義で値の違う欄の名前。"""
    pairs = zip(_KEY_FIELDS, a.key()[1:], b.key()[1:], strict=True)
    return [name for name, x, y in pairs if x != y]


def as_copy(pt: PhaseType) -> dict:
    """写しに書く形。読み直すと同じ定義になる欄に戻す。前の版の順序の欄は書かない。"""
    out: dict = {
        "kind": pt.kind,
        "title": pt.title,
        "review": pt.review,
        "scope": INHERIT if pt.scope is None else list(pt.scope_globs),
    }
    if pt.deliverables:
        out["deliverables"] = list(pt.deliverables)
    if pt.agent:
        out["agent"] = pt.agent
    if pt.when:
        out["when"] = pt.when
    return out


def copy_problems(t: ticket_model.Ticket) -> list[Problem]:
    """チケットだけを読んで済む、写しの誤り。判定（`approval_checks.blocking_problems`）・承認・
    `--lint` が同じ答えを引く。`phases.yml` は読まない（承認のあとに直しても進行中の親を止めない）。

    - 子に `phases:` がある（error）。子の `phase:`（番号）と名前が似ていて取り違えやすい
    - 写しの定義の形の誤り（error。`phases.yml` の定義と同じ検査）
    - 計画を持つのに `phases:` が無い、項が `phases:` に無い定義名を使う（error）
    - 定義の `kind` が置いた計画と合わない（error）
    - `phases:` にあって、どの項も使わない定義（warn。判定はどの項からも引かないので、範囲・
      見る場所・成果物は変わらない）
    """
    found: list[Problem] = []
    name = t.ticket

    def error(text: str) -> None:
        found.append(Problem(SEVERITY_ERROR, name, text))

    if t.is_child:
        if COPY_KEY in t.raw:
            error(
                "`phases:` は親だけの欄（計画が使うフェーズ定義の写し）。子は `phase:`（番号）で"
                "親の計画の項を指す。子の `phases:` は消してください"
            )
        return found
    fill = f"`ccnavi --plan-order {name} --fill-phases`"
    if has_copy(t):
        for p in read_copy(t.phases_raw)[1]:
            detail = p.detail if p.rule == "(phases)" else f"`phases:` の `{p.rule}`: {p.detail}"
            found.append(Problem(p.severity, name, detail))
    types = types_of(t)
    if t.has_plan and not has_copy(t):
        error(
            "計画（plan / feedback）があるのに、計画が使うフェーズ定義の写し `phases:` が無い。"
            f"提案なら {fill} で差し込む。承認済みチケットなら、ユーザが `phases:` を足すか、"
            "取り消して出し直す"
        )
        return found
    named = {str(k).strip() for k in t.phases_raw} if isinstance(t.phases_raw, dict) else set()
    used: set[str] = set()
    for key, items, kind in (
        ("plan", t.plan, KIND_WORK),
        ("feedback", t.feedback or [], KIND_FEEDBACK),
    ):
        for i, item in enumerate(items):
            used.add(item.type)
            pt = types.get(item.type)
            if pt is None:
                if item.type not in named:
                    error(
                        f"`{key}[{i}]` の定義 `{item.type}` が `phases:` に無い"
                        f"（計画が使う定義は `phases:` に写して書く。{fill} で差し込める）"
                    )
                continue
            if pt.kind != kind:
                where = "全体計画" if key == "plan" else "フィードバック計画"
                error(f"`{key}[{i}]` の `{item.type}` は kind `{pt.kind}`。{where}には置けない")
    for ident in types:
        if ident not in used:
            found.append(
                Problem(
                    SEVERITY_WARN,
                    name,
                    f"`phases:` の `{ident}` はどの項も使っていない（判定は読まない。"
                    f"消し忘れなら {fill} で外す）",
                )
            )
    return found


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

    ファイルが無いのは正常（`None`、苦情なし）。壊れていれば空として扱い（`None`）、苦情を返す。
    `id` の重複・`title` の重なりは、その 1 本の読み込みで確かめる。
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

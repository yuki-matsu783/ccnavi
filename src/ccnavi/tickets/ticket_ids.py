"""識別子とブランチ名の規則。識別子の字と形、issue から識別子を作る手順、親のブランチ名。

ファイルを読まない。ticket から分けた。ticket を読まない。
"""

from __future__ import annotations

import re
import unicodedata

from ..infra import settings
from . import ticket_model

# 識別子。親は 1 語、子は `<親>-<2 桁のフェーズ番号>-<2 桁のフェーズ内の連番>`。新しい親は
# `<先頭の語>-<番号>-<slug>`（`feature-12-login` など）の形にそろえる。形は lint の warn で、
# 読めるかどうかは文字と長さだけで決める（`i0055` のような前の形も読む）。
# 識別子だけから親を割り出すときは、右から 2 段（`-<2 桁>-<2 桁>`）を剥がす。親が `-` や数字を
# 含んでも（`web-i0012-05-01` の親は `web-i0012`）割り出し方は 1 通りに決まる。
#
# 使える文字は ASCII の英数字と `.` `_` `-`、それに日本語の字（ひらがな・カタカナ・長音記号・
# CJK 統合漢字・々）。先頭は ASCII の英数字。全角英数・全角記号・全角空白・ほかの Unicode は
# 使わない（見た目の同じ別の字で名前を紛らわせない。パスやシェルで意味を持つ字を入れない）。
# 表記は NFC に限る（macOS の NFD の表記は、見た目が同じでもバイト列が違う別の名前になる）。
JA_CHARS = "々ぁ-ゖァ-ヺー一-鿿"
# 文字クラスの中身（`[...]` に入れて使う）。拡張（TS）と sh はこの文字クラスをそのまま使う。
ID_CHARS = "A-Za-z0-9._\\-" + JA_CHARS
_ID = re.compile(rf"^[A-Za-z0-9][{ID_CHARS}]*\Z")
_CHILD = re.compile(
    rf"^(?P<parent>[A-Za-z0-9][{ID_CHARS}]*)-(?P<phase>[0-9]{{2}})-(?P<seq>[0-9]{{2}})\Z"
)
# 子の識別子に書けるフェーズ番号と連番の上限（どちらも 2 桁）。
MAX_CHILD_NUMBER = 99
# 親の識別子の末尾に置かないもの（`-<2 桁>`）。子の識別子の途中（`<親>-<フェーズ>`）と紛れる。
_CHILD_TAIL = re.compile(r"-[0-9]{2}\Z")
# プロジェクトの名前（`projects/` の下のディレクトリ名）。こちらは ASCII のまま。
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")

# 識別子の長さの上限（文字数。子は親 + 6 文字なので、親は実質 58 文字まで）。
# 識別子はパスに 2 回出ることがある（`.claude/worktrees/<親>/.ccnavi/approved/phases/<親>/...`）。
# Windows の MAX_PATH（260）から、ワークスペースまでのパス（40 文字ほど）と置き場のパスを
# 引いた残り。
# Linux の 1 つの名前の上限（255 バイト）も、日本語 1 字 3 バイトで 64 字なら収まる。
MAX_ID_LENGTH = 64
# issue から作る識別子と、新規の提案に勧める長さ（lint の warn）。上限との差は子のフェーズ番号・
# 連番と余裕。
SUGGESTED_ID_LENGTH = 48

# 新しい親の識別子の形。`<先頭の語>-<番号>-<slug>`。先頭の語は使えるリスト
# （`settings.branch_prefixes`。既定は feature・hotfix・fix など）にあるものだけを形に合うと読む。
# 番号は issue の番号か ccnavi の通し番号。
_FORM = re.compile(
    rf"^(?P<prefix>[a-z][a-z0-9]*)-(?P<num>[1-9][0-9]*)-"
    rf"(?P<slug>[A-Za-z0-9{JA_CHARS}][{ID_CHARS}]*)\Z"
)
# issue から始めるときの既定の先頭の語。
DEFAULT_ISSUE_PREFIX = "feature"
# 前の形（`i<番号>`・`<プロジェクト名>-i<番号>`）。読むのと、通し番号を数えるのにだけ使う。
_LEGACY_ISSUE_ID = re.compile(r"^i(?P<num>[0-9]+)\Z", re.IGNORECASE)
_LEGACY_PROJECT_ISSUE_ID = re.compile(
    r"^(?P<project>[A-Za-z0-9][A-Za-z0-9._-]*)-i(?P<num>[0-9]+)\Z", re.IGNORECASE
)
# issue のタイトルから slug を作るときに残さない字（残すのは英数字と日本語の字だけ）。
_SLUG_DROP = re.compile(rf"[^A-Za-z0-9{JA_CHARS}]+")
# タイトルから何も残らなかったときの slug。
EMPTY_SLUG = "issue"


def id_problem(text: str) -> str:
    """識別子として読めない理由。読めれば空。文字・NFC・長さを見る（形は見ない）。"""
    if not isinstance(text, str) or not text:
        return "識別子が無い"
    if unicodedata.normalize("NFC", text) != text:
        return (
            "識別子が NFC でない（macOS で濁点が分かれた NFD の表記など）。"
            "見た目が同じでも別の名前になるので NFC で書く"
        )
    if not _ID.match(text):
        return (
            "識別子に使えない文字がある（先頭は ASCII の英数字、続きは英数字・`.`・`_`・`-`・"
            "ひらがな・カタカナ・漢字）"
        )
    if len(text) > MAX_ID_LENGTH:
        return f"識別子が長すぎる（{len(text)} 文字。上限は {MAX_ID_LENGTH} 文字）"
    return ""


def is_valid_id(text: str) -> bool:
    """識別子として読めるか（`id_problem` が空か）。区切り文字と先頭の `.` を持たない。

    チケットの識別子でファイル名を組む側（history.py）や、sh から渡る引数を受ける側
    （cli_args.py・cli_ops.py）が、自分でも同じ検査を当てるために使う。
    """
    return not id_problem(text)


def is_valid_name(text: str) -> bool:
    """プロジェクトの名前の形（ASCII の 1 語）か。"""
    return bool(_NAME.match(text or ""))


def child_pattern() -> re.Pattern:
    """子の識別子の形（`<親>-<2 桁のフェーズ番号>-<2 桁の連番>`）。

    名前付きグループは `parent`・`phase`・`seq`。承認の側が次の連番を数えるのと、
    識別子だけから親を割り出すのに使う。
    """
    return _CHILD


def child_id(parent: str, phase: int, seq: int) -> str:
    """子の識別子を組む。フェーズ番号も連番も 2 桁の 0 埋め。"""
    return f"{parent}-{phase:02d}-{seq:02d}"


def child_tail_pattern() -> re.Pattern:
    """親の識別子の末尾に置かない形（`-<2 桁>`）。子の識別子の途中と紛れる。"""
    return _CHILD_TAIL


def form_of(name: str, prefixes=settings.DEFAULT_BRANCH_PREFIXES) -> re.Match | None:
    """`<先頭の語>-<番号>-<slug>` の形で、先頭の語が `prefixes` にあれば、その一致。"""
    matched = _FORM.match(name or "")
    if matched is None or matched.group("prefix") not in prefixes:
        return None
    return matched


def has_form(name: str, prefixes=settings.DEFAULT_BRANCH_PREFIXES) -> bool:
    """`<先頭の語>-<番号>-<slug>` の形か（先頭の語は `prefixes` のどれか）。"""
    return form_of(name, prefixes) is not None


def identifier_number(name: str, prefixes=settings.DEFAULT_BRANCH_PREFIXES) -> int | None:
    """親の識別子に含まれる番号。

    読むのは `<先頭の語>-<番号>-<slug>` と前の形（`i<番号>`・`<名前>-i<番号>`）だけ。
    """
    matched = form_of(name, prefixes)
    if matched:
        return int(matched.group("num"))
    for pattern in (_LEGACY_ISSUE_ID, _LEGACY_PROJECT_ISSUE_ID):
        matched = pattern.match(name or "")
        if matched:
            return int(matched.group("num"))
    return None


def next_serial(names, prefixes=settings.DEFAULT_BRANCH_PREFIXES) -> int:
    """issue の無い提案に使う、次の通し番号。番号を持つ識別子の最大 + 1。

    数えるのは親の識別子だけ（子は親の番号を持つ）。先頭の語が違っても同じ通し番号を使う。
    番号を持つものが無ければ 1。
    """
    found = []
    for name in names:
        if _CHILD.match(name or ""):
            continue
        number = identifier_number(name, prefixes)
        if number:
            found.append(number)
    return max(found, default=0) + 1


def issue_slug(title: str, room: int = SUGGESTED_ID_LENGTH) -> str:
    """issue のタイトルから作る slug。英数字と日本語の字だけを残し、ほかは `-` にまとめる。

    NFKC で全角英数を半角に、半角カナを全角にそろえ、ASCII の英字は小文字にする。
    前後の `-` を落とし、`room` 文字で切る。何も残らなければ空（呼び手が `EMPTY_SLUG` で埋める）。
    """
    text = unicodedata.normalize("NFKC", title or "")
    text = "".join(c.lower() if c.isascii() else c for c in text)
    slug = _SLUG_DROP.sub("-", text).strip("-")
    return slug[: max(room, 0)].rstrip("-")


def issue_identifier(
    number: int, title: str = "", project: str = "", prefix: str = DEFAULT_ISSUE_PREFIX
) -> str:
    """issue から親の識別子を決める。

    `<prefix>-<番号>-<slug>`（既定の prefix は `feature`）。slug は issue のタイトルから作る
    （`issue_slug`）。プロジェクトの issue なら slug の頭に `<プロジェクト名>-` を付ける
    （`feature-12-web-<slug>`。issue の番号はリポジトリごとなので、ワークスペースや
    別のプロジェクトの同じ番号の issue と名前を分ける）。
    タイトルから何も残らなければ slug は `issue`。全体は `SUGGESTED_ID_LENGTH` 文字に収める。
    末尾が `-<2 桁>` になるときは、その部分を落とす（`-<2 桁>-<2 桁>` なら子の識別子そのもの、
    `-<2 桁>` だけでも子の識別子の途中の `<親>-<フェーズ番号>` と紛れる。lint の warn に当たらない
    名前にする）。
    「issue → 識別子」はこの 1 つだけで、Chrome 拡張も Pyodide の上でこれを呼ぶ。
    番号が正の整数でないか、プロジェクト名が形に合わなければ ValueError。
    """
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError(f"issue の番号は正の整数: {number!r}")
    if project and not _NAME.match(project):
        raise ValueError(f"プロジェクト名が識別子の形でない: {project!r}")
    if not isinstance(prefix, str) or not settings.is_branch_prefix(prefix):
        raise ValueError(f"先頭の語が形に合わない: {prefix!r}")
    head = f"{prefix}-{number}-" + (f"{project}-" if project else "")
    slug = issue_slug(title if isinstance(title, str) else "", SUGGESTED_ID_LENGTH - len(head))
    ident = head + slug
    while slug and _CHILD_TAIL.search(ident):
        slug = slug[:-3].rstrip("-")
        ident = head + slug
    if not slug:
        ident = head + EMPTY_SLUG
    problem = id_problem(ident)
    if problem:
        raise ValueError(f"issue から識別子を作れない: {problem}")
    return ident


# 親のブランチ名は、`branch:` が無ければ親の識別子そのもの（名前を求める関数が恒等写像なので、
# Python・sh・TS で食い違わない）。識別子を、ブランチ名として安全で、
# 統合先と紛れない形にそろえる。いまは `--lint` の warn だけで、承認は止めない。
#
# 統合先や保護されたブランチの名前（`ccnavi-git.sh` の push の拒否と同じリスト）。
# 大文字小文字を区別せずに比べる。`release/*` は識別子に `/` を書けないので、
# ここでは `release` と `release-*` だけを見る。
RESERVED_BRANCH_IDS = ("main", "master", "develop", "release")


# 親のブランチ名のキー。
BRANCH_KEY = "branch"
# ブランチ名の長さの上限（文字数）。ref はパスにもなる（`.git/refs/heads/<名前>`）ので、
# 識別子と同じく短く保つ。
MAX_BRANCH_LENGTH = 200
# 親のブランチ名に使える字。識別子の字（`ID_CHARS`）に階層の区切りの `/` を足したもの。
# 先頭は ASCII の英数字。git が許すほかの字（`$`・`;`・引用符・全角の字など）は、sh と拡張が
# 名前を扱う箇所で意味を持つか、見た目の同じ別の名前を作るので使わない（厳しくする向き）。
_BRANCH = re.compile(rf"^[A-Za-z0-9][{ID_CHARS}/]*\Z")
# 先頭の階層に置けない語（git の ref の名前とリモートの名前）。大文字小文字は区別しない。
_REF_PREFIXES = ("refs", "heads", "remotes", "tags", "origin", "upstream", "head")


def branch_problem(name: str) -> str:
    """`branch:` に書いた名前を親のブランチ名に使えない理由。使えれば空。

    字は識別子の字に `/` を足したものだけ（`_BRANCH`）。そのうえで
    `git check-ref-format --branch` の形の規則（`..`・`//`・`.` で始まる階層・`.lock` で終わる階層
    など）と、統合先や保護されたブランチの名前（main・master・develop・release・release/*・
    release-*。大文字小文字は区別しない）を断る。NFC でない表記も断る（識別子と同じ。
    見た目が同じ別の名前を作らない）。その時点の統合先の名前は、呼び手
    （`approval_checks.branch_problems`）が比べる。
    """
    if not isinstance(name, str) or not name:
        return "ブランチ名が空"
    if unicodedata.normalize("NFC", name) != name:
        return "ブランチ名が NFC でない（見た目が同じでも別の名前になるので NFC で書く）"
    if len(name) > MAX_BRANCH_LENGTH:
        return f"ブランチ名が長すぎる（{len(name)} 文字。上限は {MAX_BRANCH_LENGTH} 文字）"
    if not _BRANCH.match(name):
        return (
            "ブランチ名に使えない字がある（先頭は ASCII の英数字、続きは英数字・"
            "`.`・`_`・`-`・`/`・ひらがな・カタカナ・漢字。空白・制御文字・記号は使わない）"
        )
    if ".." in name or "//" in name or name.startswith("/") or name.endswith(("/", ".")):
        return "git のブランチ名に使えない形（`..`・`//`・先頭や末尾の `/`・末尾の `.`）"
    for part in name.split("/"):
        if part.startswith(".") or part.endswith(".lock"):
            return "git のブランチ名に使えない形（`.` で始まる階層・`.lock` で終わる階層）"
    folded = name.casefold()
    parts = folded.split("/")
    if parts[0] in _REF_PREFIXES:
        return (
            f"先頭の階層 `{name.split('/')[0]}` は git の ref の名前"
            "（refs・heads・remotes・tags）か"
            "リモートの名前（origin・upstream）と紛れる。"
            "`origin/main` のような名前は親のブランチにしない"
        )
    if any(p == "head" or p.endswith("_head") for p in parts):
        return "`HEAD`・`FETCH_HEAD` のような階層は git の特別な名前と紛れる"
    if (
        folded in RESERVED_BRANCH_IDS
        or parts[0] in RESERVED_BRANCH_IDS
        or folded.startswith("release-")
    ):
        return (
            "統合先や保護されたブランチの名前（main・master・develop・release と、"
            "それを先頭の階層に持つ main/*・release/* など、release-*）は親のブランチにしない"
        )
    return ""


def branch_name(t: ticket_model.Ticket) -> str:
    """親チケットの親のブランチ名。`branch:` があればその値、無ければ識別子。

    子のブランチは子の識別子（子は `branch:` を持たない）。
    """
    if t.is_child:
        return t.ticket
    return t.branch or t.ticket


def branch_name_problems(
    t: ticket_model.Ticket,
    integration: str = "",
    serial: int = 0,
    prefixes=settings.DEFAULT_BRANCH_PREFIXES,
) -> list[str]:
    """新規の提案の識別子が、親のブランチ名の規則に合わないところ。

    規則は、ref として安全な形（`..` を含まない、`.lock` や `.` で終わらない）、統合先や保護された
    ブランチの名前を使わない、新しい親は `<先頭の語>-<番号>-<slug>` の形で、番号は `issue:` の番号か
    ccnavi の通し番号、親は `-<2 桁>` で終わらない（`-<2 桁>-<2 桁>` で終われば子の識別子そのもの、
    `-<2 桁>` だけでも子の識別子の途中の `<親>-<フェーズ番号>` と紛れる）。

    見るのは識別子と `issue:` だけで、ファイルも git も読まない。返すのはユーザに見せる文で、
    深刻度は呼ぶ側が決める（いまは warn）。大文字小文字だけが違う識別子、子の形に当たる親の識別子、
    番号の重なりは、他のチケットと並べて見るので `lint` の側で数える。

    `integration` はその時点の統合先の名前。環境変数からは読まず、呼び手が
    渡したときだけ予約に足す。固定のリスト（main など）と同じく大文字小文字を区別せずに比べる。
    `serial` は次の通し番号（`next_serial`）。渡されたときだけ、形の warn の文に添える。
    `prefixes` は使える先頭の語のリスト（`settings.branch_prefixes`）。
    """
    name = t.ticket
    folded = name.casefold()
    found: list[str] = []
    if ".." in name:
        found.append("識別子に `..` を含む。git のブランチ名に使えない")
    if folded.endswith(".lock"):
        found.append("識別子が `.lock` で終わる。git のブランチ名に使えない")
    elif name.endswith("."):
        found.append("識別子が `.` で終わる。git のブランチ名に使えない")
    if t.is_child:
        return found
    if folded in RESERVED_BRANCH_IDS or folded.startswith("release-"):
        found.append(
            "識別子が統合先や保護されたブランチの名前（main・master・develop・release・release-*）"
            "に当たる。親のブランチ名が統合先と同じになる"
        )
    elif integration and folded == integration.casefold():
        found.append(
            f"識別子が統合先の名前（{integration}）に当たる。親のブランチ名が統合先と同じになる"
        )
    found.extend(_form_problems(t, serial, prefixes))
    return found


def _form_problems(
    t: ticket_model.Ticket, serial: int = 0, prefixes=settings.DEFAULT_BRANCH_PREFIXES
) -> list[str]:
    """新しい親の識別子が `<先頭の語>-<番号>-<slug>` の形と `issue:` に合っているか。

    - 形（先頭の語は `prefixes` のどれか）に合わなければ言う。前の形（`i<番号>`・
      `<プロジェクト名>-i<番号>`）は使わないと添える
    - `issue:`（同じリポジトリの課題）があれば、番号が issue と同じか。プロジェクトの issue なら
      slug の頭が `<プロジェクト名>-` か
    - 別のリポジトリの課題（`owner/repo#N`）は番号を比べない（その番号はこのリポジトリの issue と
      紛れるので、通し番号を使う）
    - 長さが勧める上限（`SUGGESTED_ID_LENGTH`）を超えるか
    """
    name = t.ticket
    matched = form_of(name, prefixes)
    own_issue = t.issue is not None and not t.issue_repo
    if matched is None:
        legacy = bool(_LEGACY_ISSUE_ID.match(name) or _LEGACY_PROJECT_ISSUE_ID.match(name))
        head = "`i<番号>` の形はもう使わない。" if legacy else ""
        word = "・".join(prefixes)
        shape = f"`<先頭の語>-<番号>-<slug>` の形（先頭の語は {word} のどれか）"
        if own_issue:
            want = f"feature-{t.issue}-" + (f"{t.project}-" if t.project else "") + "<slug>"
            return [
                f"{head}親の識別子は {shape} にする。"
                f"`issue: {t.issue}` から始めるなら `{want}` など"
            ]
        hint = f"次の通し番号は {serial}" if serial else "番号は既にある識別子の番号の続き"
        return [
            f"{head}親の識別子は {shape} にする。issue が無ければ ccnavi の通し番号を使う（{hint}）"
        ]
    found: list[str] = []
    number = int(matched.group("num"))
    prefix = matched.group("prefix")
    if own_issue and number != t.issue:
        found.append(
            f"識別子の番号 {number} が `issue: {t.issue}` と違う。"
            f"issue から始める識別子は `{prefix}-{t.issue}-<slug>` にする"
        )
    elif (
        own_issue
        and t.project
        and not matched.group("slug").casefold().startswith(t.project.casefold() + "-")
        and matched.group("slug").casefold() != t.project.casefold()
    ):
        found.append(
            f"プロジェクト {t.project} の issue から始める識別子は "
            f"`{prefix}-{t.issue}-{t.project}-<slug>` にする。issue の番号はリポジトリごとなので、"
            "ワークスペースや別のプロジェクトの同じ番号の issue と名前を分ける"
        )
    if len(name) > SUGGESTED_ID_LENGTH:
        found.append(
            f"識別子が {SUGGESTED_ID_LENGTH} 文字を超える（{len(name)} 文字）。"
            "ワークツリーのパスが Windows の上限（MAX_PATH）に近づくので"
            " slug を短くする"
        )
    return found

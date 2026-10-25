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
# 読めるかどうかは文字と長さだけで決める。
# 識別子だけから親を割り出すときは、右から 2 段（`-<2 桁>-<2 桁>`）を剥がす。親が `-` や数字を
# 含んでも（`web-12-05-01` の親は `web-12`）割り出し方は 1 通りに決まる。
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
# 既にあるブランチを親に採用するときの識別子の上限。子の識別子は「親 + 6 文字」で、
# 64 文字に収める。
MAX_ADOPT_ID_LENGTH = MAX_ID_LENGTH - 6

# 新しい親の識別子の形。`<先頭の語>-<番号>-<slug>`。先頭の語は使えるリスト
# （`settings.branch_prefixes`。既定は feature・hotfix・fix など）にあるものだけを形に合うと読む。
# 番号は issue の番号（通し番号は直接作業のワークツリー名だけに使う）。
_FORM = re.compile(
    rf"^(?P<prefix>[a-z][a-z0-9]*)-(?P<num>[1-9][0-9]*)-"
    rf"(?P<slug>[A-Za-z0-9{JA_CHARS}][{ID_CHARS}]*)\Z"
)
# issue から始めるときの既定の先頭の語。
DEFAULT_ISSUE_PREFIX = "feature"
# issue のタイトルから slug を作るときに残さない字（残すのは英数字と日本語の字だけ）。
_SLUG_DROP = re.compile(rf"[^A-Za-z0-9{JA_CHARS}]+")
# タイトルから何も残らなかったときの slug。
EMPTY_SLUG = "issue"


def id_problem(text: str) -> str:
    """識別子として読めない理由。読めれば空。文字・NFC・長さを見る（形は見ない）。"""
    if not isinstance(text, str) or not text:
        return "識別子がありません。"
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

    チケットの識別子でファイル名を組む側や、sh から渡る引数を受ける側
    （cli_args.py・cli_ops.py）が、自分でも同じ検査を適用するために使う。
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


def direct_name_warning(
    name: str, prefixes=settings.DEFAULT_BRANCH_PREFIXES, root: str = ""
) -> str:
    """直接作業のワークツリーの名前が形に合わないときの案内（1 行）。合えば空。

    `--lint` の warn と、`ccnavi-worktree.sh` が切る前に出す案内が同じ文面を使う。
    `root` があれば、切り方を `sh <引用したパス>/ccnavi-worktree.sh <名前>` の完全な形で出す。
    無いときだけ、これまでのスクリプト名だけの形にする。
    """
    if has_form(name, prefixes):
        return ""
    cut = (
        f"`{settings.script_command(root, 'ccnavi-worktree.sh')} <名前>`"
        if root
        else "`ccnavi-worktree.sh <名前>`"
    )
    return (
        f"直接作業のワークツリー名 {name} が `<先頭の語>-<番号>-<slug>` の形ではありません。"
        "次から、先頭の語（CCNAVI_BRANCH_PREFIXES）と番号（issue の番号、"
        "無ければ `ccnavi-ticket.sh next-id` の通し番号）を付けて "
        f"{cut} で切ってください。既にある名前は変えなくてよい"
    )


def identifier_number(name: str, prefixes=settings.DEFAULT_BRANCH_PREFIXES) -> int | None:
    """親の識別子に含まれる番号。読むのは `<先頭の語>-<番号>-<slug>` だけ。"""
    matched = form_of(name, prefixes)
    return int(matched.group("num")) if matched else None


def next_serial(
    names, prefixes=settings.DEFAULT_BRANCH_PREFIXES, skip_children: bool = True
) -> int:
    """直接作業のワークツリー名に付ける、次の通し番号。番号を持つ識別子の最大 + 1。

    数えるのは親の識別子だけ（子は親の番号を持つ）。先頭の語が違っても同じ通し番号を使う。
    番号を持つものが無ければ 1。`skip_children` が偽なら、子の形の名前も数える（手元のブランチと
    ワークツリーの名前には、親の居ない子や、末尾が子の形の名前が混ざりうる。子の番号は親と同じ）。
    """
    found = []
    for name in names:
        if skip_children and _CHILD.match(name or ""):
            continue
        number = identifier_number(name, prefixes)
        if number:
            found.append(number)
    return max(found, default=0) + 1


def issue_slug(title: str, room: int = SUGGESTED_ID_LENGTH) -> str:
    """issue のタイトルから作る slug。英数字と日本語の字だけを残し、ほかは `-` にまとめる。

    NFKC で全角英数を半角に、半角カナを全角に、ASCII の英字は小文字にそろえる。前後の `-` を除く。
    `room` 文字を超えるときは、語の区切り（`-`）で切り、入りきらない最後の語は落とす。最初の 1 語が
    `room` を超えるときだけ、その語を `room` 文字で切る。日本語は語の区切りが `-` に出ないので、
    区切りの無い長いタイトルは 1 語として扱われ、落ちるか `room` 文字で切れる（短くしたいときは、
    `ccnavi-start.sh` が `--slug` で人かエージェントに短い名前を付けさせる）。
    何も残らなければ空（呼び手が `EMPTY_SLUG` で埋める）。
    """
    text = unicodedata.normalize("NFKC", title or "")
    text = "".join(c.lower() if c.isascii() else c for c in text)
    slug = _SLUG_DROP.sub("-", text).strip("-")
    room = max(room, 0)
    if len(slug) <= room:
        return slug
    kept = ""
    for word in slug.split("-"):
        joined = f"{kept}-{word}" if kept else word
        if len(joined) > room:
            break
        kept = joined
    return (kept or slug[:room]).rstrip("-")


def issue_identifier_parts(
    number: int, title: str = "", project: str = "", prefix: str = DEFAULT_ISSUE_PREFIX
) -> tuple[str, bool]:
    """`issue_identifier` の答えと、タイトルが長くて slug を短くしたか。

    短くしたかどうかは、`issue_slug` が `room` に収めるために字を落としたかで決まる。
    末尾の `-<2 桁>` を除いただけなら偽。
    `ccnavi-start.sh` はこれが真のとき、タイトルから作った名前で進めず、短い `--slug` を付けさせる。
    タイトルから何も残らず `EMPTY_SLUG` にしたときは偽。
    """
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError(f"issue の番号は正の整数: {number!r}")
    if project and not _NAME.match(project):
        raise ValueError(f"プロジェクト名が識別子の形でない: {project!r}")
    if not isinstance(prefix, str) or not settings.is_branch_prefix(prefix):
        raise ValueError(f"先頭の語が形に合わない: {prefix!r}")
    head = f"{prefix}-{number}-" + (f"{project}-" if project else "")
    title = title if isinstance(title, str) else ""
    slug = issue_slug(title, SUGGESTED_ID_LENGTH - len(head))
    # NFKC で字数が増えるタイトルでも切られないよう、十分大きい上限で全体の slug を作る。
    full = issue_slug(title, 10**6)
    shortened = len(slug) < len(full)
    ident = head + slug
    while slug and _CHILD_TAIL.search(ident):
        slug = slug[:-3].rstrip("-")
        ident = head + slug
    if not slug:
        ident = head + EMPTY_SLUG
    problem = id_problem(ident)
    if problem:
        raise ValueError(f"issue から識別子を作れない: {problem}")
    return ident, shortened


def issue_identifier(
    number: int, title: str = "", project: str = "", prefix: str = DEFAULT_ISSUE_PREFIX
) -> str:
    """issue から親の識別子を決める。

    `<prefix>-<番号>-<slug>`（既定の prefix は `feature`）。slug は issue のタイトルから作る
    （`issue_slug`）。プロジェクトの issue なら slug の頭に `<プロジェクト名>-` を付ける
    （`feature-12-web-<slug>`。issue の番号はリポジトリごとなので、ワークスペースや
    別のプロジェクトの同じ番号の issue と名前を分ける）。
    タイトルから何も残らなければ slug は `issue`。全体は `SUGGESTED_ID_LENGTH` 文字に収め、
    超えるときは slug を語の区切り（`-`）で切る（`issue_slug`）。
    末尾が `-<2 桁>` になるときは、その部分を除く（`-<2 桁>-<2 桁>` なら子の識別子そのもの、
    `-<2 桁>` だけでも子の識別子の途中の `<親>-<フェーズ番号>` と紛れる。lint の warn にヒットしない
    名前にする）。
    「issue → 識別子」はこの 1 つだけで、Chrome 拡張も Pyodide の上でこれを呼ぶ。
    番号が正の整数でないか、プロジェクト名が形に合わなければ ValueError。
    """
    return issue_identifier_parts(number, title, project, prefix)[0]


def branch_to_id(branch: str) -> str:
    """親のブランチ名から親の識別子を作る。`/` を `-` に置き換えるだけ。

    ワークツリーは `.claude/worktrees/<識別子>` に、識別子のブランチを作らず親のブランチを直接
    チェックアウトする。この変換は Python・sh（`ccnavi_branch_to_id`）・TS（拡張の `branchToId`）に
    同じものを置き、`tests/fixtures/branch-to-id.json` の同じ表で一致を確かめる。
    """
    return branch.replace("/", "-")


def adoption_id_problem(branch: str) -> str:
    """既にあるブランチ（MR の元ブランチ）を親に採用するとき、識別子が使えない理由。使えれば空。

    識別子の字と長さ（58 文字以内。子は親 + 6 文字で 64 文字に収める）に加えて、末尾が
    `-<2 桁>` でないことを求める（子の識別子の途中と紛れる）。
    """
    ident = branch_to_id(branch)
    problem = id_problem(ident)
    if problem:
        return problem
    if len(ident) > MAX_ADOPT_ID_LENGTH:
        return (
            f"識別子が長すぎる（{len(ident)} 文字。子の識別子を収めるため"
            f"採用できるのは {MAX_ADOPT_ID_LENGTH} 文字まで）"
        )
    if _CHILD_TAIL.search(ident):
        return "識別子の末尾が `-<2 桁>` になる（子の識別子の途中と紛れる）"
    return ""


def id_answer(branch: str) -> list[str]:
    """sh の `worktree id <ブランチ>` への答え（1 行 1 項目）。

    - `id <識別子>`: ブランチ名の `/` を `-` に置き換えたもの
    - `problem <理由>`: 親のブランチとして採用できない理由（ブランチ名の字と形・識別子の字と NFC・
      58 文字・末尾の `-<2 桁>`）。使えれば出さない
    - `direct <理由>`: 直接作業や子のワークツリーの名前として使えない理由（ブランチ名の字と形・
      識別子の字と NFC・64 文字）。使えれば出さない
    - `child yes`: 子の識別子の形（`<親>-<2 桁>-<2 桁>`）

    sh は字の種類と NFC と文字数を自分で確かめない（ロケールでバイト数と字数が変わる）。ここに聞く。
    """
    ident = branch_to_id(branch)
    lines = [f"id {ident}"]
    shape = branch_problem(branch)
    problem = shape or adoption_id_problem(branch)
    if problem:
        lines.append(f"problem {problem}")
    direct = shape or id_problem(ident)
    if direct:
        lines.append(f"direct {direct}")
    if "/" not in branch and _CHILD.match(ident):
        lines.append("child yes")
    return lines


# 親の識別子は、親のブランチ名の `/` を `-` に置き換えたもの（`branch_to_id`）。新しく切る親の
# 識別子を、ブランチ名として安全で、統合先と紛れない形にそろえる。いまは `--lint` の warn だけ。
#
# 統合先や保護されたブランチの名前（`ccnavi-git.sh` の push の拒否と同じリスト）。
# 大文字小文字を区別せずに比べる。`release/*` は識別子に `/` を書けないので、
# ここでは `release` と `release-*` だけを見る。
RESERVED_BRANCH_IDS = ("main", "master", "develop", "release")


# 使えなくなった `branch:` の欄の名前。あればチケットの読み込みが error にする
# （`ticket._read_relations`）。
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
    """親のブランチ名として使えない理由。使えれば空。（`write-and-push sort` の版の検査などが使う）

    字は識別子の字に `/` を足したものだけ（`_BRANCH`）。そのうえで
    `git check-ref-format --branch` の形の規則（`..`・`//`・`.` で始まる階層・`.lock` で終わる階層
    など）と、統合先や保護されたブランチの名前（main・master・develop・release・release/*・
    release-*。大文字小文字は区別しない）を断る。NFC でない表記も断る（識別子と同じ。
    見た目が同じ別の名前を作らない）。
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
            "リモートの名前（origin・upstream）と紛らわしい。"
            "`origin/main` のような名前は親のブランチにしない"
        )
    if any(p == "head" or p.endswith("_head") for p in parts):
        return "`HEAD`・`FETCH_HEAD` のような階層は git の特別な名前と紛らわしい"
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


def branch_name_problems(
    t: ticket_model.Ticket,
    integration: str = "",
    serial: int = 0,
    prefixes=settings.DEFAULT_BRANCH_PREFIXES,
) -> list[str]:
    """新規の提案の識別子が、親のブランチ名の規則に合わないところ。

    規則は、ref として安全な形（`..` を含まない、`.lock` や `.` で終わらない）、統合先や保護された
    ブランチの名前を使わない、新しい親は `<先頭の語>-<番号>-<slug>` の形で、番号は `issue:` の番号、
    親は `-<2 桁>` で終わらない（`-<2 桁>-<2 桁>` で終われば子の識別子そのもの、
    `-<2 桁>` だけでも子の識別子の途中の `<親>-<フェーズ番号>` と紛れる）。

    見るのは識別子と `issue:` だけ。返すのはユーザに見せる文で、
    深刻度は呼ぶ側が決める（いまは warn）。大文字小文字だけが違う識別子、
    子の形にヒットする親の識別子、
    番号の重なりは、他のチケットと並べて見るので `lint` の側で数える。

    `integration` はその時点の統合先の名前。呼び手が渡したときだけ予約に足す。
    固定のリスト（main など）と同じく大文字小文字を区別せずに比べる。
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
            "と同じ。親のブランチ名が統合先と同じになる"
        )
    elif integration and folded == integration.casefold():
        found.append(
            f"識別子が統合先の名前（{integration}）と同じ。親のブランチ名が統合先と同じになる"
        )
    found.extend(_form_problems(t, serial, prefixes))
    return found


def _form_problems(
    t: ticket_model.Ticket, serial: int = 0, prefixes=settings.DEFAULT_BRANCH_PREFIXES
) -> list[str]:
    """新しい親の識別子が `<先頭の語>-<番号>-<slug>` の形と `issue:` に合っているか。

    - 形（先頭の語は `prefixes` のどれか）に合わなければ言う
    - `issue:`（同じリポジトリの課題）があれば、番号が issue と同じか。プロジェクトの issue なら
      slug の頭が `<プロジェクト名>-` か
    - 別のリポジトリの課題（`owner/repo#N`）は番号を比べない（その番号はこのリポジトリの issue と
      紛れるので、このリポジトリの番号とは重ねない）
    - 長さが勧める上限（`SUGGESTED_ID_LENGTH`）を超えるか
    """
    name = t.ticket
    # 形（先頭の語・番号・slug）を見る。
    matched = form_of(name, prefixes)
    own_issue = t.issue is not None and not t.issue_repo
    if matched is None:
        word = "・".join(prefixes)
        shape = f"`<先頭の語>-<番号>-<slug>` の形（先頭の語は {word} のどれか）"
        if own_issue:
            want = f"feature-{t.issue}-" + (f"{t.project}-" if t.project else "") + "<slug>"
            return [f"親の識別子は {shape} にする。`issue: {t.issue}` から始めるなら `{want}` など"]
        return [
            f"親の識別子は {shape} にする。番号は issue の番号"
            "（`ccnavi-start.sh --issue <番号>` が決める名前にする）"
        ]
    # issue と番号・slug が合うかを見る。
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
    # 長さを見る。
    if len(name) > SUGGESTED_ID_LENGTH:
        found.append(
            f"識別子が {SUGGESTED_ID_LENGTH} 文字を超える（{len(name)} 文字）。"
            "ワークツリーのパスが Windows の上限（MAX_PATH）に近づくので"
            " slug を短くする"
        )
    return found
